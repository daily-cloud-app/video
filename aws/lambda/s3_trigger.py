"""
S3 Event Trigger:
1. When a file is PUT, automatically registers metadata in DynamoDB
2. Generates a thumbnail image (200px) and saves to thumbnails/ prefix
"""
import io
import os
import urllib.parse
from datetime import datetime, timezone

import boto3
from PIL import Image

PHOTOS_TABLE = os.environ.get('PHOTOS_TABLE', '')
THUMBNAIL_SIZE = (200, 200)

dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')


def handler(event, context):
    table = dynamodb.Table(PHOTOS_TABLE)

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

        # Skip empty objects (folder placeholders) and non-image files
        if size == 0:
            continue

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
            # No extension: check S3 object content type or default to jpeg
            try:
                head = s3_client.head_object(Bucket=bucket, Key=key)
                content_type = head.get('ContentType', 'image/jpeg')
            except Exception:
                content_type = 'image/jpeg'
            # Still skip if not an image
            if not content_type.startswith('image/'):
                print(f'Skipping non-image file: {key}')
                continue

        # Generate thumbnail + extract capture date from EXIF
        thumbnail_key = f"thumbnails/{key.removeprefix('users/')}"
        exif_date = None
        try:
            exif_date = _generate_thumbnail_and_get_date(bucket, key, thumbnail_key)
            print(f'Thumbnail generated: {thumbnail_key}')
            if exif_date:
                print(f'EXIF date: {exif_date}')
        except Exception as e:
            print(f'Thumbnail generation failed for {key}: {e}')
            thumbnail_key = None

        # Capture date: EXIF > path date > current time
        created_at = exif_date if exif_date else _extract_date_from_path(key)

        # Check if record already exists (app upload creates record before S3 upload)
        # Try filename_part first (app uses UUID as filename)
        existing = table.get_item(Key={'userId': user_id, 'photoId': filename_part})
        if 'Item' in existing:
            # App-uploaded photo: update existing record
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


def _generate_thumbnail_and_get_date(bucket, source_key, thumbnail_key):
    """Fetch image from S3, generate thumbnail, and return capture date from EXIF"""
    response = s3_client.get_object(Bucket=bucket, Key=source_key)
    image_data = response['Body'].read()

    img = Image.open(io.BytesIO(image_data))

    # Extract capture date from EXIF
    exif_date = None
    try:
        exif = img.getexif()
        if exif:
            # DateTime (306) or DateTimeOriginal (36867)
            date_str = exif.get(306) or exif.get(36867)
            if date_str:
                # "2026:05:05 14:30:00" → ISO format
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

    s3_client.put_object(
        Bucket=bucket,
        Key=thumbnail_key,
        Body=buffer.getvalue(),
        ContentType='image/jpeg',
    )

    return exif_date


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
