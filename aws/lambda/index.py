"""
Daily Cloud Photo — Unified Lambda Handler
Routes requests from API Gateway HTTP API based on path
"""
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key

# ── Environment Variables ──
USER_POOL_ID = os.environ.get('USER_POOL_ID', '')
USER_POOL_CLIENT_ID = os.environ.get('USER_POOL_CLIENT_ID', '')
PHOTOS_BUCKET = os.environ.get('PHOTOS_BUCKET', '')
PHOTOS_TABLE = os.environ.get('PHOTOS_TABLE', '')
REQUIRE_EMAIL = os.environ.get('REQUIRE_EMAIL', 'true') == 'true'
REQUIRE_PHONE = os.environ.get('REQUIRE_PHONE', 'false') == 'true'
ENABLE_SHARE_URL = os.environ.get('ENABLE_SHARE_URL', 'true') == 'true'
ENABLE_SHARE_DOWNLOAD_URL = os.environ.get('ENABLE_SHARE_DOWNLOAD_URL', 'true') == 'true'
ENABLE_LABEL_SHARING = os.environ.get('ENABLE_LABEL_SHARING', 'true') == 'true'
APP_DISPLAY_NAME = os.environ.get('APP_DISPLAY_NAME', 'Daily Cloud Photo Backend')
AWS_REGION = os.environ.get('AWS_REGION', 'ap-northeast-1')

# ── AWS Clients ──
from botocore.config import Config as BotoConfig

cognito = boto3.client('cognito-idp')
s3 = boto3.client(
    's3',
    region_name=AWS_REGION,
    endpoint_url=f'https://s3.{AWS_REGION}.amazonaws.com',
    config=BotoConfig(signature_version='s3v4'),
)
dynamodb = boto3.resource('dynamodb')


# ============================================================
# Helpers
# ============================================================

def _table():
    return dynamodb.Table(PHOTOS_TABLE)


def _body(event):
    b = event.get('body', '{}')
    return json.loads(b) if isinstance(b, str) and b else (b or {})


def _ok(status, body):
    return {
        'statusCode': status,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
        },
        'body': json.dumps(body, default=str),
    }


def _err(status, msg, code=None):
    b = {'message': msg}
    if code:
        b['error'] = code
    return _ok(status, b)


def _user_id(event):
    headers = event.get('headers', {})
    auth = headers.get('authorization', '') or headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return None
    try:
        resp = cognito.get_user(AccessToken=auth[7:])
        for a in resp.get('UserAttributes', []):
            if a['Name'] == 'sub':
                return a['Value']
    except Exception:
        return None
    return None


def _prefix(uid):
    return f'users/{uid}/'


# ============================================================
# Routing
# ============================================================

def handler(event, context):
    rc = event.get('requestContext', {})
    http = rc.get('http', {})
    method = http.get('method', 'GET').upper()
    path = http.get('path', '/')

    # Strip /v1 prefix
    if path.startswith('/v1'):
        path = path[3:]

    if method == 'GET' and path == '/info':
        return _info(event)
    if method == 'POST' and path == '/auth/signup':
        return _signup(event)
    if method == 'POST' and path == '/auth/confirm':
        return _confirm(event)
    if method == 'POST' and path == '/auth/signin':
        return _signin(event)
    if method == 'POST' and path == '/auth/refresh':
        return _refresh(event)
    if method == 'POST' and path == '/auth/forgot-password':
        return _forgot_password(event)
    if method == 'POST' and path == '/auth/reset-password':
        return _reset_password(event)
    if method == 'GET' and path == '/photos':
        return _photos_list(event)
    if method == 'GET' and path.startswith('/photos/') and '/confirm' not in path and '/labels' not in path and '/share' not in path:
        return _photos_get_one(event, path)
    if method == 'POST' and path == '/photos/upload-url':
        return _upload_url(event)
    if method == 'POST' and path.startswith('/photos/') and path.endswith('/confirm'):
        return _photos_confirm(event, path)
    if method == 'PUT' and path.startswith('/photos/') and path.endswith('/labels'):
        return _photos_update_labels(event, path)
    if method == 'DELETE' and path.startswith('/photos/'):
        return _photos_delete(event, path)
    if method == 'POST' and path == '/photos/share-upload-url':
        return _share_upload_url(event)
    if method == 'GET' and path == '/upload-page':
        return _upload_page(event)
    if method == 'POST' and path == '/photos/share-upload':
        return _share_upload(event)
    if method == 'POST' and path == '/photos/share-download-url':
        return _share_download_url(event)
    if method == 'GET' and path == '/download-page':
        return _download_page(event)
    if method == 'POST' and path == '/shares':
        return _create_share(event)
    if method == 'GET' and path == '/shares/pending':
        return _pending_shares(event)
    if method == 'GET' and path == '/shares/sent':
        return _sent_shares(event)
    if method == 'GET' and path == '/shares':
        return _list_shares(event)
    if method == 'POST' and path.startswith('/shares/') and path.endswith('/accept'):
        return _accept_share(event, path)
    if method == 'POST' and path.startswith('/shares/') and path.endswith('/reject'):
        return _reject_share(event, path)
    if method == 'DELETE' and path.startswith('/shares/'):
        return _delete_share(event, path)

    return _err(404, 'Not found')


