"""
Daily Cloud Photo — GCP Cloud Function (Unified HTTP Handler)
Equivalent to the AWS Lambda implementation using:
  - Firebase Auth (instead of Cognito)
  - Firestore Native mode (instead of DynamoDB)
  - Cloud Storage (instead of S3)
  - Flask routing (Cloud Functions standard)
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import firebase_admin
from firebase_admin import auth as firebase_auth
from google.cloud import firestore
from google.cloud import storage as gcs
import flask
import functions_framework

# ── Environment Variables ──
PHOTOS_BUCKET = os.environ.get('PHOTOS_BUCKET', '')
GCP_PROJECT = os.environ.get('GCP_PROJECT', '')
REQUIRE_EMAIL = os.environ.get('REQUIRE_EMAIL', 'true') == 'true'
REQUIRE_PHONE = os.environ.get('REQUIRE_PHONE', 'false') == 'true'
ENABLE_SHARE_URL = os.environ.get('ENABLE_SHARE_URL', 'true') == 'true'
ENABLE_SHARE_DOWNLOAD_URL = os.environ.get('ENABLE_SHARE_DOWNLOAD_URL', 'true') == 'true'
ENABLE_LABEL_SHARING = os.environ.get('ENABLE_LABEL_SHARING', 'true') == 'true'
APP_DISPLAY_NAME = os.environ.get('APP_DISPLAY_NAME', 'Daily Cloud Photo Backend')
SIGNED_URL_EXPIRY = 3600  # 1 hour

# ── Initialize Firebase & GCP Clients ──
if not firebase_admin._apps:
    firebase_admin.initialize_app()

db = firestore.Client()
storage_client = gcs.Client()

# Firestore collection name
PHOTOS_COLLECTION = 'photos'


# ============================================================
# Helpers
# ============================================================

def _ok(status, body):
    """Return a JSON success response."""
    return flask.make_response(
        flask.jsonify(body),
        status,
        {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}
    )


def _err(status, msg, code=None):
    """Return a JSON error response."""
    body = {'message': msg}
    if code:
        body['error'] = code
    return flask.make_response(
        flask.jsonify(body),
        status,
        {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}
    )


def _get_user_id(request):
    """Extract Firebase UID from Authorization Bearer token."""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    id_token = auth_header[7:]
    try:
        decoded = firebase_auth.verify_id_token(id_token)
        return decoded.get('uid')
    except Exception:
        return None


def _get_user_email(uid):
    """Get email from Firebase Auth by UID."""
    try:
        user = firebase_auth.get_user(uid)
        return user.email or user.display_name or uid
    except Exception:
        return uid


def _prefix(uid):
    """Storage path prefix for a user."""
    return f'users/{uid}/'


def _doc_id(user_id, photo_id):
    """Generate Firestore document ID from userId and photoId."""
    safe_photo_id = photo_id.replace('/', '_')
    return f'{user_id}_{safe_photo_id}'


def _get_photo_doc(user_id, photo_id):
    """Get a photo document from Firestore."""
    doc_ref = db.collection(PHOTOS_COLLECTION).document(_doc_id(user_id, photo_id))
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict()
    return None


def _query_user_photos(user_id, prefix=None, limit=None):
    """Query photos for a user, optionally filtering by photoId prefix."""
    query = db.collection(PHOTOS_COLLECTION).where('userId', '==', user_id)
    if prefix:
        # Filter documents where photoId starts with prefix
        query = query.where('photoId', '>=', prefix).where('photoId', '<', prefix + '\uffff')
    if limit:
        query = query.limit(limit)
    return query.stream()


def _generate_signed_url(blob_name, method='GET', content_type=None, expiry=SIGNED_URL_EXPIRY):
    """Generate a signed URL for Cloud Storage using IAM signBlob."""
    import google.auth
    from google.auth.transport import requests as auth_requests

    credentials, project = google.auth.default()
    # Refresh credentials to get access token for signing
    auth_req = auth_requests.Request()
    credentials.refresh(auth_req)

    bucket = storage_client.bucket(PHOTOS_BUCKET)
    blob = bucket.blob(blob_name)

    # Use service_account_email for signing via IAM API
    sa_email = f"{credentials.service_account_email}" if hasattr(credentials, 'service_account_email') else None
    if not sa_email:
        # Fallback: get from metadata server
        import requests as http_requests
        resp = http_requests.get(
            'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email',
            headers={'Metadata-Flavor': 'Google'}, timeout=2)
        sa_email = resp.text

    if method == 'PUT':
        url = blob.generate_signed_url(
            version='v4',
            expiration=timedelta(seconds=expiry),
            method='PUT',
            content_type=content_type or 'application/octet-stream',
            service_account_email=sa_email,
            access_token=credentials.token,
        )
    else:
        url = blob.generate_signed_url(
            version='v4',
            expiration=timedelta(seconds=expiry),
            method='GET',
            service_account_email=sa_email,
            access_token=credentials.token,
        )
    return url


# ============================================================
# CORS Handling
# ============================================================

def _handle_cors(request):
    """Handle CORS preflight requests."""
    if request.method == 'OPTIONS':
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Authorization, Content-Type',
            'Access-Control-Max-Age': '3600',
        }
        return ('', 204, headers)
    return None


# ============================================================
# Entry Point
# ============================================================

@functions_framework.http
def main_handler(request):
    """Main Cloud Function entry point — routes all HTTP requests."""
    # Handle CORS preflight
    cors_response = _handle_cors(request)
    if cors_response:
        return cors_response

    path = request.path
    method = request.method.upper()

    # Strip /v1 prefix if present
    if path.startswith('/v1'):
        path = path[3:]

    # Ensure path starts with /
    if not path.startswith('/'):
        path = '/' + path

    # ── Routing ──
    if method == 'GET' and path == '/info':
        return _info(request)

    # Auth routes
    if method == 'POST' and path == '/auth/signup':
        return _signup(request)
    if method == 'POST' and path == '/auth/confirm':
        return _confirm(request)
    if method == 'POST' and path == '/auth/signin':
        return _signin(request)
    if method == 'POST' and path == '/auth/refresh':
        return _refresh(request)
    if method == 'POST' and path == '/auth/forgot-password':
        return _forgot_password(request)
    if method == 'POST' and path == '/auth/reset-password':
        return _reset_password(request)

    # Photo routes
    if method == 'GET' and path == '/photos':
        return _photos_list(request)
    if method == 'POST' and path == '/photos/upload-url':
        return _upload_url(request)
    if method == 'POST' and path == '/photos/share-upload-url':
        return _share_upload_url(request)
    if method == 'GET' and path == '/upload-page':
        return _upload_page(request)
    if method == 'POST' and path == '/photos/share-upload':
        return _share_upload(request)
    if method == 'POST' and path == '/photos/share-download-url':
        return _share_download_url(request)
    if method == 'GET' and path == '/download-page':
        return _download_page(request)

    # Photo routes with ID parameter
    if method == 'POST' and path.startswith('/photos/') and path.endswith('/confirm'):
        return _photos_confirm(request, path)
    if method == 'PUT' and path.startswith('/photos/') and path.endswith('/labels'):
        return _photos_update_labels(request, path)
    if method == 'DELETE' and path.startswith('/photos/'):
        return _photos_delete(request, path)
    if method == 'GET' and path.startswith('/photos/'):
        return _photos_get_one(request, path)

    # Share routes
    if method == 'POST' and path == '/shares':
        return _create_share(request)
    if method == 'GET' and path == '/shares/pending':
        return _pending_shares(request)
    if method == 'GET' and path == '/shares/sent':
        return _sent_shares(request)
    if method == 'GET' and path == '/shares':
        return _list_shares(request)
    if method == 'POST' and path.startswith('/shares/') and path.endswith('/accept'):
        return _accept_share(request, path)
    if method == 'POST' and path.startswith('/shares/') and path.endswith('/reject'):
        return _reject_share(request, path)
    if method == 'DELETE' and path.startswith('/shares/'):
        return _delete_share(request, path)

    return _err(404, 'Not found')


# ============================================================
# GET /info
# ============================================================

def _info(request):
    fields = ['username', 'password']
    if REQUIRE_EMAIL:
        fields.append('email')
    if REQUIRE_PHONE:
        fields.append('phone')

    features = []
    if ENABLE_SHARE_URL:
        features.append('share-upload-url')
    if ENABLE_SHARE_DOWNLOAD_URL:
        features.append('share-download-url')
    if ENABLE_LABEL_SHARING:
        features.append('label-sharing')

    return _ok(200, {
        'name': APP_DISPLAY_NAME,
        'version': '1.0.0',
        'signupFields': fields,
        'features': features,
    })


# ============================================================
# POST /auth/signup
# ============================================================

def _signup(request):
    body = request.get_json(silent=True) or {}
    username = body.get('username', '').strip()
    password = body.get('password', '')
    email = body.get('email', '').strip()
    phone = body.get('phone', '').strip()

    if not username or not password:
        return _err(400, 'username and password are required')

    try:
        # Create Firebase user with email as identifier
        # Use email if provided, otherwise create from username
        user_email = email if email else f'{username}@placeholder.local'
        create_kwargs = {
            'email': user_email,
            'password': password,
            'display_name': username,
        }
        if phone:
            create_kwargs['phone_number'] = phone

        user = firebase_auth.create_user(**create_kwargs)

        # If email verification is required, send verification email
        # Firebase handles this via client SDK; server just creates user
        confirmation_required = REQUIRE_EMAIL and email

        return _ok(201, {
            'message': 'User created. Confirmation may be required.',
            'confirmationRequired': False,
        })

    except firebase_admin.exceptions.AlreadyExistsError:
        return _err(409, 'Username already exists', 'UsernameExists')
    except ValueError as e:
        return _err(400, str(e), 'InvalidParameter')
    except Exception as e:
        error_msg = str(e)
        if 'WEAK_PASSWORD' in error_msg:
            return _err(400, 'Password is too weak', 'InvalidPassword')
        return _err(500, error_msg)


# ============================================================
# POST /auth/confirm
# ============================================================

def _confirm(request):
    """
    Confirm user email verification.
    In Firebase, email verification is handled client-side via email links.
    This endpoint is provided for API compatibility; it verifies the user
    manually if called with proper credentials.
    """
    body = request.get_json(silent=True) or {}
    username = body.get('username', '').strip()
    code = body.get('confirmationCode', '').strip()

    if not username or not code:
        return _err(400, 'username and confirmationCode are required')

    try:
        # In Firebase, confirmation is typically done via email link.
        # For API compatibility, we look up user and mark as verified.
        user = firebase_auth.get_user_by_email(username)
        if not user:
            return _err(404, 'User not found', 'UserNotFound')

        # Verify the confirmation code from Firestore
        code_doc = db.collection('confirmation_codes').document(username).get()
        if not code_doc.exists:
            return _err(400, 'Invalid confirmation code', 'CodeMismatch')

        code_data = code_doc.to_dict()
        if code_data.get('code') != code:
            return _err(400, 'Invalid confirmation code', 'CodeMismatch')

        # Check expiration
        expires_at = code_data.get('expiresAt', '')
        if expires_at:
            try:
                exp_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                if datetime.now(timezone.utc) > exp_dt:
                    return _err(400, 'Confirmation code expired', 'ExpiredCode')
            except Exception:
                pass

        # Mark user as verified
        firebase_auth.update_user(user.uid, email_verified=True)

        # Clean up code
        db.collection('confirmation_codes').document(username).delete()

        return _ok(200, {'message': 'User confirmed.'})

    except firebase_auth.UserNotFoundError:
        return _err(404, 'User not found', 'UserNotFound')
    except Exception as e:
        return _err(500, str(e))


# ============================================================
# POST /auth/signin
# ============================================================

def _signin(request):
    """
    Sign in a user and return tokens.
    Firebase Auth sign-in is typically done client-side via Firebase SDK.
    For REST API compatibility, we use the Firebase Auth REST API
    (identitytoolkit) to exchange email/password for tokens.
    """
    body = request.get_json(silent=True) or {}
    username = body.get('username', '').strip()
    password = body.get('password', '')

    if not username or not password:
        return _err(400, 'username and password are required')

    try:
        import requests as http_requests

        # Use Firebase Auth REST API to sign in
        api_key = os.environ.get('FIREBASE_API_KEY', '')
        if not api_key:
            # Try to get Web API key from Firebase project config
            api_key = _get_firebase_api_key()

        if not api_key:
            return _err(500, 'Firebase API key not configured')

        # Resolve username to email if needed
        email = username
        if '@' not in username:
            try:
                user = firebase_auth.get_user_by_email(f'{username}@placeholder.local')
                email = user.email
            except Exception:
                # Try display_name lookup
                email = username

        url = f'https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}'
        payload = {
            'email': email,
            'password': password,
            'returnSecureToken': True,
        }
        resp = http_requests.post(url, json=payload, timeout=10)
        data = resp.json()

        if resp.status_code != 200:
            error_message = data.get('error', {}).get('message', '')
            if 'EMAIL_NOT_FOUND' in error_message or 'INVALID_LOGIN_CREDENTIALS' in error_message:
                return _err(401, 'Incorrect username or password', 'NotAuthorized')
            if 'USER_DISABLED' in error_message:
                return _err(403, 'User account is disabled', 'UserDisabled')
            return _err(401, 'Authentication failed', 'NotAuthorized')

        return _ok(200, {
            'accessToken': data.get('idToken', ''),
            'refreshToken': data.get('refreshToken', ''),
            'expiresIn': int(data.get('expiresIn', 3600)),
        })

    except Exception as e:
        return _err(500, str(e))


# ============================================================
# POST /auth/refresh
# ============================================================

def _refresh(request):
    """Refresh an expired ID token using a refresh token."""
    body = request.get_json(silent=True) or {}
    refresh_token = body.get('refreshToken', '')

    if not refresh_token:
        return _err(400, 'refreshToken is required')

    try:
        import requests as http_requests

        api_key = os.environ.get('FIREBASE_API_KEY', '')
        if not api_key:
            api_key = _get_firebase_api_key()

        if not api_key:
            return _err(500, 'Firebase API key not configured')

        url = f'https://securetoken.googleapis.com/v1/token?key={api_key}'
        payload = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        }
        resp = http_requests.post(url, json=payload, timeout=10)
        data = resp.json()

        if resp.status_code != 200:
            return _err(401, 'Refresh token is invalid or expired', 'NotAuthorized')

        return _ok(200, {
            'accessToken': data.get('id_token', ''),
            'expiresIn': int(data.get('expires_in', 3600)),
        })

    except Exception as e:
        return _err(500, str(e))


# ============================================================
# POST /auth/forgot-password
# ============================================================

def _forgot_password(request):
    """Send a password reset email via Firebase Auth."""
    body = request.get_json(silent=True) or {}
    username = body.get('username', '').strip()

    if not username:
        return _err(400, 'username is required')

    try:
        import requests as http_requests

        api_key = os.environ.get('FIREBASE_API_KEY', '')
        if not api_key:
            api_key = _get_firebase_api_key()

        # Resolve email
        email = username
        if '@' not in username:
            try:
                user = firebase_auth.get_user_by_email(f'{username}@placeholder.local')
                email = user.email
            except Exception:
                pass

        # Send password reset email via Firebase REST API
        url = f'https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={api_key}'
        payload = {
            'requestType': 'PASSWORD_RESET',
            'email': email,
        }
        http_requests.post(url, json=payload, timeout=10)

        # Always return success for security (don't reveal if user exists)
        return _ok(200, {'message': 'Confirmation code sent.'})

    except Exception:
        # For security, always return success
        return _ok(200, {'message': 'Confirmation code sent.'})


# ============================================================
# POST /auth/reset-password
# ============================================================

def _reset_password(request):
    """Reset password using OOB code from reset email."""
    body = request.get_json(silent=True) or {}
    username = body.get('username', '').strip()
    code = body.get('confirmationCode', '').strip()
    new_password = body.get('newPassword', '')

    if not username or not code or not new_password:
        return _err(400, 'username, confirmationCode, and newPassword are required')

    try:
        import requests as http_requests

        api_key = os.environ.get('FIREBASE_API_KEY', '')
        if not api_key:
            api_key = _get_firebase_api_key()

        # Use Firebase REST API to confirm password reset
        url = f'https://identitytoolkit.googleapis.com/v1/accounts:resetPassword?key={api_key}'
        payload = {
            'oobCode': code,
            'newPassword': new_password,
        }
        resp = http_requests.post(url, json=payload, timeout=10)
        data = resp.json()

        if resp.status_code != 200:
            error_msg = data.get('error', {}).get('message', '')
            if 'INVALID_OOB_CODE' in error_msg:
                return _err(400, 'Invalid confirmation code', 'CodeMismatch')
            if 'EXPIRED_OOB_CODE' in error_msg:
                return _err(400, 'Confirmation code expired', 'ExpiredCode')
            if 'WEAK_PASSWORD' in error_msg:
                return _err(400, 'Password is too weak', 'InvalidPassword')
            return _err(400, error_msg or 'Password reset failed')

        return _ok(200, {'message': 'Password reset successful.'})

    except Exception as e:
        return _err(500, str(e))


# ============================================================
# GET /photos
# ============================================================

def _photos_list(request):
    uid = _get_user_id(request)
    if not uid:
        return _err(401, 'Authentication required')

    limit = int(request.args.get('limit', '100'))
    cursor = request.args.get('cursor')

    # Query user's own photos
    query = db.collection(PHOTOS_COLLECTION).where('userId', '==', uid)

    docs = list(query.stream())

    photos = []
    for doc in docs:
        item = doc.to_dict()
        photo_id = item.get('photoId', '')

        # Skip non-photo records
        if photo_id.startswith('share_token:'):
            continue
        if photo_id.startswith('share:'):
            continue
        if photo_id.startswith('sent_share:'):
            continue
        if item.get('status') == 'deleted':
            continue
        if item.get('status') == 'uploading':
            continue        # Generate signed URLs
        gcs_key = item.get('gcsKey', f"{_prefix(uid)}{photo_id}")
        thumbnail_key = item.get('thumbnailKey')

        full_url = None
        thumbnail_url = None
        try:
            full_url = _generate_signed_url(gcs_key)
            if thumbnail_key:
                thumbnail_url = _generate_signed_url(thumbnail_key)
            else:
                thumbnail_url = full_url
        except Exception:
            pass

        photos.append({
            'id': photo_id,
            'filename': item.get('filename', ''),
            'contentType': item.get('contentType', 'image/jpeg'),
            'size': int(item.get('size', 0)),
            'createdAt': item.get('createdAt', ''),
            'thumbnailUrl': thumbnail_url,
            'fullUrl': full_url,
            'labels': item.get('labels', []),
            'labelNames': item.get('labelNames', {}),
            'shared': False,
            'sharedFrom': '',
        })

    # Include shared photos (from accepted shares)
    share_query = db.collection(PHOTOS_COLLECTION).where('userId', '==', uid).where(
        'photoId', '>=', 'share:').where('photoId', '<', 'share:\uffff')
    share_docs = list(share_query.stream())

    for share_doc in share_docs:
        share_item = share_doc.to_dict()
        if share_item.get('status') != 'accepted':
            continue

        from_uid = share_item.get('fromUser', '')
        label_id = share_item.get('labelId', '')
        if not from_uid or not label_id:
            continue

        # Get photos from the sharing user with the specified label
        shared_photos_query = db.collection(PHOTOS_COLLECTION).where('userId', '==', from_uid)
        for sp_doc in shared_photos_query.stream():
            sp = sp_doc.to_dict()
            sp_photo_id = sp.get('photoId', '')

            # Skip non-photo records
            if sp_photo_id.startswith('share_token:') or sp_photo_id.startswith('share:') or sp_photo_id.startswith('sent_share:'):
                continue
            if sp.get('status') == 'deleted':
                continue

            sp_labels = sp.get('labels', [])
            if label_id not in sp_labels:
                continue

            sp_gcs_key = sp.get('gcsKey', f"users/{from_uid}/{sp_photo_id}")
            sp_thumb_key = sp.get('thumbnailKey')

            try:
                sp_full_url = _generate_signed_url(sp_gcs_key)
                sp_thumb_url = _generate_signed_url(sp_thumb_key or sp_gcs_key)
            except Exception:
                sp_full_url = None
                sp_thumb_url = None

            photos.append({
                'id': sp_photo_id,
                'filename': sp.get('filename', ''),
                'contentType': sp.get('contentType', 'image/jpeg'),
                'size': int(sp.get('size', 0)),
                'createdAt': sp.get('createdAt', ''),
                'thumbnailUrl': sp_thumb_url,
                'fullUrl': sp_full_url,
                'labels': sp.get('labels', []),
                'shared': True,
                'sharedFrom': share_item.get('fromUsername', ''),
            })

    # Apply limit and cursor (simple offset-based pagination)
    # Sort by createdAt descending
    photos.sort(key=lambda p: p.get('createdAt', ''), reverse=True)
    start_idx = 0
    if cursor:
        for i, p in enumerate(photos):
            if p['id'] == cursor:
                start_idx = i + 1
                break

    page = photos[start_idx:start_idx + limit]
    next_cursor = page[-1]['id'] if len(photos) > start_idx + limit and page else None

    return _ok(200, {'photos': page, 'nextCursor': next_cursor})


# ============================================================
# GET /photos/{id}
# ============================================================

def _photos_get_one(request, path):
    uid = _get_user_id(request)
    if not uid:
        return _err(401, 'Authentication required')

    photo_id = path.split('/photos/')[-1]
    if not photo_id:
        return _err(400, 'photoId is required')

    item = _get_photo_doc(uid, photo_id)
    if not item:
        return _err(404, 'Photo not found')

    gcs_key = item.get('gcsKey', f"{_prefix(uid)}{photo_id}")
    thumbnail_key = item.get('thumbnailKey')

    full_url = None
    thumbnail_url = None
    try:
        full_url = _generate_signed_url(gcs_key)
        if thumbnail_key:
            thumbnail_url = _generate_signed_url(thumbnail_key)
        else:
            thumbnail_url = full_url
    except Exception:
        pass

    return _ok(200, {
        'id': photo_id,
        'filename': item.get('filename', ''),
        'contentType': item.get('contentType', 'image/jpeg'),
        'size': int(item.get('size', 0)),
        'createdAt': item.get('createdAt', ''),
        'fullUrl': full_url,
        'thumbnailUrl': thumbnail_url,
    })


# ============================================================
# POST /photos/upload-url
# ============================================================

def _upload_url(request):
    uid = _get_user_id(request)
    if not uid:
        return _err(401, 'Authentication required')

    body = request.get_json(silent=True) or {}
    filename = body.get('filename', '')
    content_type = body.get('contentType', 'image/jpeg')
    created_at = body.get('createdAt', datetime.now(timezone.utc).isoformat())
    photo_id = body.get('photoId', '') or str(uuid.uuid4())

    if not filename:
        return _err(400, 'filename is required')

    # Include date in GCS path: users/{uid}/2026/04/26/{photoId}
    try:
        dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
    except Exception:
        dt = datetime.now(timezone.utc)
    date_path = f"{dt.year}/{dt.month:02d}/{dt.day:02d}"
    gcs_key = f"{_prefix(uid)}{date_path}/{photo_id}"

    # Generate signed upload URL
    url = _generate_signed_url(gcs_key, method='PUT', content_type=content_type)

    # Save metadata to Firestore
    doc_ref = db.collection(PHOTOS_COLLECTION).document(_doc_id(uid, photo_id))
    doc_ref.set({
        'userId': uid,
        'photoId': photo_id,
        'filename': filename,
        'contentType': content_type,
        'gcsKey': gcs_key,
        'status': 'uploading',
        'createdAt': created_at,
        'labels': [],
    })

    return _ok(200, {
        'photoId': photo_id,
        'uploadUrl': url,
        'expiresIn': SIGNED_URL_EXPIRY,
    })


# ============================================================
# POST /photos/{id}/confirm
# ============================================================

def _photos_confirm(request, path):
    uid = _get_user_id(request)
    if not uid:
        return _err(401, 'Authentication required')

    parts = path.strip('/').split('/')
    photo_id = parts[1] if len(parts) >= 3 else ''
    if not photo_id:
        return _err(400, 'photoId is required')

    item = _get_photo_doc(uid, photo_id)
    if not item:
        return _err(404, 'Photo not found')

    gcs_key = item.get('gcsKey', '')

    # Check file exists in Cloud Storage
    try:
        bucket = storage_client.bucket(PHOTOS_BUCKET)
        blob = bucket.blob(gcs_key)
        blob.reload()
        size = blob.size or 0
    except Exception:
        return _err(404, 'File not found in storage')

    # Update status in Firestore
    doc_ref = db.collection(PHOTOS_COLLECTION).document(_doc_id(uid, photo_id))
    doc_ref.update({
        'status': 'uploaded',
        'size': size,
    })

    # Generate thumbnail URL (actual thumbnail generation is done by storage trigger)
    thumbnail_url = _generate_signed_url(gcs_key)

    return _ok(200, {'message': 'Upload confirmed.', 'thumbnailUrl': thumbnail_url})


# ============================================================
# PUT /photos/{id}/labels
# ============================================================

def _photos_update_labels(request, path):
    uid = _get_user_id(request)
    if not uid:
        return _err(401, 'Authentication required')

    parts = path.strip('/').split('/')
    photo_id = parts[1] if len(parts) >= 3 else ''
    if not photo_id:
        return _err(400, 'photoId is required')

    body = request.get_json(silent=True) or {}
    labels = body.get('labels', [])
    label_names = body.get('labelNames', {})

    if not isinstance(labels, list):
        return _err(400, 'labels must be an array')

    item = _get_photo_doc(uid, photo_id)
    if not item:
        return _err(404, 'Photo not found')

    update_data = {'labels': labels}
    if label_names and isinstance(label_names, dict):
        update_data['labelNames'] = label_names

    doc_ref = db.collection(PHOTOS_COLLECTION).document(_doc_id(uid, photo_id))
    doc_ref.update(update_data)

    return _ok(200, {'message': 'Labels updated.', 'labels': labels})


# ============================================================
# DELETE /photos/{id}
# ============================================================

def _photos_delete(request, path):
    uid = _get_user_id(request)
    if not uid:
        return _err(401, 'Authentication required')

    parts = path.strip('/').split('/')
    photo_id = parts[1] if len(parts) >= 2 else ''
    if not photo_id:
        return _err(400, 'photoId is required')

    item = _get_photo_doc(uid, photo_id)
    if not item:
        return _err(404, 'Photo not found')

    # Soft delete: change status (GCS data preserved via versioning)
    doc_ref = db.collection(PHOTOS_COLLECTION).document(_doc_id(uid, photo_id))
    doc_ref.update({
        'status': 'deleted',
        'deletedAt': datetime.now(timezone.utc).isoformat(),
    })

    return _ok(200, {'message': 'Photo deleted.'})


# ============================================================
# POST /photos/share-upload-url
# ============================================================

def _share_upload_url(request):
    if not ENABLE_SHARE_URL:
        return _err(403, 'Share URL feature is disabled')

    uid = _get_user_id(request)
    if not uid:
        return _err(401, 'Authentication required')

    body = request.get_json(silent=True) or {}
    expires_hours = int(body.get('expiresHours', 24))
    label_id = body.get('labelId', '') or ''
    label_name = body.get('labelName', '') or ''

    # Generate token
    token = str(uuid.uuid4())

    # Save token to Firestore
    doc_ref = db.collection(PHOTOS_COLLECTION).document(_doc_id(uid, f'share_token:{token}'))
    token_data = {
        'userId': uid,
        'photoId': f'share_token:{token}',
        'status': 'active',
        'createdAt': datetime.now(timezone.utc).isoformat(),
        'expiresHours': expires_hours,
        'labels': [],
    }
    if label_id:
        token_data['labelId'] = label_id
        token_data['labelName'] = label_name
    doc_ref.set(token_data)

    # Generate upload page URL
    # Use the function's own URL as base (force HTTPS for Cloud Run)
    function_url = request.url_root.rstrip('/')
    if function_url.startswith('http://'):
        function_url = 'https://' + function_url[7:]
    page_url = f"{function_url}/upload-page?token={token}"

    return _ok(200, {
        'shareUrl': page_url,
        'token': token,
        'expiresHours': expires_hours,
    })


# ============================================================
# GET /upload-page?token=xxx
# ============================================================

def _upload_page(request):
    if not ENABLE_SHARE_URL:
        return _html_response(403, '<h1>This feature is disabled.</h1>')

    token = request.args.get('token', '')
    if not token:
        return _html_response(400, '<h1>Invalid link</h1>')

    # Validate token by scanning Firestore
    query = db.collection(PHOTOS_COLLECTION).where(
        'photoId', '==', f'share_token:{token}'
    ).where('status', '==', 'active').limit(1)
    docs = list(query.stream())

    if not docs:
        return _html_response(403, '<h1>This link has expired or is invalid.</h1>')

    item = docs[0].to_dict()

    # Expiration check
    created_at = item.get('createdAt', '')
    expires_hours = int(item.get('expiresHours', 24))
    try:
        created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        if datetime.now(timezone.utc) > created_dt + timedelta(hours=expires_hours):
            return _html_response(403, '<h1>This link has expired.</h1>')
    except Exception:
        pass

    # Build API base URL (force HTTPS for Cloud Run)
    api_base = request.url_root.rstrip('/')
    if api_base.startswith('http://'):
        api_base = 'https://' + api_base[7:]

    html = f"""<!DOCTYPE html>
