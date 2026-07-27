"""
Daily Cloud Photo — Azure Blob Storage Trigger
Generates thumbnails and extracts EXIF data when photos are uploaded.
"""
import io
import json
import os
import logging
from datetime import datetime, timezone

import azure.functions as func
from azure.cosmos import CosmosClient
from azure.storage.blob import BlobServiceClient

logger = logging.getLogger(__name__)

# ── Environment Variables ──
COSMOS_CONNECTION = os.environ.get("COSMOS_CONNECTION", "")
COSMOS_DATABASE = os.environ.get("COSMOS_DATABASE", "dailycloudphoto")
STORAGE_CONNECTION = os.environ.get("STORAGE_CONNECTION", "")
STORAGE_CONTAINER = os.environ.get("STORAGE_CONTAINER", "photos")
THUMBNAIL_MAX_SIZE = int(os.environ.get("THUMBNAIL_MAX_SIZE", "400"))

# ── Event Grid Blob Trigger Handler ──
import azure.functions as func


def _get_cosmos_container(name: str):
    """Get Cosmos DB container client."""
    client = CosmosClient.from_connection_string(COSMOS_CONNECTION)
    db = client.get_database_client(COSMOS_DATABASE)
    return db.get_container_client(name)


def _get_blob_service():
    """Get Azure Blob Storage service client."""
    return BlobServiceClient.from_connection_string(STORAGE_CONNECTION)