# ============================================================
# GET /info
# ============================================================

def _info(event):
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

def _signup(event):
    b = _body(event)
    username = b.get('username', '').strip()
    password = b.get('password', '')
    email = b.get('email', '').strip()
    phone = b.get('phone', '').strip()

    if not username or not password:
        return _err(400, 'username and password are required')

    attrs = []
    if email:
        attrs.append({'Name': 'email', 'Value': email})
    if phone:
        attrs.append({'Name': 'phone_number', 'Value': phone})

    try:
        resp = cognito.sign_up(
            ClientId=USER_POOL_CLIENT_ID,
            Username=username,
            Password=password,
            UserAttributes=attrs,
        )
        return _ok(201, {
            'message': 'User created. Confirmation may be required.',
            'confirmationRequired': not resp.get('UserConfirmed', False),
        })
    except cognito.exceptions.UsernameExistsException:
        return _err(409, 'Username already exists', 'UsernameExists')
    except cognito.exceptions.InvalidPasswordException as e:
        return _err(400, str(e), 'InvalidPassword')
    except cognito.exceptions.InvalidParameterException as e:
        return _err(400, str(e), 'InvalidParameter')
    except Exception as e:
        return _err(500, str(e))


# ============================================================
# POST /auth/confirm
# ============================================================

def _confirm(event):
    b = _body(event)
    username = b.get('username', '').strip()
    code = b.get('confirmationCode', '').strip()

    if not username or not code:
        return _err(400, 'username and confirmationCode are required')

    try:
        cognito.confirm_sign_up(
            ClientId=USER_POOL_CLIENT_ID,
            Username=username,
            ConfirmationCode=code,
        )
        return _ok(200, {'message': 'User confirmed.'})
    except cognito.exceptions.CodeMismatchException:
        return _err(400, 'Invalid confirmation code', 'CodeMismatch')
    except cognito.exceptions.ExpiredCodeException:
        return _err(400, 'Confirmation code expired', 'ExpiredCode')
    except cognito.exceptions.UserNotFoundException:
        return _err(404, 'User not found', 'UserNotFound')
    except Exception as e:
        return _err(500, str(e))


# ============================================================
# POST /auth/signin
# ============================================================

def _signin(event):
    b = _body(event)
    username = b.get('username', '').strip()
    password = b.get('password', '')

    if not username or not password:
        return _err(400, 'username and password are required')

    try:
        resp = cognito.initiate_auth(
            ClientId=USER_POOL_CLIENT_ID,
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters={
                'USERNAME': username,
                'PASSWORD': password,
            },
        )
        r = resp['AuthenticationResult']
        return _ok(200, {
            'accessToken': r['AccessToken'],
            'refreshToken': r.get('RefreshToken', ''),
            'expiresIn': r.get('ExpiresIn', 3600),
        })
    except cognito.exceptions.NotAuthorizedException:
        return _err(401, 'Incorrect username or password', 'NotAuthorized')
    except cognito.exceptions.UserNotConfirmedException:
        return _err(403, 'User is not confirmed', 'UserNotConfirmed')
    except cognito.exceptions.UserNotFoundException:
        return _err(404, 'User not found', 'UserNotFound')
    except Exception as e:
        return _err(500, str(e))


# ============================================================
# POST /auth/refresh
# ============================================================

def _refresh(event):
    b = _body(event)
    rt = b.get('refreshToken', '')

    if not rt:
        return _err(400, 'refreshToken is required')

    try:
        resp = cognito.initiate_auth(
            ClientId=USER_POOL_CLIENT_ID,
            AuthFlow='REFRESH_TOKEN_AUTH',
            AuthParameters={'REFRESH_TOKEN': rt},
        )
        r = resp['AuthenticationResult']
        return _ok(200, {
            'accessToken': r['AccessToken'],
            'expiresIn': r.get('ExpiresIn', 3600),
        })
    except cognito.exceptions.NotAuthorizedException:
        return _err(401, 'Refresh token is invalid or expired', 'NotAuthorized')
    except Exception as e:
        return _err(500, str(e))


# ============================================================
# POST /auth/forgot-password
# ============================================================

def _forgot_password(event):
    b = _body(event)
    username = b.get('username', '').strip()

    if not username:
        return _err(400, 'username is required')

    try:
        cognito.forgot_password(
            ClientId=USER_POOL_CLIENT_ID,
            Username=username,
        )
        return _ok(200, {'message': 'Confirmation code sent.'})
    except cognito.exceptions.UserNotFoundException:
        # For security, return the same response even if user does not exist
        return _ok(200, {'message': 'Confirmation code sent.'})
    except cognito.exceptions.LimitExceededException:
        return _err(429, 'Too many requests. Please try again later.', 'LimitExceeded')
    except Exception as e:
        return _err(500, str(e))


# ============================================================
# POST /auth/reset-password
# ============================================================