<html lang="ja">
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
            <div class="icon">☁️</div>
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
            fileCount.textContent = selectedFiles.length > 0 ? `${{selectedFiles.length}} file(s) selected` : '';
            statusDiv.innerHTML = '';
        }});

        dropArea.addEventListener('dragover', (e) => {{ e.preventDefault(); dropArea.classList.add('dragover'); }});
        dropArea.addEventListener('dragleave', () => {{ dropArea.classList.remove('dragover'); }});
        dropArea.addEventListener('drop', (e) => {{
            e.preventDefault();
            dropArea.classList.remove('dragover');
            selectedFiles = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/'));
            uploadBtn.disabled = selectedFiles.length === 0;
            fileCount.textContent = selectedFiles.length > 0 ? `${{selectedFiles.length}} file(s) selected` : '';
            statusDiv.innerHTML = '';
        }});

        async function uploadFiles() {{
            uploadBtn.disabled = true;
            let success = 0;
            let failed = 0;

            for (const file of selectedFiles) {{
                const pct = Math.round(((success + failed) / selectedFiles.length) * 100);
                statusDiv.innerHTML = `<div class="status progress">Uploading... (${{success + failed + 1}}/${{selectedFiles.length}})<div class="progress-bar"><div class="progress-bar-fill" style="width:${{pct}}%"></div></div></div>`;
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
                        headers: {{ 'Content-Type': file.type }},
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
                statusDiv.innerHTML = `<div class="status success">✅ ${{success}} file(s) uploaded successfully</div>`;
            }} else {{
                statusDiv.innerHTML = `<div class="status error">Done: ${{success}} succeeded / ${{failed}} failed</div>`;
            }}
            uploadBtn.disabled = false;
            selectedFiles = [];
            fileCount.textContent = '';
        }}
    </script>
