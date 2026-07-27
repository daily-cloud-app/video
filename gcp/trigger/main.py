"""
Cloud Storage Event Trigger (equivalent to AWS S3 trigger):
1. When a video file is uploaded (finalized), registers metadata in Firestore
2. Generates a thumbnail (frame extraction at 1s) using ffmpeg and saves to thumbnails/ prefix

Triggered by: google.cloud.storage.object.v1.finalized
"""
import os
import subprocess
import tempfile
from datetime import datetime, timezone

import functions_framework
from cloudevents.http import CloudEvent
from google.cloud import firestore
from google.cloud import storage as gcs

# ── Configuration ──
VIDEOS_BUCKET = os.environ.get('VIDEOS_BUCKET', '')
VIDEOS_COLLECTION = 'videos'
THUMBNAIL_SIZE = (200, 200)

# ── GCP Clients ──
db = firestore.Client()
storage_client = gcs.Client()


def _doc_id(user_id, photo_id):
    """Generate Firestore document ID from userId and photoId."""
    safe_photo_id = photo_id.replace('/', '_')
    return f'{user_id}_{safe_photo_id}'


@functions_framework.cloud_event
def storage_trigger_handler(cloud_event: CloudEvent):
    """
    Cloud Storage finalize event handler.
    Processes uploaded photos: generates thumbnails and registers metadata.
    """
    data = cloud_event.data
    bucket_name = data.get('bucket', '')
    key = data.get('name', '')
    size = int(data.get('size', 0))

    if not key:
        print('No object key in event, skipping')
        return

    # Ignore thumbnails/ prefix files (prevent infinite loop)
    if key.startswith('thumbnails/'):
        print(f'Skipping thumbnail: {key}')
        return

    # Extract userId from path: users/{userId}/.../{photoId}
    parts = key.split('/')
    if len(parts) < 3 or parts[0] != 'users':
        print(f'Skipping non-user file: {key}')
        return

    user_id = parts[1]
    filename_part = parts[-1]  # UUID or filename

    print(f'Processing: {key} for user {user_id}, filename {filename_part}')

    # Skip empty objects (folder placeholders) and non-image files
    # Check size from Cloud Storage metadata
    bucket_obj = storage_client.bucket(VIDEOS_BUCKET)
    blob = bucket_obj.blob(key)
    blob.reload()
    if blob.size == 0:
        print(f'Skipping empty object: {key}')
        return

    # Infer content type from extension
    ext = filename_part.rsplit('.', 1)[-1].lower() if '.' in filename_part else ''
    content_type_map = {
        'mp4': 'video/mp4',
        'mov': 'video/quicktime',
        'avi': 'video/x-msvideo',
        'mkv': 'video/x-matroska',
        'webm': 'video/webm',
        '3gp': 'video/3gpp',
    }
    content_type = content_type_map.get(ext, '')
    if not content_type:
        # No extension: check blob content type or default to jpeg
        content_type = blob.content_type or 'video/mp4'
        if not content_type.startswith('video/'):
            print(f'Skipping non-video file: {key}')
            return

    # Generate thumbnail from video frame
    thumbnail_key = f"thumbnails/{key.removeprefix('users/')}"
    try:
        _generate_thumbnail_from_video(bucket_name, key, thumbnail_key)
        print(f'Thumbnail generated: {thumbnail_key}')
    except Exception as e:
        print(f'Thumbnail generation failed for {key}: {e}')
        thumbnail_key = None

    # Capture date: path date > current time (videos don't have EXIF)
    created_at = _extract_date_from_path(key)

    # Check if record already exists (app upload creates record before storage upload)
    doc_id = _doc_id(user_id, filename_part)
    doc_ref = db.collection(VIDEOS_COLLECTION).document(doc_id)
    doc = doc_ref.get()

    if doc.exists:
        # App-uploaded photo: update existing record
        update_data = {
            'status': 'uploaded',
            'size': blob.size,
        }
        if thumbnail_key:
            update_data['thumbnailKey'] = thumbnail_key
        doc_ref.update(update_data)
        print(f'Updated existing document: {doc_id}')
    else:
        # Direct upload: use full path as photoId to avoid same-name collisions
        photo_id = '/'.join(parts[2:])
        doc_id = _doc_id(user_id, photo_id)
        doc_ref = db.collection(VIDEOS_COLLECTION).document(doc_id)
        item = {
            'userId': user_id,
            'photoId': photo_id,
            'filename': filename_part,
            'contentType': content_type,
            'gcsKey': key,
            'size': size,
            'status': 'uploaded',
            'createdAt': created_at,
            'labels': [],
        }
        if thumbnail_key:
            item['thumbnailKey'] = thumbnail_key
        doc_ref.set(item)
        print(f'Created new document: {doc_id}')

    print(f'Processed: {key} for user {user_id}')


def _generate_thumbnail_from_video(bucket_name, source_key, thumbnail_key):
    """
    Download video from Cloud Storage, extract a frame at 1 second mark
    using ffmpeg, and upload as JPEG thumbnail.
    """
    bucket = storage_client.bucket(bucket_name)

    with tempfile.TemporaryDirectory() as tmp_dir:
        video_path = os.path.join(tmp_dir, 'input_video')
        thumb_path = os.path.join(tmp_dir, 'thumbnail.jpg')

        # Download source video
        source_blob = bucket.blob(source_key)
        source_blob.download_to_filename(video_path)

        # Extract frame at 1 second using ffmpeg
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-ss', '1',
            '-vframes', '1',
            '-vf', 'scale=200:-1',
            '-f', 'image2',
            '-y',
            thumb_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)

        if result.returncode != 0:
            # If 1s fails (video shorter than 1s), try 0s
            cmd[cmd.index('1')] = '0'
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode != 0:
                raise RuntimeError(f'ffmpeg failed: {result.stderr.decode()[:500]}')

        # Upload thumbnail to Cloud Storage
        thumbnail_blob = bucket.blob(thumbnail_key)
        thumbnail_blob.upload_from_filename(thumb_path, content_type='image/jpeg')

    return None


def _extract_date_from_path(key):
    """Extract date from storage path (users/{uid}/YYYY/MM/DD/{filename}).
    Returns ISO format string, or current time if path doesn't contain a valid date."""
    import re
    match = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', key)
    if match:
        try:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            dt = datetime(year, month, day, tzinfo=timezone.utc)
            return dt.isoformat()
        except (ValueError, OverflowError):
            pass
    return datetime.now(timezone.utc).isoformat()