def _reset_password(event):
    b = _body(event)
    username = b.get('username', '').strip()
    code = b.get('confirmationCode', '').strip()
    new_password = b.get('newPassword', '')

    if not username or not code or not new_password:
        return _err(400, 'username, confirmationCode, and newPassword are required')

    try:
        cognito.confirm_forgot_password(
            ClientId=USER_POOL_CLIENT_ID,
            Username=username,
            ConfirmationCode=code,
            Password=new_password,
        )
        return _ok(200, {'message': 'Password reset successful.'})
    except cognito.exceptions.CodeMismatchException:
        return _err(400, 'Invalid confirmation code', 'CodeMismatch')
    except cognito.exceptions.ExpiredCodeException:
        return _err(400, 'Confirmation code expired', 'ExpiredCode')
    except cognito.exceptions.InvalidPasswordException as e:
        return _err(400, str(e), 'InvalidPassword')
    except Exception as e:
        return _err(500, str(e))


# ============================================================
# GET /photos
# ============================================================

def _photos_list(event):
    uid = _user_id(event)
    if not uid:
        return _err(401, 'Authentication required')

    params = event.get('queryStringParameters') or {}
    limit = int(params.get('limit', '100'))
    cursor = params.get('cursor')

    t = _table()
    qp = {
        'KeyConditionExpression': Key('userId').eq(uid),
        'Limit': limit,
        'ScanIndexForward': False,
    }
    if cursor:
        qp['ExclusiveStartKey'] = {'userId': uid, 'photoId': cursor}

    result = t.query(**qp)
    items = result.get('Items', [])

    photos = []
    for item in items:
        # Exclude deleted items
        if item.get('status') == 'deleted':
            continue
        # Exclude incomplete uploads (uploading with no size)
        if item.get('status') == 'uploading':
            continue
        # Exclude share_token records (not photos)
        if item.get('photoId', '').startswith('share_token:'):
            continue
        # Exclude share: records (sharing metadata)
        if item.get('photoId', '').startswith('share:'):
            continue
        # Exclude sent_share: records (sent sharing metadata)
        if item.get('photoId', '').startswith('sent_share:'):
            continue

        # Return thumbnail URL if thumbnail key exists
        thumbnail_key = item.get('thumbnailKey')
        s3_key = item.get('s3Key', f"{_prefix(uid)}{item['photoId']}")

        thumbnail_url = None
        full_url = None

        try:
            # Full-size URL
            full_url = s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': PHOTOS_BUCKET, 'Key': s3_key},
                ExpiresIn=3600,
            )
            # Thumbnail URL
            if thumbnail_key:
                thumbnail_url = s3.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': PHOTOS_BUCKET, 'Key': thumbnail_key},
                    ExpiresIn=3600,
                )
            else:
                thumbnail_url = full_url
        except Exception:
            pass

        photos.append({
            'id': item['photoId'],
            'filename': item.get('filename', ''),
            'contentType': item.get('contentType', 'image/jpeg'),
            'size': int(item.get('size', 0)),
            'createdAt': item.get('createdAt', ''),
            'thumbnailUrl': thumbnail_url,
            'fullUrl': full_url,
            'labels': item.get('labels', []),
            'labelNames': item.get('labelNames', {}),
        })

    nc = None
    lk = result.get('LastEvaluatedKey')
    if lk:
        nc = lk.get('photoId')

    # Also include shared photos
    from boto3.dynamodb.conditions import Attr
    print(f'[Shares] Querying shared items for user {uid}')
    shared_result = t.query(
        KeyConditionExpression=Key('userId').eq(uid) & Key('photoId').begins_with('share:'),
        FilterExpression=Attr('status').eq('accepted'),
    )
    print(f'[Shares] Found {len(shared_result.get("Items", []))} accepted shares')
    for share_item in shared_result.get('Items', []):
        from_uid = share_item.get('fromUser', '')
        label_id = share_item.get('labelId', '')
        print(f'[Shares] Processing share: from={from_uid}, label={label_id}')
        if not from_uid or not label_id:
            continue

        # Get photos from the sharing user that have the specified label
        shared_photos = t.query(
            KeyConditionExpression=Key('userId').eq(from_uid),
        )
        print(f'[Shares] Found {len(shared_photos.get("Items", []))} photos from {from_uid}')
        for sp in shared_photos.get('Items', []):
            if sp.get('photoId', '').startswith('share_token:'):
                continue
            if sp.get('photoId', '').startswith('share:'):
                continue
            if sp.get('status') == 'deleted':
                continue
            sp_labels = sp.get('labels', [])
            if label_id not in sp_labels:
                continue
            print(f'[Shares] Matched photo {sp["photoId"]} with labels {sp_labels}')

            sp_s3_key = sp.get('s3Key', f"users/{from_uid}/{sp['photoId']}")
            sp_thumb_key = sp.get('thumbnailKey')
            try:
                sp_thumb_url = s3.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': PHOTOS_BUCKET, 'Key': sp_thumb_key or sp_s3_key},
                    ExpiresIn=3600,
                )
                sp_full_url = s3.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': PHOTOS_BUCKET, 'Key': sp_s3_key},
                    ExpiresIn=3600,
                )
            except Exception:
                sp_thumb_url = None
                sp_full_url = None

            photos.append({
                'id': sp['photoId'],
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

    return _ok(200, {'photos': photos, 'nextCursor': nc})


# ============================================================
# POST /photos/upload-url
# ============================================================

def _upload_url(event):
    uid = _user_id(event)
    if not uid:
        return _err(401, 'Authentication required')

    b = _body(event)
    filename = b.get('filename', '')
    ct = b.get('contentType', 'image/jpeg')
    created_at = b.get('createdAt', datetime.now(timezone.utc).isoformat())
    # Use photoId from the app if provided (overwrite on re-upload)
    photo_id = b.get('photoId', '') or str(uuid.uuid4())

    if not filename:
        return _err(400, 'filename is required')

    # Include date in S3 path: users/{sub}/2026/04/26/{photoId}
    try:
        dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
    except Exception:
        dt = datetime.now(timezone.utc)
    date_path = f"{dt.year}/{dt.month:02d}/{dt.day:02d}"
    s3_key = f"{_prefix(uid)}{date_path}/{photo_id}"

    url = s3.generate_presigned_url(
        'put_object',
        Params={'Bucket': PHOTOS_BUCKET, 'Key': s3_key, 'ContentType': ct},
        ExpiresIn=3600,
    )

    _table().put_item(Item={
        'userId': uid,
        'photoId': photo_id,
        'filename': filename,
        'contentType': ct,
        's3Key': s3_key,
        'status': 'uploading',
        'createdAt': created_at,
        'labels': [],
    })

    return _ok(200, {
        'photoId': photo_id,
        'uploadUrl': url,
        'expiresIn': 3600,
    })


# ============================================================
# POST /photos/{photoId}/confirm
# ============================================================

def _photos_confirm(event, path):
    uid = _user_id(event)
    if not uid:
        return _err(401, 'Authentication required')

    # Extract ID from /photos/{photoId}/confirm
    parts = path.strip('/').split('/')
    photo_id = parts[1] if len(parts) >= 3 else ''
    if not photo_id:
        return _err(400, 'photoId is required')

    t = _table()
    result = t.get_item(Key={'userId': uid, 'photoId': photo_id})
    item = result.get('Item')
    if not item:
        return _err(404, 'Photo not found')

    s3_key = item.get('s3Key', '')
    try:
        obj = s3.head_object(Bucket=PHOTOS_BUCKET, Key=s3_key)
        size = obj.get('ContentLength', 0)
    except Exception:
        return _err(404, 'File not found in storage')

    t.update_item(
        Key={'userId': uid, 'photoId': photo_id},
        UpdateExpression='SET #s = :status, #sz = :size',
        ExpressionAttributeNames={'#s': 'status', '#sz': 'size'},
        ExpressionAttributeValues={':status': 'uploaded', ':size': size},
    )

    thumb = s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': PHOTOS_BUCKET, 'Key': s3_key},
        ExpiresIn=3600,
    )

    return _ok(200, {'message': 'Upload confirmed.', 'thumbnailUrl': thumb})


# ============================================================
# GET /photos/{photoId}
# ============================================================

def _photos_get_one(event, path):
    uid = _user_id(event)
    if not uid:
        return _err(401, 'Authentication required')

    photo_id = path.split('/photos/')[-1]
    if not photo_id:
        return _err(400, 'photoId is required')

    t = _table()
    result = t.get_item(Key={'userId': uid, 'photoId': photo_id})
    item = result.get('Item')
    if not item:
        return _err(404, 'Photo not found')

    s3_key = item.get('s3Key', f"{_prefix(uid)}{photo_id}")
    thumbnail_key = item.get('thumbnailKey')

    full_url = None
    thumbnail_url = None
    try:
        full_url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': PHOTOS_BUCKET, 'Key': s3_key},
            ExpiresIn=3600,
        )
        if thumbnail_key:
            thumbnail_url = s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': PHOTOS_BUCKET, 'Key': thumbnail_key},
                ExpiresIn=3600,
            )
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
# DELETE /photos/{photoId}
# ============================================================