def handle_blob_event(event: func.EventGridEvent):
    """
    Process an Event Grid event for blob creation.
    Extracts blob path from event, downloads data, generates thumbnail, updates Cosmos DB.
    """
    # Event Grid の subject からblob パスを取得
    # subject 形式: /blobServices/default/containers/{container}/blobs/{blob_path}
    subject = event.subject or ""
    logger.info(f"Event Grid event: type={event.event_type}, subject={subject}")

    # BlobCreated イベントのみ処理
    if event.event_type != "Microsoft.Storage.BlobCreated":
        logger.info(f"Skipping event type: {event.event_type}")
        return

    # subject から blob パスを抽出
    # /blobServices/default/containers/photos/blobs/users/{uid}/{year}/{month}/{day}/{filename}
    blob_prefix = f"/blobServices/default/containers/{STORAGE_CONTAINER}/blobs/"
    if not subject.startswith(blob_prefix):
        logger.info(f"Skipping: subject doesn't match container: {subject}")
        return

    blob_name = subject[len(blob_prefix):]
    if not blob_name:
        return

    logger.info(f"Processing blob: {blob_name}")

    # Parse the blob path: users/{userId}/{date_path}/{photoId}
    path_parts = blob_name.split("/")

    # Find user ID and photo ID from path
    # Expected: users/{userId}/{year}/{month}/{day}/{photoId}
    try:
        if path_parts[0] != "users":
            logger.info(f"Skipping non-user blob: {blob_name}")
            return

        user_id = path_parts[1]
        filename_part = path_parts[-1]  # UUID or filename

        # Skip thumbnails (avoid infinite loop)
        if "thumbnails/" in blob_name:
            logger.info(f"Skipping thumbnail blob: {blob_name}")
            return

        # Skip non-image files
        ext = filename_part.rsplit('.', 1)[-1].lower() if '.' in filename_part else ''
        image_extensions = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'heif'}
        if ext and ext not in image_extensions:
            logger.info(f"Skipping non-image file: {blob_name}")
            return

    except (IndexError, ValueError) as e:
        logger.error(f"Failed to parse blob path: {blob_name}, error: {e}")
        return

    # Download the blob data
    try:
        blob_service = _get_blob_service()
        container_client = blob_service.get_container_client(STORAGE_CONTAINER)
        blob_client = container_client.get_blob_client(blob_name)
        image_data = blob_client.download_blob().readall()
    except Exception as e:
        logger.error(f"Failed to download blob {blob_name}: {e}")
        return

    if not image_data:
        logger.warning(f"Empty blob: {blob_name}")
        return

    # Extract EXIF data and generate thumbnail
    exif_date = None
    thumbnail_data = None

    try:
        from PIL import Image
        import piexif

        img = Image.open(io.BytesIO(image_data))

        # Extract EXIF date
        try:
            exif_dict = piexif.load(img.info.get("exif", b""))
            if exif_dict.get("Exif"):
                date_original = exif_dict["Exif"].get(piexif.ExifIFD.DateTimeOriginal)
                if date_original:
                    date_str = date_original.decode("utf-8") if isinstance(date_original, bytes) else str(date_original)
                    # Parse EXIF date format: "2025:01:01 12:00:00"
                    exif_date = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone.utc).isoformat()
                    logger.info(f"EXIF date extracted: {exif_date}")
        except Exception as e:
            logger.warning(f"EXIF extraction failed for {blob_name}: {e}")

        # Generate thumbnail
        try:
            # Fix orientation based on EXIF
            try:
                exif_data = piexif.load(img.info.get("exif", b""))
                orientation = exif_data.get("0th", {}).get(piexif.ImageIFD.Orientation, 1)
                if orientation == 3:
                    img = img.rotate(180, expand=True)
                elif orientation == 6:
                    img = img.rotate(270, expand=True)
                elif orientation == 8:
                    img = img.rotate(90, expand=True)
            except Exception:
                pass

            # Resize maintaining aspect ratio
            img.thumbnail((THUMBNAIL_MAX_SIZE, THUMBNAIL_MAX_SIZE), Image.Resampling.LANCZOS)

            # Save thumbnail to bytes
            thumb_buffer = io.BytesIO()
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(thumb_buffer, format="JPEG", quality=80, optimize=True)
            thumbnail_data = thumb_buffer.getvalue()
            logger.info(f"Thumbnail generated: {len(thumbnail_data)} bytes")

        except Exception as e:
            logger.error(f"Thumbnail generation failed for {blob_name}: {e}")

    except ImportError:
        logger.warning("Pillow/piexif not available, skipping image processing")
    except Exception as e:
        logger.error(f"Image processing failed for {blob_name}: {e}")

    # Upload thumbnail to blob storage
    thumbnail_key = None
    if thumbnail_data:
        thumbnail_key = f"thumbnails/{'/'.join(path_parts[1:])}"
        try:
            blob_service = _get_blob_service()
            container_client = blob_service.get_container_client(STORAGE_CONTAINER)
            thumb_blob = container_client.get_blob_client(thumbnail_key)
            thumb_blob.upload_blob(
                thumbnail_data,
                overwrite=True,
                content_type="image/jpeg",
            )
            logger.info(f"Thumbnail uploaded: {thumbnail_key}")
        except Exception as e:
            logger.error(f"Failed to upload thumbnail: {e}")
            thumbnail_key = None

    # Update Cosmos DB record
    try:
        container = _get_cosmos_container("photos")

        # Check if app-uploaded record exists (uses filename_part as photoId)
        query = "SELECT * FROM c WHERE c.userId = @userId AND c.id = @photoId"
        params = [
            {"name": "@userId", "value": user_id},
            {"name": "@photoId", "value": filename_part},
        ]
        items = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))

        if items:
            # App-uploaded: update existing record
            item = items[0]
            update_fields = {"status": "uploaded", "size": len(image_data)}

            if thumbnail_key:
                update_fields["thumbnailKey"] = thumbnail_key
            if exif_date:
                update_fields["exifDate"] = exif_date
                if not item.get("createdAt") or item.get("status") == "uploading":
                    update_fields["createdAt"] = exif_date

            item.update(update_fields)
            container.upsert_item(body=item)
            logger.info(f"Cosmos DB updated for photo {filename_part}")
        else:
            # Direct upload: use path-based ID (sanitize for Cosmos DB)
            raw_id = '/'.join(path_parts[2:])
            # Cosmos DB の id にスラッシュ等の不正文字が使えないためハッシュ化
            import hashlib
            photo_id = hashlib.sha256(raw_id.encode()).hexdigest()[:32]
            blob_key = "/".join(path_parts)
            photo_doc = {
                "id": photo_id,
                "userId": user_id,
                "filename": filename_part,
                "contentType": "image/jpeg",
                "blobKey": blob_key,
                "status": "uploaded",
                "createdAt": exif_date or _extract_date_from_path(blob_key),
                "labels": [],
                "size": len(image_data),
                "uploadedViaShare": True,
            }
            if thumbnail_key:
                photo_doc["thumbnailKey"] = thumbnail_key
            container.upsert_item(body=photo_doc)
            logger.info(f"New photo record created for {photo_id}")

    except Exception as e:
        logger.error(f"Failed to update Cosmos DB for {photo_id}: {e}")


def _extract_date_from_path(key):
    """Extract date from blob path (users/{uid}/YYYY/MM/DD/{filename}).
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
