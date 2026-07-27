"""
Daily Cloud Photo — Azure Functions Backend (v2 Programming Model)
All API endpoints with custom JWT auth, Cosmos DB, and Azure Blob Storage.
"""
import json
import os
import uuid
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from functools import wraps

import azure.functions as func
import jwt
import bcrypt
from azure.cosmos import CosmosClient, PartitionKey, exceptions as cosmos_exceptions
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions

# ── Azure Functions App ──
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# ── Environment Variables ──
COSMOS_CONNECTION = os.environ.get("COSMOS_CONNECTION", "")
COSMOS_DATABASE = os.environ.get("COSMOS_DATABASE", "dailycloudphoto")
STORAGE_CONNECTION = os.environ.get("STORAGE_CONNECTION", "")
STORAGE_CONTAINER = os.environ.get("STORAGE_CONTAINER", "photos")
JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "30"))
REQUIRE_EMAIL = os.environ.get("REQUIRE_EMAIL", "true").lower() == "true"
REQUIRE_PHONE = os.environ.get("REQUIRE_PHONE", "false").lower() == "true"
ENABLE_SHARE_URL = os.environ.get("ENABLE_SHARE_URL", "true").lower() == "true"
ENABLE_SHARE_DOWNLOAD_URL = os.environ.get("ENABLE_SHARE_DOWNLOAD_URL", "true").lower() == "true"
ENABLE_LABEL_SHARING = os.environ.get("ENABLE_LABEL_SHARING", "true").lower() == "true"
APP_DISPLAY_NAME = os.environ.get("APP_DISPLAY_NAME", "Daily Cloud Photo Backend")
FUNCTION_APP_URL = os.environ.get("FUNCTION_APP_URL", "")

logger = logging.getLogger(__name__)


# ============================================================
# Database & Storage Initialization
# ============================================================

def _get_cosmos_client():
    """Get Cosmos DB client (lazy initialization)."""
    return CosmosClient.from_connection_string(COSMOS_CONNECTION)


def _get_database():
    """Get Cosmos DB database."""
    client = _get_cosmos_client()
    return client.get_database_client(COSMOS_DATABASE)


def _get_container(name: str):
    """Get Cosmos DB container."""
    db = _get_database()
    return db.get_container_client(name)


def _get_blob_service():
    """Get Azure Blob Storage service client."""
    return BlobServiceClient.from_connection_string(STORAGE_CONNECTION)


def _get_container_client():
    """Get the photos blob container client."""
    service = _get_blob_service()
    return service.get_container_client(STORAGE_CONTAINER)


# ============================================================
# JWT Auth Helpers
# ============================================================