def _photos_delete(event, path):
    uid = _user_id(event)
    if not uid:
        return _err(401, 'Authentication required')

    parts = path.strip('/').split('/')
    photo_id = parts[1] if len(parts) >= 2 else ''
    if not photo_id:
        return _err(400, 'photoId is required')

    t = _table()
    result = t.get_item(Key={'userId': uid, 'photoId': photo_id})
    item = result.get('Item')
    if not item:
        return _err(404, 'Photo not found')

    # Soft delete: change status to deleted (S3 data retained via versioning)
    t.update_item(
        Key={'userId': uid, 'photoId': photo_id},
        UpdateExpression='SET #s = :status, deletedAt = :now',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={
            ':status': 'deleted',
            ':now': datetime.now(timezone.utc).isoformat(),
        },
    )

    return _ok(200, {'message': 'Photo deleted.'})


# ============================================================
# PUT /photos/{photoId}/labels
# ============================================================

def _photos_update_labels(event, path):
    uid = _user_id(event)
    if not uid:
        return _err(401, 'Authentication required')

    # Extract ID from /photos/{photoId}/labels
    parts = path.strip('/').split('/')
    photo_id = parts[1] if len(parts) >= 3 else ''
    if not photo_id:
        return _err(400, 'photoId is required')

    b = _body(event)
    labels = b.get('labels', [])
    label_names = b.get('labelNames', {})  # Optional: {labelId: displayName}

    if not isinstance(labels, list):
        return _err(400, 'labels must be an array')

    t = _table()
    result = t.get_item(Key={'userId': uid, 'photoId': photo_id})
    item = result.get('Item')
    if not item:
        return _err(404, 'Photo not found')

    update_expr = 'SET labels = :labels'
    expr_values = {':labels': labels}

    # Save label names mapping if provided
    if label_names and isinstance(label_names, dict):
        update_expr += ', labelNames = :labelNames'
        expr_values[':labelNames'] = label_names

    t.update_item(
        Key={'userId': uid, 'photoId': photo_id},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values,
    )

    return _ok(200, {'message': 'Labels updated.', 'labels': labels})