</body>
</html>"""

    return _html_response(200, html)


def _html_response(status_code, html):
    """Return an HTML response."""
    return flask.make_response(
        html,
        status_code,
        {'Content-Type': 'text/html; charset=utf-8', 'Access-Control-Allow-Origin': '*'}
    )


# ============================================================
# POST /photos/share-upload (token-based, no auth required)
# ============================================================

def _share_upload(request):
    """Issue signed URL via token (for third-party users)."""
    if not ENABLE_SHARE_URL:
        return _err(403, 'Share URL feature is disabled')

    body = request.get_json(silent=True) or {}
    token = body.get('token', '')
    filename = body.get('filename', '')
    content_type = body.get('contentType', 'image/jpeg')

    if not token or not filename:
        return _err(400, 'token and filename are required')

    # Validate token
    query = db.collection(PHOTOS_COLLECTION).where(
        'photoId', '==', f'share_token:{token}'
    ).where('status', '==', 'active').limit(1)
    docs = list(query.stream())

    if not docs:
        return _err(403, 'Invalid or expired token')

    item = docs[0].to_dict()

    # Expiration check
    created_at = item.get('createdAt', '')
    expires_hours = int(item.get('expiresHours', 24))
    try:
        created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        if datetime.now(timezone.utc) > created_dt + timedelta(hours=expires_hours):
            return _err(403, 'Token has expired')
    except Exception:
        pass

    uid = item['userId']
    label_id = item.get('labelId', '')
    label_name = item.get('labelName', '')

    # Generate signed URL for upload
    photo_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    date_path = f"{now.year}/{now.month:02d}/{now.day:02d}"
    gcs_key = f"{_prefix(uid)}{date_path}/{photo_id}"

    url = _generate_signed_url(gcs_key, method='PUT', content_type=content_type)

    # Pre-create Firestore record with label
    doc_ref = db.collection(PHOTOS_COLLECTION).document(_doc_id(uid, photo_id))
    photo_data = {
        'userId': uid,
        'photoId': photo_id,
        'filename': filename,
        'contentType': content_type,
        'gcsKey': gcs_key,
        'status': 'uploading',
        'createdAt': now.isoformat(),
        'labels': [label_id] if label_id else [],
    }
    if label_id and label_name:
        photo_data['labelNames'] = {label_id: label_name}
    doc_ref.set(photo_data)

    return _ok(200, {
        'uploadUrl': url,
        'photoId': photo_id,
    })


# ============================================================
# Shares API
# ============================================================

def _create_share(request):
    """POST /shares — Create a label share request."""
    if not ENABLE_LABEL_SHARING:
        return _err(403, 'Label sharing feature is disabled')

    uid = _get_user_id(request)
    if not uid:
        return _err(401, 'Authentication required')

    body = request.get_json(silent=True) or {}
    to_username = body.get('toUsername', '').strip()
    label_id = body.get('labelId', '').strip()
    label_name = body.get('labelName', '').strip()

    if not to_username or not label_id:
        return _err(400, 'toUsername and labelId are required')

    # Find recipient user by email
    try:
        to_user = firebase_auth.get_user_by_email(to_username)
        to_uid = to_user.uid
    except firebase_auth.UserNotFoundError:
        return _err(404, 'User not found')
    except Exception as e:
        return _err(500, str(e))

    if to_uid == uid:
        return _err(400, 'Cannot share with yourself')

    # Create share record
    share_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    from_username = _get_user_email(uid)

    # Save as a record for the recipient user
    recipient_doc = db.collection(PHOTOS_COLLECTION).document(_doc_id(to_uid, f'share:{share_id}'))
    recipient_doc.set({
        'userId': to_uid,
        'photoId': f'share:{share_id}',
        'fromUser': uid,
        'fromUsername': from_username,
        'labelId': label_id,
        'labelName': label_name or label_id,
        'permission': body.get('permission', 'view'),
        'status': 'pending',
        'createdAt': now,
        'labels': [],
    })

    # Save a record for the sender (for managing sent shares)
    sender_doc = db.collection(PHOTOS_COLLECTION).document(_doc_id(uid, f'sent_share:{share_id}'))
    sender_doc.set({
        'userId': uid,
        'photoId': f'sent_share:{share_id}',
        'toUser': to_uid,
        'toUsername': to_username,
        'labelId': label_id,
        'labelName': label_name or label_id,
        'permission': body.get('permission', 'view'),
        'status': 'active',
        'createdAt': now,
        'labels': [],
    })

    return _ok(201, {
        'message': 'Share request created.',
        'shareId': share_id,
    })


def _pending_shares(request):
    """GET /shares/pending — List pending share requests received."""
    uid = _get_user_id(request)
    if not uid:
        return _err(401, 'Authentication required')

    query = db.collection(PHOTOS_COLLECTION).where('userId', '==', uid).where(
        'photoId', '>=', 'share:').where('photoId', '<', 'share:\uffff')
    docs = list(query.stream())

    shares = []
    for doc in docs:
        item = doc.to_dict()
        if item.get('status') != 'pending':
            continue
        shares.append({
            'shareId': item['photoId'].removeprefix('share:'),
            'fromUser': item.get('fromUsername', ''),
            'labelId': item.get('labelId', ''),
            'labelName': item.get('labelName', ''),
            'createdAt': item.get('createdAt', ''),
        })

    return _ok(200, {'shares': shares})


def _sent_shares(request):
    """GET /shares/sent — List shares sent by the current user."""
    uid = _get_user_id(request)
    if not uid:
        return _err(401, 'Authentication required')

    query = db.collection(PHOTOS_COLLECTION).where('userId', '==', uid).where(
        'photoId', '>=', 'sent_share:').where('photoId', '<', 'sent_share:\uffff')
    docs = list(query.stream())

    shares = []
    for doc in docs:
        item = doc.to_dict()
        shares.append({
            'shareId': item['photoId'].removeprefix('sent_share:'),
            'toUser': item.get('toUsername', ''),
            'labelId': item.get('labelId', ''),
            'labelName': item.get('labelName', ''),
            'status': item.get('status', ''),
            'createdAt': item.get('createdAt', ''),
        })

    return _ok(200, {'shares': shares})


def _list_shares(request):
    """GET /shares — List accepted shares (labels shared with you)."""
    uid = _get_user_id(request)
    if not uid:
        return _err(401, 'Authentication required')

    query = db.collection(PHOTOS_COLLECTION).where('userId', '==', uid).where(
        'photoId', '>=', 'share:').where('photoId', '<', 'share:\uffff')
    docs = list(query.stream())

    shares = []
    for doc in docs:
        item = doc.to_dict()
        if item.get('status') != 'accepted':
            continue
        shares.append({
            'shareId': item['photoId'].removeprefix('share:'),
            'fromUser': item.get('fromUsername', ''),
            'fromUserId': item.get('fromUser', ''),
            'labelId': item.get('labelId', ''),
            'labelName': item.get('labelName', ''),
            'createdAt': item.get('createdAt', ''),
        })

    return _ok(200, {'shares': shares})


def _accept_share(request, path):
    """POST /shares/{shareId}/accept — Accept a share."""
    uid = _get_user_id(request)
    if not uid:
        return _err(401, 'Authentication required')

    parts = path.strip('/').split('/')
    share_id = parts[1] if len(parts) >= 3 else ''

    doc_ref = db.collection(PHOTOS_COLLECTION).document(_doc_id(uid, f'share:{share_id}'))
    doc = doc_ref.get()
    if not doc.exists:
        return _err(404, 'Share not found')

    doc_ref.update({'status': 'accepted'})

    return _ok(200, {'message': 'Share accepted.'})


def _reject_share(request, path):
    """POST /shares/{shareId}/reject — Reject a share."""
    uid = _get_user_id(request)
    if not uid:
        return _err(401, 'Authentication required')

    parts = path.strip('/').split('/')
    share_id = parts[1] if len(parts) >= 3 else ''

    doc_ref = db.collection(PHOTOS_COLLECTION).document(_doc_id(uid, f'share:{share_id}'))
    doc_ref.delete()

    return _ok(200, {'message': 'Share rejected.'})


def _delete_share(request, path):
    """DELETE /shares/{shareId} — Remove a share (from either sender or receiver)."""
    uid = _get_user_id(request)
    if not uid:
        return _err(401, 'Authentication required')

    parts = path.strip('/').split('/')
    share_id = parts[1] if len(parts) >= 2 else ''

    # Try deleting as receiver (share:xxx)
    receiver_ref = db.collection(PHOTOS_COLLECTION).document(_doc_id(uid, f'share:{share_id}'))
    receiver_doc = receiver_ref.get()
    if receiver_doc.exists:
        from_uid = receiver_doc.to_dict().get('fromUser', '')
        receiver_ref.delete()
        # Also delete the sender's record
        if from_uid:
            sender_ref = db.collection(PHOTOS_COLLECTION).document(_doc_id(from_uid, f'sent_share:{share_id}'))
            sender_ref.delete()
        return _ok(200, {'message': 'Share removed.'})

    # Try deleting as sender (sent_share:xxx)
    sender_ref = db.collection(PHOTOS_COLLECTION).document(_doc_id(uid, f'sent_share:{share_id}'))
    sender_doc = sender_ref.get()
    if sender_doc.exists:
        to_uid = sender_doc.to_dict().get('toUser', '')
        sender_ref.delete()
        # Also delete the receiver's record
        if to_uid:
            receiver_ref = db.collection(PHOTOS_COLLECTION).document(_doc_id(to_uid, f'share:{share_id}'))
            receiver_ref.delete()
        return _ok(200, {'message': 'Share removed.'})

    return _err(404, 'Share not found')


# ============================================================
# POST /photos/share-download-url
# ============================================================

def _share_download_url(request):
    """Generate a download page URL for sharing photos by label."""
    if not ENABLE_SHARE_DOWNLOAD_URL:
        return _err(403, 'Share URL feature is disabled')
    uid = _get_user_id(request)
    if not uid:
        return _err(401, 'Authentication required')

    data = request.get_json(silent=True) or {}
    label_id = data.get('labelId', '')
    label_name = data.get('labelName', '')
    expires_hours = int(data.get('expiresHours', 72))

    if not label_id:
        return _err(400, 'labelId is required')

    # Count matching photos
    docs = _query_user_photos(uid)
    matching = [
        d for d in docs
        if d.get('status') != 'deleted'
        and d.get('status') != 'uploading'
        and not d.get('photoId', '').startswith('share_token:')
        and not d.get('photoId', '').startswith('share:')
        and not d.get('photoId', '').startswith('sent_share:')
        and not d.get('photoId', '').startswith('download_token:')
        and label_id in d.get('labels', [])
    ]

    if not matching:
        return _err(404, 'No photos found matching the criteria')

    token = str(uuid.uuid4())

    # Save token to Firestore
    db_client.collection('users').document(uid).collection('photos').document(f'download_token:{token}').set({
        'status': 'active',
        'createdAt': datetime.now(timezone.utc).isoformat(),
        'expiresHours': expires_hours,
        'labelId': label_id,
        'labelName': label_name,
        'photoCount': len(matching),
    })

    api_base = os.environ.get('FUNCTION_URL', request.url_root.rstrip('/'))
    page_url = f"{api_base}/download-page?token={token}"

    return _ok(200, {
        'downloadUrl': page_url,
        'token': token,
        'expiresHours': expires_hours,
        'photoCount': len(matching),
    })


# ============================================================
# GET /download-page?token=xxx
# ============================================================

def _download_page(request):
    """Render an HTML download page for shared photos."""
    if not ENABLE_SHARE_DOWNLOAD_URL:
        return _html_response(403, '<h1>This feature is disabled.</h1>')

    token = request.args.get('token', '')
    if not token:
        return _html_response(400, '<h1>Invalid link.</h1>')

    # Find token record by scanning all users (token is unique)
    from google.cloud.firestore_v1.base_query import FieldFilter
    users_ref = db_client.collection('users')
    token_doc = None
    uid = None

    for user_doc in users_ref.stream():
        doc_ref = users_ref.document(user_doc.id).collection('photos').document(f'download_token:{token}')
        doc = doc_ref.get()
        if doc.exists and doc.to_dict().get('status') == 'active':
            token_doc = doc.to_dict()
            uid = user_doc.id
            break

    if not token_doc or not uid:
        return _html_response(404, '<h1>This link has expired or is invalid.</h1>')

    label_id = token_doc.get('labelId', '')
    label_name = token_doc.get('labelName', label_id)
    expires_hours = int(token_doc.get('expiresHours', 72))

    # Check expiration
    created_at = token_doc.get('createdAt', '')
    if created_at:
        created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        if datetime.now(timezone.utc) > created_dt + timedelta(hours=expires_hours):
            return _html_response(410, '<h1>This link has expired.</h1>')

    # Get photos with label
    docs = _query_user_photos(uid)
    photos = [
        d for d in docs
        if d.get('status') != 'deleted'
        and d.get('status') != 'uploading'
        and not d.get('photoId', '').startswith('share_token:')
        and not d.get('photoId', '').startswith('share:')
        and not d.get('photoId', '').startswith('download_token:')
        and label_id in d.get('labels', [])
    ]
    photos.sort(key=lambda p: p.get('createdAt', ''), reverse=True)

    # Generate signed URLs
    photo_entries = []
    for photo in photos:
        gcs_key = photo.get('gcsKey', f"users/{uid}/{photo.get('photoId', '')}")
        thumb_key = photo.get('thumbnailKey', gcs_key)
        try:
            thumb_url = _generate_signed_url(thumb_key)
            full_url = _generate_signed_url(gcs_key)
        except Exception:
            continue
        photo_entries.append({
            'filename': photo.get('filename', photo.get('photoId', '')),
            'thumbUrl': thumb_url,
            'fullUrl': full_url,
        })

    import json as json_mod
    photos_json = json_mod.dumps([{'filename': e['filename'], 'fullUrl': e['fullUrl']} for e in photo_entries])

    photo_grid = ''
    for entry in photo_entries:
        photo_grid += f'''
        <div class="photo-card">
            <a href="{entry['fullUrl']}" target="_blank" download="{entry['filename']}">
                <img src="{entry['thumbUrl']}" alt="{entry['filename']}" loading="lazy" />
            </a>
            <div class="photo-name">{entry['filename']}</div>
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

    return _html_response(200, html)


# ============================================================
# Utility: Get Firebase API Key
# ============================================================

def _get_firebase_api_key():
    """
    Retrieve the Firebase Web API key.
    In production, set FIREBASE_API_KEY as an environment variable.
    This fallback attempts to read it from the Firebase project config.
    """
    # Check environment variable first
    key = os.environ.get('FIREBASE_API_KEY', '')
    if key:
        return key

    # Fallback: try to get from Google Cloud resource manager
    try:
        import requests as http_requests
        import google.auth
        import google.auth.transport.requests

        creds, project = google.auth.default()
        auth_req = google.auth.transport.requests.Request()
        creds.refresh(auth_req)

        # Get browser key from Firebase project
        url = f'https://firebase.googleapis.com/v1beta1/projects/{project or GCP_PROJECT}/webApps'
        headers = {'Authorization': f'Bearer {creds.token}'}
        resp = http_requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            apps = resp.json().get('apps', [])
            if apps:
                app_id = apps[0].get('appId', '')
                config_url = f'https://firebase.googleapis.com/v1beta1/projects/{project or GCP_PROJECT}/webApps/{app_id}/config'
                config_resp = http_requests.get(config_url, headers=headers, timeout=10)
                if config_resp.status_code == 200:
                    return config_resp.json().get('apiKey', '')
    except Exception:
        pass

    return ''


# ============================================================
# Cloud Storage Event Trigger (thumbnail + EXIF)
# ============================================================

THUMBNAIL_SIZE = (200, 200)