def _create_access_token(user_id: str, username: str) -> str:
    """Create a JWT access token."""
    payload = {
        "sub": user_id,
        "username": username,
        "type": "access",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _create_refresh_token(user_id: str, username: str) -> str:
    """Create a JWT refresh token."""
    payload = {
        "sub": user_id,
        "username": username,
        "type": "refresh",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _verify_token(token: str, token_type: str = "access") -> dict | None:
    """Verify and decode a JWT token. Returns payload or None."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != token_type:
            return None
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def _get_user_from_request(req: func.HttpRequest) -> dict | None:
    """Extract user info from Authorization header."""
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    return _verify_token(token, "access")


def _hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# ============================================================
# Response Helpers
# ============================================================

def _ok(status: int, body: dict) -> func.HttpResponse:
    """Return a JSON response."""
    return func.HttpResponse(
        body=json.dumps(body, default=str),
        status_code=status,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


def _err(status: int, message: str, code: str = None) -> func.HttpResponse:
    """Return an error JSON response."""
    body = {"message": message}
    if code:
        body["error"] = code
    return _ok(status, body)


def _body(req: func.HttpRequest) -> dict:
    """Parse JSON body from request."""
    try:
        return req.get_json()
    except (ValueError, TypeError):
        return {}


# ============================================================
# Blob Storage Helpers
# ============================================================

def _generate_sas_url(blob_name: str, permission: str = "r", expiry_hours: int = 1) -> str:
    """Generate a SAS URL for a blob."""
    service = _get_blob_service()
    account_name = service.account_name
    account_key = service.credential.account_key

    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=STORAGE_CONTAINER,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=(permission == "r"), write=(permission == "w"), create=(permission == "w")),
        expiry=datetime.now(timezone.utc) + timedelta(hours=expiry_hours),
        content_type="image/jpeg" if permission == "w" else None,
    )
    return f"https://{account_name}.blob.core.windows.net/{STORAGE_CONTAINER}/{blob_name}?{sas_token}"


def _generate_upload_sas(blob_name: str, content_type: str = "image/jpeg") -> str:
    """Generate a SAS URL for uploading a blob."""
    service = _get_blob_service()
    account_name = service.account_name
    account_key = service.credential.account_key

    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=STORAGE_CONTAINER,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(write=True, create=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        content_type=content_type,
    )
    return f"https://{account_name}.blob.core.windows.net/{STORAGE_CONTAINER}/{blob_name}?{sas_token}"


# ============================================================
# CORS Preflight Handler
# ============================================================

@app.route(route="{*path}", methods=["OPTIONS"])
def cors_preflight(req: func.HttpRequest) -> func.HttpResponse:
    """Handle CORS preflight requests."""
    return func.HttpResponse(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Max-Age": "86400",
        },
    )


# ============================================================
# GET /info
# ============================================================

@app.route(route="info", methods=["GET"])
def get_info(req: func.HttpRequest) -> func.HttpResponse:
    """Return server configuration."""
    fields = ["username", "password"]
    if REQUIRE_EMAIL:
        fields.append("email")
    if REQUIRE_PHONE:
        fields.append("phone")

    features = []
    if ENABLE_SHARE_URL:
        features.append("share-upload-url")
    if ENABLE_SHARE_DOWNLOAD_URL:
        features.append("share-download-url")
    if ENABLE_LABEL_SHARING:
        features.append("label-sharing")

    return _ok(200, {
        "name": APP_DISPLAY_NAME,
        "version": "1.0.0",
        "signupFields": fields,
        "features": features,
    })


# ============================================================
# POST /auth/signup
# ============================================================

@app.route(route="auth/signup", methods=["POST"])
def auth_signup(req: func.HttpRequest) -> func.HttpResponse:
    """Register a new user."""
    b = _body(req)
    username = b.get("username", "").strip()
    password = b.get("password", "")
    email = b.get("email", "").strip()
    phone = b.get("phone", "").strip()

    if not username or not password:
        return _err(400, "username and password are required")

    if len(password) < 8:
        return _err(400, "Password must be at least 8 characters", "InvalidPassword")

    if REQUIRE_EMAIL and not email:
        return _err(400, "email is required")

    if REQUIRE_PHONE and not phone:
        return _err(400, "phone is required")

    container = _get_container("users")
    user_id = str(uuid.uuid4())

    # Check if username already exists
    query = "SELECT * FROM c WHERE c.username = @username"
    params = [{"name": "@username", "value": username}]
    existing = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))
    if existing:
        return _err(409, "Username already exists", "UsernameExists")

    # Create user document
    user_doc = {
        "id": user_id,
        "userId": user_id,
        "username": username,
        "passwordHash": _hash_password(password),
        "email": email,
        "phone": phone,
        "confirmed": True,  # Auto-confirm for simplicity (no email service)
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }

    try:
        container.create_item(body=user_doc)
    except cosmos_exceptions.CosmosResourceExistsError:
        return _err(409, "Username already exists", "UsernameExists")

    return _ok(201, {
        "message": "User created. Confirmation may be required.",
        "confirmationRequired": False,
    })


# ============================================================
# POST /auth/confirm
# ============================================================

@app.route(route="auth/confirm", methods=["POST"])
def auth_confirm(req: func.HttpRequest) -> func.HttpResponse:
    """Confirm signup (auto-confirmed in this implementation)."""
    b = _body(req)
    username = b.get("username", "").strip()
    code = b.get("confirmationCode", "").strip()

    if not username or not code:
        return _err(400, "username and confirmationCode are required")

    # In this implementation users are auto-confirmed
    return _ok(200, {"message": "User confirmed."})


# ============================================================
# POST /auth/signin
# ============================================================

@app.route(route="auth/signin", methods=["POST"])
def auth_signin(req: func.HttpRequest) -> func.HttpResponse:
    """Sign in and receive tokens."""
    b = _body(req)
    username = b.get("username", "").strip()
    password = b.get("password", "")

    if not username or not password:
        return _err(400, "username and password are required")

    container = _get_container("users")
    query = "SELECT * FROM c WHERE c.username = @username"
    params = [{"name": "@username", "value": username}]
    users = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))

    if not users:
        return _err(401, "Incorrect username or password", "NotAuthorized")

    user = users[0]

    if not _verify_password(password, user["passwordHash"]):
        return _err(401, "Incorrect username or password", "NotAuthorized")

    if not user.get("confirmed", False):
        return _err(403, "User is not confirmed", "UserNotConfirmed")

    access_token = _create_access_token(user["id"], user["username"])
    refresh_token = _create_refresh_token(user["id"], user["username"])

    return _ok(200, {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresIn": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    })


# ============================================================
# POST /auth/refresh
# ============================================================

@app.route(route="auth/refresh", methods=["POST"])
def auth_refresh(req: func.HttpRequest) -> func.HttpResponse:
    """Refresh an expired access token."""
    b = _body(req)
    rt = b.get("refreshToken", "")

    if not rt:
        return _err(400, "refreshToken is required")

    payload = _verify_token(rt, "refresh")
    if not payload:
        return _err(401, "Refresh token is invalid or expired", "NotAuthorized")

    access_token = _create_access_token(payload["sub"], payload["username"])

    return _ok(200, {
        "accessToken": access_token,
        "expiresIn": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    })


# ============================================================
# POST /auth/forgot-password
# ============================================================

@app.route(route="auth/forgot-password", methods=["POST"])
def auth_forgot_password(req: func.HttpRequest) -> func.HttpResponse:
    """Request a password reset code."""
    b = _body(req)
    username = b.get("username", "").strip()

    if not username:
        return _err(400, "username is required")

    container = _get_container("users")
    query = "SELECT * FROM c WHERE c.username = @username"
    params = [{"name": "@username", "value": username}]
    users = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))

    if users:
        # Generate a reset code and store it
        reset_code = str(uuid.uuid4())[:6].upper()
        user = users[0]
        user["resetCode"] = reset_code
        user["resetCodeExpiry"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        container.upsert_item(body=user)
        logger.info(f"Reset code for {username}: {reset_code}")

    # Always return success for security (don't reveal if user exists)
    return _ok(200, {"message": "Confirmation code sent."})


# ============================================================
# POST /auth/reset-password
# ============================================================

@app.route(route="auth/reset-password", methods=["POST"])
def auth_reset_password(req: func.HttpRequest) -> func.HttpResponse:
    """Reset password with confirmation code."""
    b = _body(req)
    username = b.get("username", "").strip()
    code = b.get("confirmationCode", "").strip()
    new_password = b.get("newPassword", "")

    if not username or not code or not new_password:
        return _err(400, "username, confirmationCode, and newPassword are required")

    if len(new_password) < 8:
        return _err(400, "Password must be at least 8 characters", "InvalidPassword")

    container = _get_container("users")
    query = "SELECT * FROM c WHERE c.username = @username"
    params = [{"name": "@username", "value": username}]
    users = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))

    if not users:
        return _err(404, "User not found", "UserNotFound")

    user = users[0]
    stored_code = user.get("resetCode", "")
    expiry = user.get("resetCodeExpiry", "")

    if not stored_code or stored_code != code:
        return _err(400, "Invalid confirmation code", "CodeMismatch")

    # Check expiry
    try:
        expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expiry_dt:
            return _err(400, "Confirmation code expired", "ExpiredCode")
    except Exception:
        return _err(400, "Confirmation code expired", "ExpiredCode")

    # Update password and clear reset code
    user["passwordHash"] = _hash_password(new_password)
    user.pop("resetCode", None)
    user.pop("resetCodeExpiry", None)
    container.upsert_item(body=user)

    return _ok(200, {"message": "Password reset successful."})


# ============================================================
# GET /photos
# ============================================================

@app.route(route="photos", methods=["GET"])
def photos_list(req: func.HttpRequest) -> func.HttpResponse:
    """List photos for the authenticated user."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    limit = int(req.params.get("limit", "100"))
    cursor = req.params.get("cursor", None)

    container = _get_container("photos")

    # Query user's photos
    query = "SELECT * FROM c WHERE c.userId = @uid AND c.status != 'deleted' AND c.status != 'uploading' AND NOT STARTSWITH(c.id, 'share_token:') AND NOT STARTSWITH(c.id, 'share:') AND NOT STARTSWITH(c.id, 'sent_share:') ORDER BY c.createdAt DESC"
    params = [{"name": "@uid", "value": uid}]

    items = list(container.query_items(
        query=query,
        parameters=params,
        enable_cross_partition_query=True,
        max_item_count=limit,
    ))

    photos = []
    for item in items:
        blob_key = item.get("blobKey", "")
        thumbnail_key = item.get("thumbnailKey", "")

        full_url = _generate_sas_url(blob_key) if blob_key else None
        thumbnail_url = _generate_sas_url(thumbnail_key) if thumbnail_key else full_url

        photos.append({
            "id": item["id"],
            "filename": item.get("filename", ""),
            "contentType": item.get("contentType", "image/jpeg"),
            "size": int(item.get("size", 0)),
            "createdAt": item.get("createdAt", ""),
            "thumbnailUrl": thumbnail_url,
            "fullUrl": full_url,
            "labels": item.get("labels", []),
            "labelNames": item.get("labelNames", {}),
            "shared": False,
            "sharedFrom": "",
        })

    # Include shared photos
    if ENABLE_LABEL_SHARING:
        shares_query = "SELECT * FROM c WHERE c.userId = @uid AND STARTSWITH(c.id, 'share:') AND c.status = 'accepted'"
        shares = list(container.query_items(
            query=shares_query,
            parameters=params,
            enable_cross_partition_query=True,
        ))

        for share_item in shares:
            from_uid = share_item.get("fromUser", "")
            label_id = share_item.get("labelId", "")
            if not from_uid or not label_id:
                continue

            # Get photos from sharing user with the specified label
            shared_photos_query = "SELECT * FROM c WHERE c.userId = @fromUid AND c.status != 'deleted' AND ARRAY_CONTAINS(c.labels, @labelId)"
            shared_params = [
                {"name": "@fromUid", "value": from_uid},
                {"name": "@labelId", "value": label_id},
            ]
            shared_photos = list(container.query_items(
                query=shared_photos_query,
                parameters=shared_params,
                enable_cross_partition_query=True,
            ))

            for sp in shared_photos:
                if sp.get("id", "").startswith("share_token:") or sp.get("id", "").startswith("share:") or sp.get("id", "").startswith("sent_share:"):
                    continue
                sp_blob_key = sp.get("blobKey", "")
                sp_thumb_key = sp.get("thumbnailKey", "")

                photos.append({
                    "id": sp["id"],
                    "filename": sp.get("filename", ""),
                    "contentType": sp.get("contentType", "image/jpeg"),
                    "size": int(sp.get("size", 0)),
                    "createdAt": sp.get("createdAt", ""),
                    "thumbnailUrl": _generate_sas_url(sp_thumb_key) if sp_thumb_key else (_generate_sas_url(sp_blob_key) if sp_blob_key else None),
                    "fullUrl": _generate_sas_url(sp_blob_key) if sp_blob_key else None,
                    "labels": sp.get("labels", []),
                    "shared": True,
                    "sharedFrom": share_item.get("fromUsername", ""),
                })

    # Simple cursor-based pagination
    next_cursor = None
    if len(photos) > limit:
        photos = photos[:limit]
        next_cursor = photos[-1]["id"] if photos else None

    return _ok(200, {"photos": photos, "nextCursor": next_cursor})


# ============================================================
# GET /photos/{id}
# ============================================================

@app.route(route="photos/{photo_id}", methods=["GET"])
def photos_get_one(req: func.HttpRequest) -> func.HttpResponse:
    """Get a single photo's metadata."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    photo_id = req.route_params.get("photo_id", "")
    if not photo_id:
        return _err(400, "photoId is required")

    # Avoid matching sub-routes
    if photo_id in ("upload-url", "share-upload-url", "share-upload"):
        return _err(404, "Not found")

    container = _get_container("photos")
    try:
        item = container.read_item(item=photo_id, partition_key=uid)
    except cosmos_exceptions.CosmosResourceNotFoundError:
        return _err(404, "Photo not found")

    blob_key = item.get("blobKey", "")
    thumbnail_key = item.get("thumbnailKey", "")
    full_url = _generate_sas_url(blob_key) if blob_key else None
    thumbnail_url = _generate_sas_url(thumbnail_key) if thumbnail_key else full_url

    return _ok(200, {
        "id": photo_id,
        "filename": item.get("filename", ""),
        "contentType": item.get("contentType", "image/jpeg"),
        "size": int(item.get("size", 0)),
        "createdAt": item.get("createdAt", ""),
        "fullUrl": full_url,
        "thumbnailUrl": thumbnail_url,
    })


# ============================================================
# POST /photos/upload-url
# ============================================================

@app.route(route="photos/upload-url", methods=["POST"])
def photos_upload_url(req: func.HttpRequest) -> func.HttpResponse:
    """Get a presigned URL for uploading a photo."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    b = _body(req)
    filename = b.get("filename", "")
    content_type = b.get("contentType", "image/jpeg")
    created_at = b.get("createdAt", datetime.now(timezone.utc).isoformat())
    photo_id = b.get("photoId", "") or str(uuid.uuid4())

    if not filename:
        return _err(400, "filename is required")

    # Build blob path: users/{uid}/YYYY/MM/DD/{photoId}
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now(timezone.utc)
    date_path = f"{dt.year}/{dt.month:02d}/{dt.day:02d}"
    blob_key = f"users/{uid}/{date_path}/{photo_id}"

    upload_url = _generate_upload_sas(blob_key, content_type)

    # Store photo metadata in Cosmos DB
    container = _get_container("photos")
    photo_doc = {
        "id": photo_id,
        "userId": uid,
        "filename": filename,
        "contentType": content_type,
        "blobKey": blob_key,
        "status": "uploading",
        "createdAt": created_at,
        "labels": [],
        "size": 0,
    }

    try:
        container.upsert_item(body=photo_doc)
    except Exception as e:
        logger.error(f"Failed to save photo metadata: {e}")
        return _err(500, "Failed to create photo record")

    return _ok(200, {
        "photoId": photo_id,
        "uploadUrl": upload_url,
        "headers": {"x-ms-blob-type": "BlockBlob", "Content-Type": content_type},
        "expiresIn": 3600,
    })


# ============================================================
# POST /photos/{id}/confirm
# ============================================================

@app.route(route="photos/{photo_id}/confirm", methods=["POST"])
def photos_confirm(req: func.HttpRequest) -> func.HttpResponse:
    """Confirm that upload to presigned URL is complete."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    photo_id = req.route_params.get("photo_id", "")
    if not photo_id:
        return _err(400, "photoId is required")

    container = _get_container("photos")
    try:
        item = container.read_item(item=photo_id, partition_key=uid)
    except cosmos_exceptions.CosmosResourceNotFoundError:
        return _err(404, "Photo not found")

    # Verify blob exists
    blob_key = item.get("blobKey", "")
    try:
        blob_client = _get_container_client().get_blob_client(blob_key)
        properties = blob_client.get_blob_properties()
        size = properties.size
    except Exception:
        return _err(404, "File not found in storage")

    # Update status
    item["status"] = "uploaded"
    item["size"] = size
    container.upsert_item(body=item)

    thumbnail_key = item.get("thumbnailKey", blob_key)
    thumbnail_url = _generate_sas_url(thumbnail_key)

    return _ok(200, {"message": "Upload confirmed.", "thumbnailUrl": thumbnail_url})


# ============================================================
# PUT /photos/{id}/labels
# ============================================================

@app.route(route="photos/{photo_id}/labels", methods=["PUT"])
def photos_update_labels(req: func.HttpRequest) -> func.HttpResponse:
    """Update labels for a photo."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    photo_id = req.route_params.get("photo_id", "")
    if not photo_id:
        return _err(400, "photoId is required")

    b = _body(req)
    labels = b.get("labels", [])
    label_names = b.get("labelNames", {})
    if not isinstance(labels, list):
        return _err(400, "labels must be an array")

    container = _get_container("photos")
    try:
        item = container.read_item(item=photo_id, partition_key=uid)
    except cosmos_exceptions.CosmosResourceNotFoundError:
        return _err(404, "Photo not found")

    item["labels"] = labels
    if label_names and isinstance(label_names, dict):
        item["labelNames"] = label_names
    container.upsert_item(body=item)

    return _ok(200, {"message": "Labels updated.", "labels": labels})


# ============================================================
# DELETE /photos/{id}
# ============================================================

@app.route(route="photos/{photo_id}", methods=["DELETE"])
def photos_delete(req: func.HttpRequest) -> func.HttpResponse:
    """Soft-delete a photo."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    photo_id = req.route_params.get("photo_id", "")
    if not photo_id:
        return _err(400, "photoId is required")

    container = _get_container("photos")
    try:
        item = container.read_item(item=photo_id, partition_key=uid)
    except cosmos_exceptions.CosmosResourceNotFoundError:
        return _err(404, "Photo not found")

    # Soft delete
    item["status"] = "deleted"
    item["deletedAt"] = datetime.now(timezone.utc).isoformat()
    container.upsert_item(body=item)

    return _ok(200, {"message": "Photo deleted."})


# ============================================================
# POST /photos/share-upload-url
# ============================================================

@app.route(route="photos/share-upload-url", methods=["POST"])
def photos_share_upload_url(req: func.HttpRequest) -> func.HttpResponse:
    """Generate a temporary upload page URL for third parties."""
    if not ENABLE_SHARE_URL:
        return _err(403, "Share URL feature is disabled")

    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    b = _body(req)
    expires_hours = int(b.get("expiresHours", 24))
    label_id = b.get("labelId", "") or ""
    label_name = b.get("labelName", "") or ""

    token = str(uuid.uuid4())

    # Save token to Cosmos DB
    container = _get_container("photos")
    token_doc = {
        "id": f"share_token:{token}",
        "userId": uid,
        "status": "active",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "expiresHours": expires_hours,
        "labels": [],
    }
    if label_id:
        token_doc["labelId"] = label_id
        token_doc["labelName"] = label_name
    container.upsert_item(body=token_doc)

    # Build upload page URL
    base_url = FUNCTION_APP_URL or f"https://{req.headers.get('Host', 'localhost')}"
    page_url = f"{base_url}/v1/upload-page?token={token}"

    return _ok(200, {
        "shareUrl": page_url,
        "token": token,
        "expiresHours": expires_hours,
    })


# ============================================================
# GET /upload-page?token=xxx
# ============================================================

@app.route(route="upload-page", methods=["GET"])
def upload_page(req: func.HttpRequest) -> func.HttpResponse:
    """Returns an HTML upload page."""
    if not ENABLE_SHARE_URL:
        return func.HttpResponse(
            body="<h1>This feature is disabled.</h1>",
            status_code=403,
            mimetype="text/html",
        )

    token = req.params.get("token", "")
    if not token:
        return func.HttpResponse(
            body="<h1>Invalid link</h1>",
            status_code=400,
            mimetype="text/html",
        )

    # Validate token
    container = _get_container("photos")
    try:
        # Query for the share token across partitions
        query = "SELECT * FROM c WHERE c.id = @tokenId AND c.status = 'active'"
        params = [{"name": "@tokenId", "value": f"share_token:{token}"}]
        items = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))
    except Exception:
        items = []

    if not items:
        return func.HttpResponse(
            body="<h1>This link has expired or is invalid.</h1>",
            status_code=403,
            mimetype="text/html",
        )

    item = items[0]

    # Expiration check
    created_at = item.get("createdAt", "")
    expires_hours = int(item.get("expiresHours", 24))
    try:
        created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > created_dt + timedelta(hours=expires_hours):
            return func.HttpResponse(
                body="<h1>This link has expired.</h1>",
                status_code=403,
                mimetype="text/html",
            )
    except Exception:
        pass

    base_url = FUNCTION_APP_URL or f"https://{req.headers.get('Host', 'localhost')}"
    api_base = f"{base_url}/v1"

    html = _build_upload_page_html(token, api_base)

    return func.HttpResponse(
        body=html,
        status_code=200,
        mimetype="text/html",
        charset="utf-8",
    )


# ============================================================
# POST /photos/share-upload
# ============================================================

@app.route(route="photos/share-upload", methods=["POST"])
def photos_share_upload(req: func.HttpRequest) -> func.HttpResponse:
    """Get a presigned URL using a share token (no auth required)."""
    if not ENABLE_SHARE_URL:
        return _err(403, "Share URL feature is disabled")

    b = _body(req)
    token = b.get("token", "")
    filename = b.get("filename", "")
    content_type = b.get("contentType", "image/jpeg")

    if not token or not filename:
        return _err(400, "token and filename are required")

    # Validate token
    container = _get_container("photos")
    query = "SELECT * FROM c WHERE c.id = @tokenId AND c.status = 'active'"
    params = [{"name": "@tokenId", "value": f"share_token:{token}"}]
    items = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))

    if not items:
        return _err(403, "Invalid or expired token")

    item = items[0]

    # Expiration check
    created_at = item.get("createdAt", "")
    expires_hours = int(item.get("expiresHours", 24))
    try:
        created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > created_dt + timedelta(hours=expires_hours):
            return _err(403, "Token has expired")
    except Exception:
        pass

    uid = item["userId"]
    label_id = item.get("labelId", "")
    label_name = item.get("labelName", "")
    photo_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    date_path = f"{now.year}/{now.month:02d}/{now.day:02d}"
    blob_key = f"users/{uid}/{date_path}/{photo_id}"

    upload_url = _generate_upload_sas(blob_key, content_type)

    # Create photo record
    photo_doc = {
        "id": photo_id,
        "userId": uid,
        "filename": filename,
        "contentType": content_type,
        "blobKey": blob_key,
        "status": "uploading",
        "createdAt": now.isoformat(),
        "labels": [label_id] if label_id else [],
        "size": 0,
        "uploadedViaShare": True,
    }
    if label_id and label_name:
        photo_doc["labelNames"] = {label_id: label_name}
    container.upsert_item(body=photo_doc)

    return _ok(200, {
        "uploadUrl": upload_url,
        "photoId": photo_id,
    })


# ============================================================
# POST /shares
# ============================================================

@app.route(route="shares", methods=["POST"])
def create_share(req: func.HttpRequest) -> func.HttpResponse:
    """Create a label share request."""
    if not ENABLE_LABEL_SHARING:
        return _err(403, "Label sharing feature is disabled")

    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    username = user["username"]
    b = _body(req)
    to_username = b.get("toUsername", "").strip()
    label_id = b.get("labelId", "").strip()
    label_name = b.get("labelName", "").strip()

    if not to_username or not label_id:
        return _err(400, "toUsername and labelId are required")

    # Find recipient user
    users_container = _get_container("users")
    query = "SELECT * FROM c WHERE c.username = @username"
    params = [{"name": "@username", "value": to_username}]
    recipients = list(users_container.query_items(query=query, parameters=params, enable_cross_partition_query=True))

    if not recipients:
        return _err(404, "User not found")

    to_uid = recipients[0]["id"]
    if to_uid == uid:
        return _err(400, "Cannot share with yourself")

    share_id = str(uuid.uuid4())
    photos_container = _get_container("photos")

    # Create receiver's share record
    receiver_doc = {
        "id": f"share:{share_id}",
        "userId": to_uid,
        "fromUser": uid,
        "fromUsername": username,
        "labelId": label_id,
        "labelName": label_name or label_id,
        "status": "pending",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "labels": [],
    }
    photos_container.upsert_item(body=receiver_doc)

    # Create sender's share record
    sender_doc = {
        "id": f"sent_share:{share_id}",
        "userId": uid,
        "toUser": to_uid,
        "toUsername": to_username,
        "labelId": label_id,
        "labelName": label_name or label_id,
        "status": "active",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "labels": [],
    }
    photos_container.upsert_item(body=sender_doc)

    return _ok(201, {
        "message": "Share request created.",
        "shareId": share_id,
    })


# ============================================================
# GET /shares/pending
# ============================================================

@app.route(route="shares/pending", methods=["GET"])
def shares_pending(req: func.HttpRequest) -> func.HttpResponse:
    """List pending share requests received."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    container = _get_container("photos")

    query = "SELECT * FROM c WHERE c.userId = @uid AND STARTSWITH(c.id, 'share:') AND c.status = 'pending'"
    params = [{"name": "@uid", "value": uid}]
    items = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))

    shares = []
    for item in items:
        shares.append({
            "shareId": item["id"].removeprefix("share:"),
            "fromUser": item.get("fromUsername", ""),
            "labelId": item.get("labelId", ""),
            "labelName": item.get("labelName", ""),
            "createdAt": item.get("createdAt", ""),
        })

    return _ok(200, {"shares": shares})