# ============================================================
# POST /photos/share-upload-url
# ============================================================

def _share_upload_url(event):
    if not ENABLE_SHARE_URL:
        return _err(403, 'Share URL feature is disabled')
    uid = _user_id(event)
    if not uid:
        return _err(401, 'Authentication required')

    b = _body(event)
    expires_hours = int(b.get('expiresHours', 24))
    label_id = b.get('labelId', '') or ''
    label_name = b.get('labelName', '') or ''

    # Generate token
    token = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc).isoformat()

    # Save token to DynamoDB (special record in photos table instead of share_tokens table)
    token_item = {
        'userId': uid,
        'photoId': f'share_token:{token}',
        'status': 'active',
        'createdAt': datetime.now(timezone.utc).isoformat(),
        'expiresHours': expires_hours,
        'labels': [],
    }
    if label_id:
        token_item['labelId'] = label_id
        token_item['labelName'] = label_name

    _table().put_item(Item=token_item)

    # Generate upload page URL
    rc = event.get('requestContext', {})
    domain = rc.get('domainName', '')
    stage = rc.get('stage', 'v1')
    page_url = f"https://{domain}/{stage}/upload-page?token={token}"

    return _ok(200, {
        'shareUrl': page_url,
        'token': token,
        'expiresHours': expires_hours,
    })


# ============================================================
# GET /upload-page?token=xxx
# ============================================================

def _upload_page(event):
    if not ENABLE_SHARE_URL:
        return _html_response(403, '<h1>This feature is disabled.</h1>')
    params = event.get('queryStringParameters') or {}
    token = params.get('token', '')

    if not token:
        return _html_response(400, '<h1>Invalid link</h1>')

    # Validate token (fast lookup via GSI query)
    t = _table()
    result = t.query(
        IndexName='photoId-index',
        KeyConditionExpression=Key('photoId').eq(f'share_token:{token}'),
    )
    items = [i for i in result.get('Items', []) if i.get('status') == 'active']

    if not items:
        return _html_response(403, '<h1>This link has expired or is invalid.</h1>')

    item = items[0]

    # Expiration check
    created_at = item.get('createdAt', '')
    expires_hours = int(item.get('expiresHours', 24))
    try:
        created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        if datetime.now(timezone.utc) > created_dt + timedelta(hours=expires_hours):
            return _html_response(403, '<h1>This link has expired.</h1>')
    except Exception:
        pass

    uid = item['userId']

    # Generate presigned URL (generated individually per upload to support multiple files)
    rc = event.get('requestContext', {})
    domain = rc.get('domainName', '')
    stage = rc.get('stage', 'v1')
    api_base = f"https://{domain}/{stage}"

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
            <p>or <span class="browse">Browse files</span></p>
            <input type="file" id="fileInput" accept="image/*" multiple>
            <div id="fileCount" class="file-count"></div>
        </div>

        <button id="uploadBtn" onclick="uploadFiles()" disabled>Upload</button>
        <div id="status"></div>
    </div>

    <script>
        const token = '{token}';
        const userId = '{uid}';
        const bucket = '{PHOTOS_BUCKET}';
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
                    // Get presigned URL
                    const res = await fetch('{api_base}/photos/share-upload', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ token: token, filename: file.name, contentType: file.type }})
                    }});
                    const data = await res.json();
                    if (!data.uploadUrl) throw new Error('No upload URL');

                    // Upload to S3
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
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'text/html; charset=utf-8',
            'Access-Control-Allow-Origin': '*',
        },
        'body': html,
    }


# ============================================================
# POST /photos/share-upload (token-based, no auth required)
# ============================================================

def _share_upload(event):
    """Issue presigned URL via token (for third-party users)"""
    if not ENABLE_SHARE_URL:
        return _err(403, 'Share URL feature is disabled')
    b = _body(event)
    token = b.get('token', '')
    filename = b.get('filename', '')
    ct = b.get('contentType', 'image/jpeg')

    if not token or not filename:
        return _err(400, 'token and filename are required')

    # Validate token (fast lookup via GSI query)
    t = _table()
    result = t.query(
        IndexName='photoId-index',
        KeyConditionExpression=Key('photoId').eq(f'share_token:{token}'),
    )
    items = [i for i in result.get('Items', []) if i.get('status') == 'active']

    if not items:
        return _err(403, 'Invalid or expired token')

    item = items[0]

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

    # Generate presigned URL
    photo_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    date_path = f"{now.year}/{now.month:02d}/{now.day:02d}"
    s3_key = f"{_prefix(uid)}{date_path}/{photo_id}"

    url = s3.generate_presigned_url(
        'put_object',
        Params={'Bucket': PHOTOS_BUCKET, 'Key': s3_key, 'ContentType': ct},
        ExpiresIn=3600,
    )

    # Pre-create DynamoDB record with label (S3 trigger will update size/status on confirm)
    photo_item = {
        'userId': uid,
        'photoId': photo_id,
        'filename': filename,
        'contentType': ct,
        's3Key': s3_key,
        'status': 'uploading',
        'createdAt': now.isoformat(),
        'labels': [label_id] if label_id else [],
    }
    if label_id and label_name:
        photo_item['labelNames'] = {label_id: label_name}
    t.put_item(Item=photo_item)

    return _ok(200, {
        'uploadUrl': url,
        'photoId': photo_id,
    })


