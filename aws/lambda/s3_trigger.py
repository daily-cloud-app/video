"""
S3 Event Trigger:
1. When a video file is PUT, automatically registers metadata in DynamoDB
2. Generates a thumbnail image (frame extraction at 1s) using ffmpeg and saves to thumbnails/ prefix
"""
import os
import subprocess
import tempfile
import urllib.parse
from datetime import datetime, timezone

import boto3

VIDEOS_TABLE = os.environ.get('VIDEOS_TABLE', '')
THUMBNAIL_SIZE = (200, 200)

dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')


def handler(event, context):
    table = dynamodb.Table(VIDEOS_TABLE)

    for record in event.get('Records', []):
        bucket = record['s3']['bucket']['name']
        key = urllib.parse.unquote_plus(record['s3']['object']['key'])
        size = record['s3']['object'].get('size', 0)

        # Ignore thumbnails/ prefix files (prevent infinite loop)
        if key.startswith('thumbnails/'):
            continue

        # Extract userId from path
        parts = key.split('/')
        if len(parts) < 3 or parts[0] != 'users':
            continue

        user_id = parts[1]
        filename_part = parts[-1]  # UUID or filename

        # Skip empty objects (folder placeholders) and non-video files
        if size == 0:
            continue

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
            # No extension: check S3 object content type or default to mp4
            try:
                head = s3_client.head_object(Bucket=bucket, Key=key)
                content_type = head.get('ContentType', 'video/mp4')
            except Exception:
                content_type = 'video/mp4'
            # Still skip if not a video
            if not content_type.startswith('video/'):
                print(f'Skipping non-video file: {key}')
                continue

        # Generate thumbnail from video frame
        thumbnail_key = f"thumbnails/{key.removeprefix('users/')}"
        try:
            _generate_thumbnail_from_video(bucket, key, thumbnail_key)
            print(f'Thumbnail generated: {thumbnail_key}')
        except Exception as e:
            print(f'Thumbnail generation failed for {key}: {e}')
            thumbnail_key = None

        # Capture date: path date > current time (videos don't have EXIF like images)
        created_at = _extract_date_from_path(key)

        # Check if record already exists (app upload creates record before S3 upload)
        # Try filename_part first (app uses UUID as filename)
        existing = table.get_item(Key={'userId': user_id, 'photoId': filename_part})
        if 'Item' in existing:
            # App-uploaded video: update existing record
            photo_id = filename_part
            update_expr = 'SET #s = :status, #sz = :size'
            expr_names = {'#s': 'status', '#sz': 'size'}
            expr_values = {':status': 'uploaded', ':size': size}
            if thumbnail_key:
                update_expr += ', thumbnailKey = :tk'
                expr_values[':tk'] = thumbnail_key
            table.update_item(
                Key={'userId': user_id, 'photoId': photo_id},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
            )
        else:
            # Direct S3 upload: use full path as photoId to avoid same-name collisions
            photo_id = '/'.join(parts[2:])
            item = {
                'userId': user_id,
                'photoId': photo_id,
                'filename': filename_part,
                'contentType': content_type,
                's3Key': key,
                'size': size,
                'status': 'uploaded',
                'createdAt': created_at,
                'labels': [],
            }
            if thumbnail_key:
                item['thumbnailKey'] = thumbnail_key
            table.put_item(Item=item)

        print(f'Processed: {key} for user {user_id}')


def _generate_thumbnail_from_video(bucket, source_key, thumbnail_key):
    """Download video from S3, extract a frame at 1 second mark using ffmpeg, upload as JPEG thumbnail."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        video_path = os.path.join(tmp_dir, 'input_video')
        thumb_path = os.path.join(tmp_dir, 'thumbnail.jpg')

        # Download video from S3
        s3_client.download_file(bucket, source_key, video_path)

        # Extract frame at 1 second using ffmpeg
        # Use /opt/bin/ffmpeg (Lambda layer path)
        ffmpeg_path = '/opt/bin/ffmpeg'
        if not os.path.exists(ffmpeg_path):
            ffmpeg_path = 'ffmpeg'  # fallback to PATH

        cmd = [
            ffmpeg_path,
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

        # Upload thumbnail to S3
        with open(thumb_path, 'rb') as f:
            s3_client.put_object(
                Bucket=bucket,
                Key=thumbnail_key,
                Body=f.read(),
                ContentType='image/jpeg',
            )


def _extract_date_from_path(key):
    """Extract date from S3 key path (users/{uid}/YYYY/MM/DD/{filename}).
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