# ============================================================
# GET /shares/sent
# ============================================================

@app.route(route="shares/sent", methods=["GET"])
def shares_sent(req: func.HttpRequest) -> func.HttpResponse:
    """List shares sent by the current user."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    container = _get_container("photos")

    query = "SELECT * FROM c WHERE c.userId = @uid AND STARTSWITH(c.id, 'sent_share:')"
    params = [{"name": "@uid", "value": uid}]
    items = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))

    shares = []
    for item in items:
        shares.append({
            "shareId": item["id"].removeprefix("sent_share:"),
            "toUser": item.get("toUsername", ""),
            "labelId": item.get("labelId", ""),
            "labelName": item.get("labelName", ""),
            "status": item.get("status", ""),
            "createdAt": item.get("createdAt", ""),
        })

    return _ok(200, {"shares": shares})


# ============================================================
# GET /shares
# ============================================================

@app.route(route="shares", methods=["GET"])
def shares_list(req: func.HttpRequest) -> func.HttpResponse:
    """List accepted shares."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    container = _get_container("photos")

    query = "SELECT * FROM c WHERE c.userId = @uid AND STARTSWITH(c.id, 'share:') AND c.status = 'accepted'"
    params = [{"name": "@uid", "value": uid}]
    items = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))

    shares = []
    for item in items:
        shares.append({
            "shareId": item["id"].removeprefix("share:"),
            "fromUser": item.get("fromUsername", ""),
            "fromUserId": item.get("fromUser", ""),
            "labelId": item.get("labelId", ""),
            "labelName": item.get("labelName", ""),
            "createdAt": item.get("createdAt", ""),
        })

    return _ok(200, {"shares": shares})