# ============================================================
# Shares API
# ============================================================

def _create_share(event):
    """POST /shares — Create a share request"""
    if not ENABLE_LABEL_SHARING:
        return _err(403, 'Label sharing feature is disabled')
    uid = _user_id(event)
    if not uid:
        return _err(401, 'Authentication required')

    b = _body(event)
    to_username = b.get('toUsername', '').strip()  # Email address of the recipient
    label_id = b.get('labelId', '').strip()
    label_name = b.get('labelName', '').strip()

    if not to_username or not label_id:
        return _err(400, 'toUsername and labelId are required')

    # Get the recipient user's sub
    try:
        resp = cognito.admin_get_user(
            UserPoolId=USER_POOL_ID,
            Username=to_username,
        )
        to_uid = None
        for attr in resp.get('UserAttributes', []):
            if attr['Name'] == 'sub':
                to_uid = attr['Value']
                break
        if not to_uid:
            return _err(404, 'User not found')
    except cognito.exceptions.UserNotFoundException:
        return _err(404, 'User not found')
    except Exception as e:
        return _err(500, str(e))

    if to_uid == uid:
        return _err(400, 'Cannot share with yourself')

    # Create share record
    share_id = str(uuid.uuid4())
    t = _table()

    # Save as a record for the recipient user
    t.put_item(Item={
        'userId': to_uid,
        'photoId': f'share:{share_id}',
        'fromUser': uid,
        'fromUsername': _get_username(uid),
        'labelId': label_id,
        'labelName': label_name or label_id,
        'permission': b.get('permission', 'view'),
        'status': 'pending',
        'createdAt': datetime.now(timezone.utc).isoformat(),
        'labels': [],
    })

    # Also save a record for the sender (for managing sent shares)
    t.put_item(Item={
        'userId': uid,
        'photoId': f'sent_share:{share_id}',
        'toUser': to_uid,
        'toUsername': to_username,
        'labelId': label_id,
        'labelName': label_name or label_id,
        'permission': b.get('permission', 'view'),
        'status': 'active',
        'createdAt': datetime.now(timezone.utc).isoformat(),
        'labels': [],
    })

    return _ok(201, {
        'message': 'Share request created.',
        'shareId': share_id,
    })


def _pending_shares(event):
    """GET /shares/pending — List pending share requests"""
    uid = _user_id(event)
    if not uid:
        return _err(401, 'Authentication required')

    t = _table()
    from boto3.dynamodb.conditions import Key, Attr
    result = t.query(
        KeyConditionExpression=Key('userId').eq(uid) & Key('photoId').begins_with('share:'),
        FilterExpression=Attr('status').eq('pending'),
    )

    shares = []
    for item in result.get('Items', []):
        shares.append({
            'shareId': item['photoId'].removeprefix('share:'),
            'fromUser': item.get('fromUsername', ''),
            'labelId': item.get('labelId', ''),
            'labelName': item.get('labelName', ''),
            'createdAt': item.get('createdAt', ''),
        })

    return _ok(200, {'shares': shares})


def _sent_shares(event):
    """GET /shares/sent — List shares sent by the current user"""
    uid = _user_id(event)
    if not uid:
        return _err(401, 'Authentication required')

    t = _table()
    from boto3.dynamodb.conditions import Key
    result = t.query(
        KeyConditionExpression=Key('userId').eq(uid) & Key('photoId').begins_with('sent_share:'),
    )

    shares = []
    for item in result.get('Items', []):
        shares.append({
            'shareId': item['photoId'].removeprefix('sent_share:'),
            'toUser': item.get('toUsername', ''),
            'labelId': item.get('labelId', ''),
            'labelName': item.get('labelName', ''),
            'status': item.get('status', ''),
            'createdAt': item.get('createdAt', ''),
        })

    return _ok(200, {'shares': shares})


def _list_shares(event):
    """GET /shares — List accepted shares"""
    uid = _user_id(event)
    if not uid:
        return _err(401, 'Authentication required')

    t = _table()
    from boto3.dynamodb.conditions import Key, Attr
    result = t.query(
        KeyConditionExpression=Key('userId').eq(uid) & Key('photoId').begins_with('share:'),
        FilterExpression=Attr('status').eq('accepted'),
    )

    shares = []
    for item in result.get('Items', []):
        shares.append({
            'shareId': item['photoId'].removeprefix('share:'),
            'fromUser': item.get('fromUsername', ''),
            'fromUserId': item.get('fromUser', ''),
            'labelId': item.get('labelId', ''),
            'labelName': item.get('labelName', ''),
            'createdAt': item.get('createdAt', ''),
        })

    return _ok(200, {'shares': shares})


