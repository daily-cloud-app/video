"""
Cloud Storage Event Trigger (equivalent to AWS S3 trigger):
1. When a file is uploaded (finalized), registers metadata in Firestore
2. Generates a thumbnail image (200px) and saves to thumbnails/ prefix
3. Extracts EXIF capture date from the image

Triggered by: google.cloud.storage.object.v1.finalized
"""
import io
import os
from datetime import datetime, timezone

import functions_framework
from cloudevents.http import CloudEvent
from google.cloud import firestore
from google.cloud import storage as gcs
from PIL import Image

# ── Configuration ──
PHOTOS_BUCKET = os.environ.get('PHOTOS_BUCKET', '')
PHOTOS_COLLECTION = 'photos'
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
    bucket_obj = storage_client.bucket(PHOTOS_BUCKET)
    blob = bucket_obj.blob(key)
    blob.reload()
    if blob.size == 0:
        print(f'Skipping empty object: {key}')
        return

    # Infer content type from extension
    ext = filename_part.rsplit('.', 1)[-1].lower() if '.' in filename_part else ''
    content_type_map = {
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'png': 'image/png',
        'gif': 'image/gif',
        'webp': 'image/webp',
        'heic': 'image/heic',
        'heif': 'image/heic',
    }
    content_type = content_type_map.get(ext, '')
    if not content_type:
        # No extension: check blob content type or default to jpeg
        content_type = blob.content_type or 'image/jpeg'
        if not content_type.startswith('image/'):
            print(f'Skipping non-image file: {key}')
            return

    # Generate thumbnail + extract capture date from EXIF
    thumbnail_key = f"thumbnails/{key.removeprefix('users/')}"
    exif_date = None
    try:
        exif_date = _generate_thumbnail_and_get_date(bucket_name, key, thumbnail_key)
        print(f'Thumbnail generated: {thumbnail_key}')
        if exif_date:
            print(f'EXIF date: {exif_date}')
    except Exception as e:
        print(f'Thumbnail generation failed for {key}: {e}')
        thumbnail_key = None

    # Capture date: EXIF > path date > current time
    created_at = exif_date if exif_date else _extract_date_from_path(key)

    # Check if record already exists (app upload creates record before storage upload)
    doc_id = _doc_id(user_id, filename_part)
    doc_ref = db.collection(PHOTOS_COLLECTION).document(doc_id)
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
        doc_ref = db.collection(PHOTOS_COLLECTION).document(doc_id)
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


def _generate_thumbnail_and_get_date(bucket_name, source_key, thumbnail_key):
    """
    Fetch image from Cloud Storage, generate thumbnail, and return
    capture date from EXIF metadata.
    """
    bucket = storage_client.bucket(bucket_name)

    # Download source image
    source_blob = bucket.blob(source_key)
    image_data = source_blob.download_as_bytes()

    img = Image.open(io.BytesIO(image_data))

    # Extract capture date from EXIF
    exif_date = None
    try:
        exif = img.getexif()
        if exif:
            # DateTime (306) or DateTimeOriginal (36867)
            date_str = exif.get(306) or exif.get(36867)
            if date_str:
                # "2026:05:05 14:30:00" -> ISO format
                dt = datetime.strptime(date_str, '%Y:%m:%d %H:%M:%S')
                exif_date = dt.replace(tzinfo=timezone.utc).isoformat()
    except Exception as e:
        print(f'EXIF extraction failed: {e}')

    # Generate thumbnail
    img.thumbnail(THUMBNAIL_SIZE, Image.LANCZOS)

    buffer = io.BytesIO()
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    img.save(buffer, format='JPEG', quality=80)
    buffer.seek(0)

    # Upload thumbnail to Cloud Storage
    thumbnail_blob = bucket.blob(thumbnail_key)
    thumbnail_blob.upload_from_file(buffer, content_type='image/jpeg')

    return exif_date


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