# ============================================================
# POST /shares/{shareId}/accept
# ============================================================

@app.route(route="shares/{share_id}/accept", methods=["POST"])
def shares_accept(req: func.HttpRequest) -> func.HttpResponse:
    """Accept a pending share request."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    share_id = req.route_params.get("share_id", "")

    container = _get_container("photos")
    doc_id = f"share:{share_id}"

    try:
        item = container.read_item(item=doc_id, partition_key=uid)
    except cosmos_exceptions.CosmosResourceNotFoundError:
        return _err(404, "Share not found")

    item["status"] = "accepted"
    container.upsert_item(body=item)

    return _ok(200, {"message": "Share accepted."})


# ============================================================
# POST /shares/{shareId}/reject
# ============================================================

@app.route(route="shares/{share_id}/reject", methods=["POST"])
def shares_reject(req: func.HttpRequest) -> func.HttpResponse:
    """Reject a pending share request."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    share_id = req.route_params.get("share_id", "")

    container = _get_container("photos")
    doc_id = f"share:{share_id}"

    try:
        container.delete_item(item=doc_id, partition_key=uid)
    except cosmos_exceptions.CosmosResourceNotFoundError:
        pass

    return _ok(200, {"message": "Share rejected."})


# ============================================================
# DELETE /shares/{shareId}
# ============================================================