def _accept_share(event, path):
    """POST /shares/{shareId}/accept — Accept a share"""
    uid = _user_id(event)
    if not uid:
        return _err(401, 'Authentication required')

    parts = path.strip('/').split('/')
    share_id = parts[1] if len(parts) >= 3 else ''

    t = _table()
    key = {'userId': uid, 'photoId': f'share:{share_id}'}
    result = t.get_item(Key=key)
    if 'Item' not in result:
        return _err(404, 'Share not found')

    t.update_item(
        Key=key,
        UpdateExpression='SET #s = :status',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':status': 'accepted'},
    )

    return _ok(200, {'message': 'Share accepted.'})


def _reject_share(event, path):
    """POST /shares/{shareId}/reject — Reject a share"""
    uid = _user_id(event)
    if not uid:
        return _err(401, 'Authentication required')

    parts = path.strip('/').split('/')
    share_id = parts[1] if len(parts) >= 3 else ''

    t = _table()
    key = {'userId': uid, 'photoId': f'share:{share_id}'}
    t.delete_item(Key=key)

    return _ok(200, {'message': 'Share rejected.'})


def _delete_share(event, path):
    """DELETE /shares/{shareId} — Remove a share (from either sender or receiver)"""
    uid = _user_id(event)
    if not uid:
        return _err(401, 'Authentication required')

    parts = path.strip('/').split('/')
    share_id = parts[1] if len(parts) >= 2 else ''

    t = _table()

    # Delete as receiver (share:xxx)
    receiver_key = {'userId': uid, 'photoId': f'share:{share_id}'}
    receiver_result = t.get_item(Key=receiver_key)
    if 'Item' in receiver_result:
        from_uid = receiver_result['Item'].get('fromUser', '')
        t.delete_item(Key=receiver_key)
        # Also delete the sender's record
        if from_uid:
            t.delete_item(Key={'userId': from_uid, 'photoId': f'sent_share:{share_id}'})
        return _ok(200, {'message': 'Share removed.'})

    # Delete as sender (sent_share:xxx)
    sender_key = {'userId': uid, 'photoId': f'sent_share:{share_id}'}
    sender_result = t.get_item(Key=sender_key)
    if 'Item' in sender_result:
        to_uid = sender_result['Item'].get('toUser', '')
        t.delete_item(Key=sender_key)
        # Also delete the receiver's record
        if to_uid:
            t.delete_item(Key={'userId': to_uid, 'photoId': f'share:{share_id}'})
        return _ok(200, {'message': 'Share removed.'})

    return _err(404, 'Share not found')


def _get_username(uid):
    """Get email address from user ID"""
    try:
        resp = cognito.list_users(
            UserPoolId=USER_POOL_ID,
            Filter=f'sub = "{uid}"',
            Limit=1,
        )
        users = resp.get('Users', [])
        if users:
            for attr in users[0].get('Attributes', []):
                if attr['Name'] == 'email':
                    return attr['Value']
            return users[0].get('Username', uid)
    except Exception:
        pass
    return uid


# ============================================================
# POST /photos/share-download-url
# ============================================================

def _share_download_url(event):
    """Generate a download page URL for sharing photos by label."""
    if not ENABLE_SHARE_URL:
        return _err(403, 'Share URL feature is disabled')
    uid = _user_id(event)
    if not uid:
        return _err(401, 'Authentication required')

    b = _body(event)
    label_id = b.get('labelId', '')
    label_name = b.get('labelName', '')
    expires_hours = int(b.get('expiresHours', 72))
    date_from = b.get('dateFrom')
    date_to = b.get('dateTo')

    if not label_id:
        return _err(400, 'labelId is required')

    # Count matching photos
    from boto3.dynamodb.conditions import Key, Attr
    t = _table()

    # Paginate through all user's photos
    items = []
    query_params = {'KeyConditionExpression': Key('userId').eq(uid)}
    while True:
        resp = t.query(**query_params)
        items.extend(resp.get('Items', []))
        if 'LastEvaluatedKey' not in resp:
            break
        query_params['ExclusiveStartKey'] = resp['LastEvaluatedKey']

    matching = [
        item for item in items
        if item.get('status') != 'deleted'
        and item.get('status') != 'uploading'
        and not item.get('photoId', '').startswith('share_token:')
        and not item.get('photoId', '').startswith('share:')
        and not item.get('photoId', '').startswith('sent_share:')
        and not item.get('photoId', '').startswith('download_token:')
        and label_id in item.get('labels', [])
    ]

    # Optional date filter
    if date_from:
        matching = [m for m in matching if m.get('createdAt', '') >= date_from]
    if date_to:
        matching = [m for m in matching if m.get('createdAt', '') <= date_to]

    if not matching:
        return _err(404, 'No photos found matching the criteria')

    # Generate token
    token = str(uuid.uuid4())

    # Save token to DynamoDB
    t.put_item(Item={
        'userId': uid,
        'photoId': f'download_token:{token}',
        'status': 'active',
        'createdAt': datetime.now(timezone.utc).isoformat(),
        'expiresHours': expires_hours,
        'labelId': label_id,
        'labelName': label_name,
        'dateFrom': date_from or '',
        'dateTo': date_to or '',
        'photoCount': len(matching),
    })

    # Generate download page URL
    rc = event.get('requestContext', {})
    domain = rc.get('domainName', '')
    stage = rc.get('stage', 'v1')
    page_url = f"https://{domain}/{stage}/download-page?token={token}"

    return _ok(200, {
        'downloadUrl': page_url,
        'token': token,
        'expiresHours': expires_hours,
        'photoCount': len(matching),
    })