@app.route(route="shares/{share_id}", methods=["DELETE"])
def shares_delete(req: func.HttpRequest) -> func.HttpResponse:
    """Remove a share (works for both sender and receiver)."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    share_id = req.route_params.get("share_id", "")

    container = _get_container("photos")

    # Try as receiver first
    receiver_id = f"share:{share_id}"
    try:
        receiver_item = container.read_item(item=receiver_id, partition_key=uid)
        from_uid = receiver_item.get("fromUser", "")
        container.delete_item(item=receiver_id, partition_key=uid)
        # Also delete sender's record
        if from_uid:
            try:
                container.delete_item(item=f"sent_share:{share_id}", partition_key=from_uid)
            except cosmos_exceptions.CosmosResourceNotFoundError:
                pass
        return _ok(200, {"message": "Share removed."})
    except cosmos_exceptions.CosmosResourceNotFoundError:
        pass

    # Try as sender
    sender_id = f"sent_share:{share_id}"
    try:
        sender_item = container.read_item(item=sender_id, partition_key=uid)
        to_uid = sender_item.get("toUser", "")
        container.delete_item(item=sender_id, partition_key=uid)
        # Also delete receiver's record
        if to_uid:
            try:
                container.delete_item(item=f"share:{share_id}", partition_key=to_uid)
            except cosmos_exceptions.CosmosResourceNotFoundError:
                pass
        return _ok(200, {"message": "Share removed."})
    except cosmos_exceptions.CosmosResourceNotFoundError:
        pass

    return _err(404, "Share not found")


# ============================================================
# Upload Page HTML Builder
# ============================================================

def _build_upload_page_html(token: str, api_base: str) -> str:
    """Build the HTML upload page."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Photo Upload</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }}
        .card {{ background: white; border-radius: 20px; padding: 40px; max-width: 480px; width: 100%; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }}
        h1 {{ font-size: 1.5em; color: #333; margin-bottom: 8px; }}
        .subtitle {{ color: #888; font-size: 0.9em; margin-bottom: 24px; }}
        .upload-area {{ border: 2px dashed #ddd; border-radius: 16px; padding: 48px 24px; text-align: center; cursor: pointer; transition: all 0.3s ease; }}
        .upload-area:hover {{ border-color: #667eea; background: #f8f7ff; }}
        .upload-area.dragover {{ border-color: #667eea; background: #ede9ff; transform: scale(1.02); }}
        .upload-area .icon {{ font-size: 48px; margin-bottom: 12px; }}
        .upload-area p {{ color: #666; margin: 4px 0; }}
        .upload-area .browse {{ color: #667eea; font-weight: 600; text-decoration: underline; cursor: pointer; }}
        input[type="file"] {{ display: none; }}
        .file-count {{ color: #667eea; font-weight: 600; margin-top: 12px; font-size: 0.95em; }}
        button {{ background: linear-gradient(135deg, #667eea, #764ba2); color: white; border: none; padding: 14px 32px; border-radius: 12px; font-size: 1em; font-weight: 600; cursor: pointer; width: 100%; margin-top: 20px; transition: all 0.3s ease; }}
        button:hover:not(:disabled) {{ transform: translateY(-2px); box-shadow: 0 8px 24px rgba(102, 126, 234, 0.4); }}
        button:disabled {{ background: #ddd; transform: none; box-shadow: none; cursor: not-allowed; }}
        .status {{ margin-top: 16px; padding: 12px 16px; border-radius: 12px; font-size: 0.9em; }}
        .success {{ background: #e8f5e9; color: #2e7d32; }}
        .error {{ background: #ffebee; color: #c62828; }}
        .progress {{ background: #ede9ff; color: #5c4db1; }}
        .progress-bar {{ width: 100%; height: 4px; background: #eee; border-radius: 2px; margin-top: 8px; overflow: hidden; }}
        .progress-bar-fill {{ height: 100%; background: linear-gradient(90deg, #667eea, #764ba2); transition: width 0.3s ease; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>Upload Photos</h1>
        <p class="subtitle">Select or drop images to upload. This link expires in {expires_hours} hours.</p>
        <div class="upload-area" id="dropArea" onclick="document.getElementById('fileInput').click()">
            <div class="icon">&#9729;&#65039;</div>
            <p>Drag & Drop</p>
            <p>or <span class="browse">Browse Files</span></p>
            <input type="file" id="fileInput" accept="image/*" multiple>
            <div id="fileCount" class="file-count"></div>
        </div>
        <button id="uploadBtn" onclick="uploadFiles()" disabled>Upload</button>
        <div id="status"></div>
    </div>
    <script>
        const token = '{token}';
        let selectedFiles = [];
        const fileInput = document.getElementById('fileInput');
        const uploadBtn = document.getElementById('uploadBtn');
        const statusDiv = document.getElementById('status');
        const dropArea = document.getElementById('dropArea');
        const fileCount = document.getElementById('fileCount');

        fileInput.addEventListener('change', (e) => {{
            selectedFiles = Array.from(e.target.files);
            uploadBtn.disabled = selectedFiles.length === 0;
            fileCount.textContent = selectedFiles.length > 0 ? selectedFiles.length + ' file(s) selected' : '';
            statusDiv.innerHTML = '';
        }});

        dropArea.addEventListener('dragover', (e) => {{ e.preventDefault(); dropArea.classList.add('dragover'); }});
        dropArea.addEventListener('dragleave', () => {{ dropArea.classList.remove('dragover'); }});
        dropArea.addEventListener('drop', (e) => {{
            e.preventDefault();
            dropArea.classList.remove('dragover');
            selectedFiles = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/'));
            uploadBtn.disabled = selectedFiles.length === 0;
            fileCount.textContent = selectedFiles.length > 0 ? selectedFiles.length + ' file(s) selected' : '';
            statusDiv.innerHTML = '';
        }});

        async function uploadFiles() {{
            uploadBtn.disabled = true;
            let success = 0, failed = 0;
            for (const file of selectedFiles) {{
                const pct = Math.round(((success + failed) / selectedFiles.length) * 100);
                statusDiv.innerHTML = '<div class="status progress">Uploading... (' + (success + failed + 1) + '/' + selectedFiles.length + ')<div class="progress-bar"><div class="progress-bar-fill" style="width:' + pct + '%"></div></div></div>';
                try {{
                    const res = await fetch('{api_base}/photos/share-upload', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ token: token, filename: file.name, contentType: file.type }})
                    }});
                    const data = await res.json();
                    if (!data.uploadUrl) throw new Error('No upload URL');
                    const putRes = await fetch(data.uploadUrl, {{
                        method: 'PUT',
                        headers: {{ 'Content-Type': file.type, 'x-ms-blob-type': 'BlockBlob' }},
                        body: file
                    }});
                    if (!putRes.ok) throw new Error('Upload failed');
                    success++;
                }} catch (e) {{
                    console.error(e);
                    failed++;
                }}
            }}
            if (failed === 0) {{
                statusDiv.innerHTML = '<div class="status success">&#10004; ' + success + ' file(s) uploaded successfully</div>';
            }} else {{
                statusDiv.innerHTML = '<div class="status error">Done: ' + success + ' succeeded / ' + failed + ' failed</div>';
            }}
            uploadBtn.disabled = false;
            selectedFiles = [];
            fileCount.textContent = '';
        }}
    </script>
</body>
</html>"""


# ============================================================
# POST /photos/share-download-url
# ============================================================

@app.route(route="photos/share-download-url", methods=["POST"])
def share_download_url(req: func.HttpRequest) -> func.HttpResponse:
    """Generate a download page URL for sharing photos by label."""
    if not ENABLE_SHARE_DOWNLOAD_URL:
        return _err(403, "Share URL feature is disabled")
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    b = _body(req)
    label_id = b.get("labelId", "")
    label_name = b.get("labelName", "")
    expires_hours = int(b.get("expiresHours", 72))

    if not label_id:
        return _err(400, "labelId is required")

    # Count matching photos
    container = _get_container("photos")
    query = "SELECT * FROM c WHERE c.userId = @uid AND c.status != 'deleted' AND c.status != 'uploading' AND ARRAY_CONTAINS(c.labels, @labelId) AND NOT STARTSWITH(c.id, 'share_token:') AND NOT STARTSWITH(c.id, 'share:') AND NOT STARTSWITH(c.id, 'download_token:')"
    params = [{"name": "@uid", "value": uid}, {"name": "@labelId", "value": label_id}]
    matching = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))

    if not matching:
        return _err(404, "No photos found matching the criteria")

    token = str(uuid.uuid4())
    container.upsert_item(body={
        "id": f"download_token:{token}",
        "userId": uid,
        "status": "active",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "expiresHours": expires_hours,
        "labelId": label_id,
        "labelName": label_name,
        "photoCount": len(matching),
    })

    base_url = FUNCTION_APP_URL or f"https://{req.headers.get('Host', 'localhost')}"
    page_url = f"{base_url}/v1/download-page?token={token}"

    return _ok(200, {
        "downloadUrl": page_url,
        "token": token,
        "expiresHours": expires_hours,
        "photoCount": len(matching),
    })


# ============================================================
# GET /download-page?token=xxx
# ============================================================

@app.route(route="download-page", methods=["GET"])
def download_page(req: func.HttpRequest) -> func.HttpResponse:
    """Render an HTML download page for shared photos."""
    if not ENABLE_SHARE_DOWNLOAD_URL:
        return func.HttpResponse("<h1>This feature is disabled.</h1>", status_code=403, mimetype="text/html")

    token = req.params.get("token", "")
    if not token:
        return func.HttpResponse("<h1>Invalid link.</h1>", status_code=400, mimetype="text/html")

    # Find token record
    container = _get_container("photos")
    query = "SELECT * FROM c WHERE c.id = @tokenId AND c.status = 'active'"
    params = [{"name": "@tokenId", "value": f"download_token:{token}"}]
    items = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))

    if not items:
        return func.HttpResponse("<h1>This link has expired or is invalid.</h1>", status_code=404, mimetype="text/html")

    token_record = items[0]
    uid = token_record["userId"]
    label_id = token_record.get("labelId", "")
    label_name = token_record.get("labelName", label_id)
    expires_hours = int(token_record.get("expiresHours", 72))

    # Check expiration
    created_at = token_record.get("createdAt", "")
    if created_at:
        created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > created_dt + timedelta(hours=expires_hours):
            return func.HttpResponse("<h1>This link has expired.</h1>", status_code=410, mimetype="text/html")

    # Get photos with label
    photo_query = "SELECT * FROM c WHERE c.userId = @uid AND c.status != 'deleted' AND c.status != 'uploading' AND ARRAY_CONTAINS(c.labels, @labelId) AND NOT STARTSWITH(c.id, 'share_token:') AND NOT STARTSWITH(c.id, 'share:') AND NOT STARTSWITH(c.id, 'download_token:')"
    photo_params = [{"name": "@uid", "value": uid}, {"name": "@labelId", "value": label_id}]
    photos = list(container.query_items(query=photo_query, parameters=photo_params, enable_cross_partition_query=True))

    photo_entries = []
    for photo in photos:
        blob_key = photo.get("blobKey", "")
        thumb_key = photo.get("thumbnailKey", blob_key)
        if not blob_key:
            continue
        thumb_url = _generate_sas_url(thumb_key)
        full_url = _generate_sas_url(blob_key)
        photo_entries.append({
            "filename": photo.get("filename", photo["id"]),
            "thumbUrl": thumb_url,
            "fullUrl": full_url,
        })

    photos_json = json.dumps([{"filename": e["filename"], "fullUrl": e["fullUrl"]} for e in photo_entries])

    photo_grid = ""
    for entry in photo_entries:
        photo_grid += f'''
        <div class="photo-card">
            <a href="{entry["fullUrl"]}" target="_blank" download="{entry["filename"]}">
                <img src="{entry["thumbUrl"]}" alt="{entry["filename"]}" loading="lazy" />
            </a>
            <div class="photo-name">{entry["filename"]}</div>
        </div>'''

    html = f'''<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Download Photos</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }}
        .card {{ background: white; border-radius: 20px; padding: 40px; max-width: 800px; width: 100%; margin: 0 auto; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }}
        h1 {{ font-size: 1.5em; color: #333; margin-bottom: 8px; }}
        .subtitle {{ color: #888; font-size: 0.9em; margin-bottom: 16px; }}
        .actions {{ margin-bottom: 24px; }}
        .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 24px; }}
        .photo-card {{ border-radius: 12px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        .photo-card img {{ width: 100%; aspect-ratio: 1; object-fit: cover; cursor: pointer; transition: opacity 0.2s; }}
        .photo-card img:hover {{ opacity: 0.7; }}
        .photo-name {{ padding: 4px 6px; font-size: 0.65rem; color: #666; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; background: #f9f9f9; }}
        button {{ background: linear-gradient(135deg, #667eea, #764ba2); color: white; border: none; padding: 14px 32px; border-radius: 12px; font-size: 1em; font-weight: 600; cursor: pointer; width: 100%; transition: all 0.3s ease; }}
        button:hover:not(:disabled) {{ transform: translateY(-2px); box-shadow: 0 8px 24px rgba(102, 126, 234, 0.4); }}
        button:disabled {{ background: #ddd; transform: none; box-shadow: none; cursor: not-allowed; }}
        .status {{ margin-top: 12px; padding: 12px 16px; border-radius: 12px; font-size: 0.9em; text-align: center; }}
        .footer {{ text-align: center; margin-top: 16px; color: #999; font-size: 0.8em; }}
    </style>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js"></script>
</head>
<body>
    <div class="card">
        <h1>Download Photos</h1>
        <p class="subtitle">{label_name} — {len(photo_entries)} photos</p>
        <div class="actions">
            <p class="footer" style="margin-bottom: 12px;">Click a photo to download individually. This link expires in {expires_hours} hours.</p>
            <button id="downloadAllBtn" onclick="downloadAll()">📥 Download ZIP — for PC</button>
            <div id="status"></div>
        </div>
        <div class="grid">{photo_grid}</div>
    </div>
    <script>
        const photos = {photos_json};
        async function downloadAll() {{
            const btn = document.getElementById('downloadAllBtn');
            const st = document.getElementById('status');
            btn.disabled = true;
            st.className = 'status'; st.style.background = '#ede9ff'; st.style.color = '#5c4db1';
            st.textContent = 'Preparing download...';
            try {{
                const zip = new JSZip();
                for (let i = 0; i < photos.length; i++) {{
                    st.textContent = `Downloading ${{i+1}} / ${{photos.length}}...`;
                    const r = await fetch(photos[i].fullUrl);
                    zip.file(photos[i].filename, await r.blob());
                }}
                st.textContent = 'Creating ZIP...';
                const blob = await zip.generateAsync({{type:'blob'}});
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = '{label_name}.zip';
                a.click();
                st.style.background = '#e8f5e9'; st.style.color = '#2e7d32';
                st.textContent = 'Download complete!';
            }} catch(e) {{
                st.style.background = '#ffebee'; st.style.color = '#c62828';
                st.textContent = 'Error: ' + e.message;
            }}
            btn.disabled = false;
        }}
    </script>
</body>
</html>'''

    return func.HttpResponse(html, status_code=200, mimetype="text/html", headers={"Access-Control-Allow-Origin": "*"})


# ============================================================
# Blob Storage Event (via Event Grid Subscription)
# ============================================================

@app.event_grid_trigger(arg_name="event")
def process_photo(event: func.EventGridEvent):
    """Triggered by Event Grid when a blob is created in the photos container."""
    from storage_trigger import handle_blob_event
    handle_blob_event(event)