# ============================================================
# GET /download-page?token=xxx
# ============================================================

def _download_page(event):
    """Render an HTML download page for shared photos."""
    if not ENABLE_SHARE_URL:
        return _html_response(403, '<h1>This feature is disabled.</h1>')

    qs = event.get('queryStringParameters') or {}
    token = qs.get('token', '')

    if not token:
        return _html_response(400, '<h1>Invalid link.</h1>')

    # Find token record
    from boto3.dynamodb.conditions import Key, Attr
    t = _table()
    resp = t.query(
        IndexName='photoId-index',
        KeyConditionExpression=Key('photoId').eq(f'download_token:{token}'),
    )
    items = [i for i in resp.get('Items', []) if i.get('status') == 'active']
    if not items:
        return _html_response(404, '<h1>This link has expired or is invalid.</h1>')

    token_record = items[0]
    uid = token_record['userId']
    label_id = token_record.get('labelId', '')
    label_name = token_record.get('labelName', label_id)
    expires_hours = int(token_record.get('expiresHours', 72))
    date_from = token_record.get('dateFrom', '')
    date_to = token_record.get('dateTo', '')

    # Check expiration
    created_at = token_record.get('createdAt', '')
    if created_at:
        from datetime import timedelta
        created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        if datetime.now(timezone.utc) > created_dt + timedelta(hours=expires_hours):
            return _html_response(410, '<h1>This link has expired.</h1>')

    # Get photos with the specified label (paginate through all)
    all_items = []
    query_params = {'KeyConditionExpression': Key('userId').eq(uid)}
    while True:
        resp = t.query(**query_params)
        all_items.extend(resp.get('Items', []))
        if 'LastEvaluatedKey' not in resp:
            break
        query_params['ExclusiveStartKey'] = resp['LastEvaluatedKey']

    photos = [
        item for item in all_items
        if item.get('status') != 'deleted'
        and item.get('status') != 'uploading'
        and not item.get('photoId', '').startswith('share_token:')
        and not item.get('photoId', '').startswith('share:')
        and not item.get('photoId', '').startswith('sent_share:')
        and not item.get('photoId', '').startswith('download_token:')
        and label_id in item.get('labels', [])
    ]

    # Apply date filter
    if date_from:
        photos = [p for p in photos if p.get('createdAt', '') >= date_from]
    if date_to:
        photos = [p for p in photos if p.get('createdAt', '') <= date_to]

    # Sort by date descending
    photos.sort(key=lambda p: p.get('createdAt', ''), reverse=True)

    # Generate signed URLs for each photo
    photo_entries = []
    for photo in photos:
        s3_key = photo.get('s3Key', f"users/{uid}/{photo['photoId']}")
        thumb_key = photo.get('thumbnailKey', s3_key)
        try:
            thumb_url = s3.generate_presigned_url('get_object', Params={'Bucket': PHOTOS_BUCKET, 'Key': thumb_key}, ExpiresIn=3600)
            full_url = s3.generate_presigned_url('get_object', Params={'Bucket': PHOTOS_BUCKET, 'Key': s3_key}, ExpiresIn=3600)
        except Exception:
            continue
        photo_entries.append({
            'filename': photo.get('filename', photo['photoId']),
            'thumbUrl': thumb_url,
            'fullUrl': full_url,
            'createdAt': photo.get('createdAt', ''),
        })

    # Render HTML — matching upload page style with JSZip bulk download
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
        .progress {{ background: #ede9ff; color: #5c4db1; }}
        .success {{ background: #e8f5e9; color: #2e7d32; }}
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

        <div class="grid">
            {photo_grid}
        </div>
    </div>
    <script>
        const photos = {photos_json};
        async function downloadAll() {{
            const btn = document.getElementById('downloadAllBtn');
            const statusEl = document.getElementById('status');
            btn.disabled = true;
            statusEl.className = 'status progress';
            statusEl.textContent = 'Preparing download...';

            try {{
                const zip = new JSZip();
                for (let i = 0; i < photos.length; i++) {{
                    statusEl.textContent = `Downloading ${{i + 1}} / ${{photos.length}}...`;
                    const resp = await fetch(photos[i].fullUrl);
                    const blob = await resp.blob();
                    zip.file(photos[i].filename, blob);
                }}
                statusEl.textContent = 'Creating ZIP...';
                const content = await zip.generateAsync({{type: 'blob'}});
                const url = URL.createObjectURL(content);
                const a = document.createElement('a');
                a.href = url;
                a.download = '{label_name}.zip';
                a.click();
                URL.revokeObjectURL(url);
                statusEl.className = 'status success';
                statusEl.textContent = 'Download complete!';
            }} catch (e) {{
                statusEl.className = 'status';
                statusEl.style.background = '#ffebee';
                statusEl.style.color = '#c62828';
                statusEl.textContent = 'Error: ' + e.message;
            }}
            btn.disabled = false;
        }}
    </script>
</body>
</html>'''

    return _html_response(200, html)
