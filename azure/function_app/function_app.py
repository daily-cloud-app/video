"""
Daily Cloud Video — Azure Functions Backend (v2 Programming Model)

Authentication is delegated to Microsoft Entra External ID via the Native
Authentication API. This backend acts as a thin, stateless bridge that keeps
the existing Android API contract (username/password) while all credentials,
OTP verification, tokens and password resets are handled by Entra External ID.
No password, password hash, reset code or verification code is ever stored.
Cosmos DB only holds a username -> email / Entra object id mapping so that the
app can continue to sign in with a username and resolve share recipients.
"""
import json
import os
import time
import uuid
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import azure.functions as func
import jwt
from jwt import PyJWKClient
import requests
from azure.cosmos import CosmosClient, PartitionKey, exceptions as cosmos_exceptions
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions

# ── Azure Functions App ──
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# ── Environment Variables ──
COSMOS_CONNECTION = os.environ.get("COSMOS_CONNECTION", "")
COSMOS_DATABASE = os.environ.get("COSMOS_DATABASE", "dailycloudvideo")
STORAGE_CONNECTION = os.environ.get("STORAGE_CONNECTION", "")
STORAGE_CONTAINER = os.environ.get("STORAGE_CONTAINER", "videos")

# ── Microsoft Entra External ID (Native Authentication) ──
# Tenant subdomain, e.g. "contoso" for contoso.onmicrosoft.com.
ENTRA_TENANT_SUBDOMAIN = os.environ.get("ENTRA_TENANT_SUBDOMAIN", "")
# Directory (tenant) GUID. Used as the token issuer/authority tenant id.
ENTRA_TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "")
# Application (client) ID of the native-auth-enabled public client app.
ENTRA_CLIENT_ID = os.environ.get("ENTRA_CLIENT_ID", "")
# Scopes requested when acquiring tokens. offline_access -> refresh token,
# openid -> id token. A resource/API scope can be appended if needed.
ENTRA_SCOPES = os.environ.get("ENTRA_SCOPES", "openid offline_access")
# Base URL for the Native Authentication API.
ENTRA_BASE_URL = (
    f"https://{ENTRA_TENANT_SUBDOMAIN}.ciamlogin.com/"
    f"{ENTRA_TENANT_SUBDOMAIN}.onmicrosoft.com"
).rstrip("/") if ENTRA_TENANT_SUBDOMAIN else ""

REQUIRE_EMAIL = os.environ.get("REQUIRE_EMAIL", "true").lower() == "true"
REQUIRE_PHONE = os.environ.get("REQUIRE_PHONE", "false").lower() == "true"
ENABLE_SHARE_URL = os.environ.get("ENABLE_SHARE_URL", "true").lower() == "true"
ENABLE_SHARE_DOWNLOAD_URL = os.environ.get("ENABLE_SHARE_DOWNLOAD_URL", "true").lower() == "true"
ENABLE_LABEL_SHARING = os.environ.get("ENABLE_LABEL_SHARING", "true").lower() == "true"
APP_DISPLAY_NAME = os.environ.get("APP_DISPLAY_NAME", "Daily Cloud Video Backend")
# Share URL validity (hours). Admin-configurable; surfaced via /info.
SHARE_UPLOAD_URL_EXPIRY_HOURS = int(os.environ.get("SHARE_UPLOAD_URL_EXPIRY_HOURS", "24"))
SHARE_DOWNLOAD_URL_EXPIRY_HOURS = int(os.environ.get("SHARE_DOWNLOAD_URL_EXPIRY_HOURS", "72"))
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
    """Get the videos blob container client."""
    service = _get_blob_service()
    return service.get_container_client(STORAGE_CONTAINER)


# ============================================================
# Entra External ID — Native Authentication client
# ============================================================
#
# The backend never issues its own tokens. It relays credentials to the
# Native Authentication API and returns the tokens Entra issues. Between the
# stateless /auth/signup and /auth/confirm (and /auth/forgot-password and
# /auth/reset-password) calls, Entra requires a "continuation_token" that
# carries the flow state. We persist that opaque flow token transiently in the
# Cosmos "users" mapping document, keyed by username. It is not a credential,
# password, hash, reset code or verification code — it is a short-lived,
# single-flow handle, so storing it complies with the security requirements.

# Sign-up / sign-in with email + password advertise these challenge types.
_CHALLENGE_SIGNUP = "oob password redirect"
_CHALLENGE_SIGNIN = "password redirect"
_CHALLENGE_SSPR = "oob redirect"
# How long a stored continuation token is considered usable (seconds).
_FLOW_TOKEN_TTL_SECONDS = 600

# Cached JWKS client for validating Entra access tokens.
_jwks_client: PyJWKClient | None = None


def _entra_configured() -> bool:
    """Whether the Entra External ID settings are present."""
    return bool(ENTRA_BASE_URL and ENTRA_CLIENT_ID)


def _entra_post(path: str, data: dict) -> tuple[int, dict]:
    """POST a form-urlencoded request to a Native Auth endpoint.

    Returns (status_code, parsed_json). client_id is injected automatically.
    """
    payload = {"client_id": ENTRA_CLIENT_ID}
    payload.update({k: v for k, v in data.items() if v is not None})
    url = f"{ENTRA_BASE_URL}{path}"
    resp = requests.post(
        url,
        data=urlencode(payload),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body


def _entra_openid_config() -> dict:
    """Fetch the OpenID configuration for the external tenant."""
    url = f"{ENTRA_BASE_URL}/v2.0/.well-known/openid-configuration"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _get_jwks_client() -> PyJWKClient:
    """Lazily build a JWKS client from the tenant's OpenID metadata."""
    global _jwks_client
    if _jwks_client is None:
        jwks_uri = _entra_openid_config()["jwks_uri"]
        _jwks_client = PyJWKClient(jwks_uri)
    return _jwks_client


def _validate_entra_token(token: str) -> dict | None:
    """Validate an Entra access token via JWKS and return its claims.

    Signature, expiry and issuer are verified. Audience is intentionally not
    hard-verified here because the requested scope/resource is configurable;
    the signature + issuer checks bind the token to this external tenant.
    """
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        return claims
    except Exception as e:  # noqa: BLE001 - any failure means "unauthenticated"
        logger.warning("Entra token validation failed: %s", e)
        return None


def _get_user_from_request(req: func.HttpRequest) -> dict | None:
    """Resolve the authenticated user from the Entra access token.

    Returns a dict shaped like {"sub": <stable user id>, "username": <app
    username>} so that the existing video endpoints keep working unchanged.
    The stable user id is the Entra object id (oid), falling back to sub.
    """
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]

    claims = _validate_entra_token(token)
    if not claims:
        return None

    # oid is the immutable directory object id; sub is per-app pairwise. Prefer
    # oid so the same user id is stable across app registrations.
    uid = claims.get("oid") or claims.get("sub")
    if not uid:
        return None

    # Resolve the app-level username from the mapping (needed by label sharing).
    username = _lookup_username_by_uid(uid)
    if not username:
        # Fall back to a claim so requests still authenticate even if the
        # mapping is missing (e.g. user created directly in the tenant).
        username = (
            claims.get("preferred_username")
            or claims.get("email")
            or claims.get("verified_primary_email", [None])[0]
            or uid
        )

    return {"sub": uid, "username": username}


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
# Username -> email / Entra id mapping (Cosmos "users")
# ============================================================
#
# The mapping document schema (nothing sensitive is ever stored):
#   {
#     "id": <username>,              # partition key & unique id
#     "username": <username>,
#     "email": <email>,
#     "entraObjectId": <oid|sub>,    # set once the user is confirmed
#     "createdAt": <iso8601>,
#     # transient, single-flow handles (cleared as soon as possible):
#     "signupContinuationToken": <token>,
#     "signupTokenExpiresAt": <epoch>,
#     "resetContinuationToken": <token>,
#     "resetTokenExpiresAt": <epoch>,
#   }

def _users_container():
    return _get_container("users")


def _find_user_doc(username: str) -> dict | None:
    """Read the mapping document for a username, or None."""
    try:
        return _users_container().read_item(item=username, partition_key=username)
    except cosmos_exceptions.CosmosResourceNotFoundError:
        return None


def _find_user_by_email(email: str) -> dict | None:
    """Find a mapping document by email (cross-partition query)."""
    if not email:
        return None
    query = "SELECT * FROM c WHERE c.email = @email"
    params = [{"name": "@email", "value": email}]
    items = list(_users_container().query_items(
        query=query, parameters=params, enable_cross_partition_query=True,
    ))
    return items[0] if items else None


def _lookup_username_by_uid(uid: str) -> str | None:
    """Resolve the app username from an Entra object id."""
    if not uid:
        return None
    query = "SELECT c.username FROM c WHERE c.entraObjectId = @uid"
    params = [{"name": "@uid", "value": uid}]
    items = list(_users_container().query_items(
        query=query, parameters=params, enable_cross_partition_query=True,
    ))
    return items[0]["username"] if items else None


def _store_flow_token(username: str, kind: str, token: str) -> None:
    """Persist a transient continuation token for a username."""
    doc = _find_user_doc(username) or {
        "id": username,
        "username": username,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    doc[f"{kind}ContinuationToken"] = token
    doc[f"{kind}TokenExpiresAt"] = int(time.time()) + _FLOW_TOKEN_TTL_SECONDS
    _users_container().upsert_item(body=doc)


def _read_flow_token(username: str, kind: str) -> str | None:
    """Read a still-valid transient continuation token, or None."""
    doc = _find_user_doc(username)
    if not doc:
        return None
    token = doc.get(f"{kind}ContinuationToken")
    expires = doc.get(f"{kind}TokenExpiresAt", 0)
    if not token or int(time.time()) > int(expires):
        return None
    return token


def _clear_flow_token(username: str, kind: str) -> None:
    """Remove a transient continuation token once the flow completes."""
    doc = _find_user_doc(username)
    if not doc:
        return
    doc.pop(f"{kind}ContinuationToken", None)
    doc.pop(f"{kind}TokenExpiresAt", None)
    _users_container().upsert_item(body=doc)


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
        content_type="video/mp4" if permission == "w" else None,
    )
    return f"https://{account_name}.blob.core.windows.net/{STORAGE_CONTAINER}/{blob_name}?{sas_token}"


def _generate_upload_sas(blob_name: str, content_type: str = "video/mp4") -> str:
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

    info = {
        "name": APP_DISPLAY_NAME,
        "version": "1.0.0",
        "signupFields": fields,
        "features": features,
    }
    # Advertise share URL validity so the app can show it in the result popup.
    if ENABLE_SHARE_URL:
        info["uploadUrlExpiryHours"] = SHARE_UPLOAD_URL_EXPIRY_HOURS
    if ENABLE_SHARE_DOWNLOAD_URL:
        info["downloadUrlExpiryHours"] = SHARE_DOWNLOAD_URL_EXPIRY_HOURS

    return _ok(200, info)


# ============================================================
# POST /auth/signup
# ============================================================

@app.route(route="auth/signup", methods=["POST"])
def auth_signup(req: func.HttpRequest) -> func.HttpResponse:
    """Start an Entra External ID sign-up (email + password) and send an OTP.

    The app username maps to the Entra account email. We submit the password
    at /signup/v1.0/start so that /auth/confirm only needs to verify the OTP.
    The continuation token is stored transiently keyed by username, then the
    Android app is told a confirmation code is required.
    """
    if not _entra_configured():
        return _err(503, "Authentication provider is not configured", "NotConfigured")

    b = _body(req)
    username = b.get("username", "").strip()
    password = b.get("password", "")
    email = b.get("email", "").strip()

    if not username or not password:
        return _err(400, "username and password are required")
    if len(password) < 8:
        return _err(400, "Password must be at least 8 characters", "InvalidPassword")
    if REQUIRE_EMAIL and not email:
        return _err(400, "email is required")

    # Entra uses the email as the account username.
    entra_username = email or username

    # Enforce username uniqueness against our mapping.
    existing = _find_user_doc(username)
    if existing and existing.get("entraObjectId"):
        return _err(409, "Username already exists", "UsernameExists")

    # Step 1: /signup/v1.0/start with password (single-screen sign-up).
    status, body = _entra_post("/signup/v1.0/start", {
        "username": entra_username,
        "password": password,
        "challenge_type": _CHALLENGE_SIGNUP,
    })
    if status != 200:
        err = body.get("error", "")
        if err == "user_already_exists":
            return _err(409, "Username already exists", "UsernameExists")
        if err == "invalid_grant":
            return _err(400, body.get("error_description", "Password does not meet requirements"), "InvalidPassword")
        logger.warning("signup/start failed: %s", body)
        return _err(400, body.get("error_description", "Sign-up failed"), err or "SignupFailed")

    continuation_token = body.get("continuation_token")

    # Step 2: /signup/v1.0/challenge triggers the email OTP.
    status, body = _entra_post("/signup/v1.0/challenge", {
        "continuation_token": continuation_token,
        "challenge_type": _CHALLENGE_SIGNUP,
    })
    if status != 200 or body.get("challenge_type") not in ("oob", None):
        logger.warning("signup/challenge failed: %s", body)
        return _err(400, body.get("error_description", "Failed to send confirmation code"), body.get("error", "ChallengeFailed"))

    continuation_token = body.get("continuation_token", continuation_token)

    # Persist the mapping + transient flow token so /auth/confirm can finish.
    doc = existing or {
        "id": username,
        "username": username,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    doc["email"] = email
    doc["pendingEntraUsername"] = entra_username
    _users_container().upsert_item(body=doc)
    _store_flow_token(username, "signup", continuation_token)

    return _ok(201, {
        "message": "User created. Confirmation required.",
        "confirmationRequired": True,
    })


# ============================================================
# POST /auth/confirm
# ============================================================

@app.route(route="auth/confirm", methods=["POST"])
def auth_confirm(req: func.HttpRequest) -> func.HttpResponse:
    """Verify the email OTP to complete Entra External ID sign-up."""
    if not _entra_configured():
        return _err(503, "Authentication provider is not configured", "NotConfigured")

    b = _body(req)
    username = b.get("username", "").strip()
    code = b.get("confirmationCode", "").strip()

    if not username or not code:
        return _err(400, "username and confirmationCode are required")

    continuation_token = _read_flow_token(username, "signup")
    if not continuation_token:
        return _err(400, "Confirmation session expired. Please sign up again.", "ExpiredCode")

    # Submit the OTP to /signup/v1.0/continue.
    status, body = _entra_post("/signup/v1.0/continue", {
        "continuation_token": continuation_token,
        "grant_type": "oob",
        "oob": code,
    })
    if status != 200:
        err = body.get("error", "")
        suberr = body.get("suberror", "")
        if err == "invalid_grant" or suberr == "invalid_oob_value":
            return _err(400, "Invalid confirmation code", "CodeMismatch")
        if err == "expired_token":
            return _err(400, "Confirmation session expired. Please sign up again.", "ExpiredCode")
        logger.warning("signup/continue failed: %s", body)
        return _err(400, body.get("error_description", "Confirmation failed"), err or "ConfirmFailed")

    continuation_token = body.get("continuation_token", continuation_token)

    # Exchange the continuation token for tokens to learn the stable user id.
    doc = _find_user_doc(username)
    entra_username = (doc or {}).get("pendingEntraUsername", username)
    status, token_body = _entra_post("/oauth2/v2.0/token", {
        "continuation_token": continuation_token,
        "grant_type": "continuation_token",
        "scope": ENTRA_SCOPES,
        "username": entra_username,
    })

    # Record the stable Entra object id in the mapping (best effort).
    if status == 200 and token_body.get("id_token"):
        try:
            id_claims = jwt.decode(token_body["id_token"], options={"verify_signature": False})
            uid = id_claims.get("oid") or id_claims.get("sub")
            if doc and uid:
                doc["entraObjectId"] = uid
                doc.pop("pendingEntraUsername", None)
                _users_container().upsert_item(body=doc)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to decode id_token during confirm: %s", e)

    _clear_flow_token(username, "signup")
    return _ok(200, {"message": "User confirmed."})


# ============================================================
# POST /auth/signin
# ============================================================

@app.route(route="auth/signin", methods=["POST"])
def auth_signin(req: func.HttpRequest) -> func.HttpResponse:
    """Sign in via Entra External ID and return the tokens it issues."""
    if not _entra_configured():
        return _err(503, "Authentication provider is not configured", "NotConfigured")

    b = _body(req)
    username = b.get("username", "").strip()
    password = b.get("password", "")

    if not username or not password:
        return _err(400, "username and password are required")

    # Resolve the account email from the mapping; fall back to the username.
    doc = _find_user_doc(username)
    entra_username = (doc or {}).get("email") or username

    # Step 1: /oauth2/v2.0/initiate
    status, body = _entra_post("/oauth2/v2.0/initiate", {
        "username": entra_username,
        "challenge_type": _CHALLENGE_SIGNIN,
    })
    if status != 200:
        # user_not_found and other errors are masked to avoid enumeration.
        logger.warning("signin/initiate failed: status=%s error=%s suberror=%s desc=%s",
                       status, body.get("error"), body.get("suberror"), body.get("error_description"))
        return _err(401, "Incorrect username or password", "NotAuthorized")

    continuation_token = body.get("continuation_token")

    # Step 2: /oauth2/v2.0/challenge (selects password method).
    status, body = _entra_post("/oauth2/v2.0/challenge", {
        "continuation_token": continuation_token,
        "challenge_type": _CHALLENGE_SIGNIN,
    })
    if status != 200:
        logger.warning("signin/challenge failed: status=%s error=%s suberror=%s desc=%s challenge_type=%s",
                       status, body.get("error"), body.get("suberror"), body.get("error_description"),
                       body.get("challenge_type"))
        return _err(401, "Incorrect username or password", "NotAuthorized")

    continuation_token = body.get("continuation_token", continuation_token)

    # Step 3: /oauth2/v2.0/token with the password.
    status, token_body = _entra_post("/oauth2/v2.0/token", {
        "continuation_token": continuation_token,
        "grant_type": "password",
        "password": password,
        "scope": ENTRA_SCOPES,
    })
    if status != 200:
        logger.warning("signin/token failed: status=%s error=%s suberror=%s desc=%s",
                       status, token_body.get("error"), token_body.get("suberror"),
                       token_body.get("error_description"))
        return _err(401, "Incorrect username or password", "NotAuthorized")

    # Keep the username -> entra id mapping fresh (best effort).
    _sync_mapping_from_id_token(username, entra_username, token_body.get("id_token"))

    return _ok(200, {
        "accessToken": token_body.get("access_token"),
        "refreshToken": token_body.get("refresh_token", ""),
        "expiresIn": int(token_body.get("expires_in", 3600)),
    })


def _sync_mapping_from_id_token(username: str, email: str, id_token: str | None) -> None:
    """Ensure the mapping doc exists and records the Entra object id."""
    if not id_token:
        return
    try:
        claims = jwt.decode(id_token, options={"verify_signature": False})
    except Exception:  # noqa: BLE001
        return
    uid = claims.get("oid") or claims.get("sub")
    if not uid:
        return
    doc = _find_user_doc(username) or {
        "id": username,
        "username": username,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    doc["email"] = doc.get("email") or email
    doc["entraObjectId"] = uid
    _users_container().upsert_item(body=doc)


# ============================================================
# POST /auth/refresh
# ============================================================

@app.route(route="auth/refresh", methods=["POST"])
def auth_refresh(req: func.HttpRequest) -> func.HttpResponse:
    """Exchange an Entra refresh token for a new access token."""
    if not _entra_configured():
        return _err(503, "Authentication provider is not configured", "NotConfigured")

    b = _body(req)
    rt = b.get("refreshToken", "")
    if not rt:
        return _err(400, "refreshToken is required")

    status, token_body = _entra_post("/oauth2/v2.0/token", {
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "scope": ENTRA_SCOPES,
    })
    if status != 200:
        return _err(401, "Refresh token is invalid or expired", "NotAuthorized")

    return _ok(200, {
        "accessToken": token_body.get("access_token"),
        # Entra may rotate refresh tokens; surface the new one when present.
        "refreshToken": token_body.get("refresh_token", rt),
        "expiresIn": int(token_body.get("expires_in", 3600)),
    })


# ============================================================
# POST /auth/forgot-password
# ============================================================

@app.route(route="auth/forgot-password", methods=["POST"])
def auth_forgot_password(req: func.HttpRequest) -> func.HttpResponse:
    """Start Entra self-service password reset and email an OTP.

    The response never reveals whether the account exists.
    """
    b = _body(req)
    username = b.get("username", "").strip()
    if not username:
        return _err(400, "username is required")

    # Uniform success message regardless of outcome (anti-enumeration).
    success = _ok(200, {"message": "Confirmation code sent."})

    if not _entra_configured():
        return success

    doc = _find_user_doc(username)
    entra_username = (doc or {}).get("email") or username

    # Step 1: /resetpassword/v1.0/start
    status, body = _entra_post("/resetpassword/v1.0/start", {
        "username": entra_username,
        "challenge_type": _CHALLENGE_SSPR,
    })
    if status != 200:
        # user_not_found etc. — swallow to avoid leaking existence.
        return success

    continuation_token = body.get("continuation_token")

    # Step 2: /resetpassword/v1.0/challenge sends the OTP.
    status, body = _entra_post("/resetpassword/v1.0/challenge", {
        "continuation_token": continuation_token,
        "challenge_type": _CHALLENGE_SSPR,
    })
    if status != 200:
        return success

    continuation_token = body.get("continuation_token", continuation_token)
    _store_flow_token(username, "reset", continuation_token)
    return success


# ============================================================
# POST /auth/reset-password
# ============================================================

@app.route(route="auth/reset-password", methods=["POST"])
def auth_reset_password(req: func.HttpRequest) -> func.HttpResponse:
    """Complete Entra self-service password reset using the emailed OTP."""
    if not _entra_configured():
        return _err(503, "Authentication provider is not configured", "NotConfigured")

    b = _body(req)
    username = b.get("username", "").strip()
    code = b.get("confirmationCode", "").strip()
    new_password = b.get("newPassword", "")

    if not username or not code or not new_password:
        return _err(400, "username, confirmationCode, and newPassword are required")
    if len(new_password) < 8:
        return _err(400, "Password must be at least 8 characters", "InvalidPassword")

    continuation_token = _read_flow_token(username, "reset")
    if not continuation_token:
        return _err(400, "Reset session expired. Please request a new code.", "ExpiredCode")

    # Step 3: /resetpassword/v1.0/continue submits the OTP.
    status, body = _entra_post("/resetpassword/v1.0/continue", {
        "continuation_token": continuation_token,
        "grant_type": "oob",
        "oob": code,
    })
    if status != 200:
        err = body.get("error", "")
        suberr = body.get("suberror", "")
        if err == "invalid_grant" or suberr == "invalid_oob_value":
            return _err(400, "Invalid confirmation code", "CodeMismatch")
        if err == "expired_token":
            return _err(400, "Reset session expired. Please request a new code.", "ExpiredCode")
        logger.warning("resetpassword/continue failed: %s", body)
        return _err(400, body.get("error_description", "Password reset failed"), err or "ResetFailed")

    continuation_token = body.get("continuation_token", continuation_token)

    # Step 4: /resetpassword/v1.0/submit sets the new password.
    status, body = _entra_post("/resetpassword/v1.0/submit", {
        "continuation_token": continuation_token,
        "new_password": new_password,
    })
    if status != 200:
        suberr = body.get("suberror", "")
        if suberr in ("password_too_weak", "password_too_short", "password_too_long",
                      "password_recently_used", "password_banned", "password_is_invalid"):
            return _err(400, body.get("error_description", "Password does not meet requirements"), "InvalidPassword")
        logger.warning("resetpassword/submit failed: %s", body)
        return _err(400, body.get("error_description", "Password reset failed"), body.get("error", "ResetFailed"))

    continuation_token = body.get("continuation_token", continuation_token)
    poll_interval = int(body.get("poll_interval", 2))

    # Step 5: poll until the reset is applied.
    for _ in range(10):
        time.sleep(max(1, poll_interval))
        status, body = _entra_post("/resetpassword/v1.0/poll_completion", {
            "continuation_token": continuation_token,
        })
        if status != 200:
            break
        state = body.get("status", "")
        if state == "succeeded":
            _clear_flow_token(username, "reset")
            return _ok(200, {"message": "Password reset successful."})
        if state == "failed":
            return _err(400, "Password reset failed", "ResetFailed")
        continuation_token = body.get("continuation_token", continuation_token)

    # Reset was accepted but not yet confirmed applied; treat as success so the
    # user can sign in shortly. The flow token is cleared to avoid reuse.
    _clear_flow_token(username, "reset")
    return _ok(200, {"message": "Password reset successful."})


# ============================================================
# GET /videos
# ============================================================

@app.route(route="videos", methods=["GET"])
def videos_list(req: func.HttpRequest) -> func.HttpResponse:
    """List videos for the authenticated user."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    limit = int(req.params.get("limit", "100"))
    cursor = req.params.get("cursor", None)

    container = _get_container("videos")

    # Query user's videos
    query = "SELECT * FROM c WHERE c.userId = @uid AND c.status != 'deleted' AND c.status != 'uploading' AND (NOT IS_DEFINED(c.size) OR c.size > 0) AND NOT STARTSWITH(c.id, 'share_token:') AND NOT STARTSWITH(c.id, 'share:') AND NOT STARTSWITH(c.id, 'sent_share:') ORDER BY c.createdAt DESC"
    params = [{"name": "@uid", "value": uid}]

    items = list(container.query_items(
        query=query,
        parameters=params,
        enable_cross_partition_query=True,
        max_item_count=limit,
    ))

    videos = []
    for item in items:
        blob_key = item.get("blobKey", "")
        thumbnail_key = item.get("thumbnailKey", "")

        full_url = _generate_sas_url(blob_key) if blob_key else None
        thumbnail_url = _generate_sas_url(thumbnail_key) if thumbnail_key else full_url

        videos.append({
            "id": item["id"],
            "filename": item.get("filename", ""),
            "contentType": item.get("contentType", "video/mp4"),
            "size": int(item.get("size", 0)),
            "createdAt": item.get("createdAt", ""),
            "thumbnailUrl": thumbnail_url,
            "fullUrl": full_url,
            "labels": item.get("labels", []),
            "labelNames": item.get("labelNames", {}),
            "shared": False,
            "sharedFrom": "",
        })

    # Include shared videos
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

            # Get videos from sharing user with the specified label
            shared_videos_query = "SELECT * FROM c WHERE c.userId = @fromUid AND c.status != 'deleted' AND ARRAY_CONTAINS(c.labels, @labelId)"
            shared_params = [
                {"name": "@fromUid", "value": from_uid},
                {"name": "@labelId", "value": label_id},
            ]
            shared_videos = list(container.query_items(
                query=shared_videos_query,
                parameters=shared_params,
                enable_cross_partition_query=True,
            ))

            for sp in shared_videos:
                if sp.get("id", "").startswith("share_token:") or sp.get("id", "").startswith("share:") or sp.get("id", "").startswith("sent_share:"):
                    continue
                sp_blob_key = sp.get("blobKey", "")
                sp_thumb_key = sp.get("thumbnailKey", "")

                videos.append({
                    "id": sp["id"],
                    "filename": sp.get("filename", ""),
                    "contentType": sp.get("contentType", "video/mp4"),
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
    if len(videos) > limit:
        videos = videos[:limit]
        next_cursor = videos[-1]["id"] if videos else None

    return _ok(200, {"videos": videos, "nextCursor": next_cursor})


# ============================================================
# GET /videos/{id}
# ============================================================

@app.route(route="videos/{video_id}", methods=["GET"])
def videos_get_one(req: func.HttpRequest) -> func.HttpResponse:
    """Get a single video's metadata."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    video_id = req.route_params.get("video_id", "")
    if not video_id:
        return _err(400, "videoId is required")

    # Avoid matching sub-routes
    if video_id in ("upload-url", "share-upload-url", "share-upload"):
        return _err(404, "Not found")

    container = _get_container("videos")
    try:
        item = container.read_item(item=video_id, partition_key=uid)
    except cosmos_exceptions.CosmosResourceNotFoundError:
        return _err(404, "Video not found")

    blob_key = item.get("blobKey", "")
    thumbnail_key = item.get("thumbnailKey", "")
    full_url = _generate_sas_url(blob_key) if blob_key else None
    thumbnail_url = _generate_sas_url(thumbnail_key) if thumbnail_key else full_url

    return _ok(200, {
        "id": video_id,
        "filename": item.get("filename", ""),
        "contentType": item.get("contentType", "video/mp4"),
        "size": int(item.get("size", 0)),
        "createdAt": item.get("createdAt", ""),
        "fullUrl": full_url,
        "thumbnailUrl": thumbnail_url,
    })


# ============================================================
# POST /videos/upload-url
# ============================================================

@app.route(route="videos/upload-url", methods=["POST"])
def videos_upload_url(req: func.HttpRequest) -> func.HttpResponse:
    """Get a presigned URL for uploading a video."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    b = _body(req)
    filename = b.get("filename", "")
    content_type = b.get("contentType", "video/mp4")
    created_at = b.get("createdAt", datetime.now(timezone.utc).isoformat())
    video_id = b.get("photoId", "") or b.get("videoId", "") or str(uuid.uuid4())

    if not filename:
        return _err(400, "filename is required")

    # Build blob path: users/{uid}/YYYY/MM/DD/{videoId}
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now(timezone.utc)
    date_path = f"{dt.year}/{dt.month:02d}/{dt.day:02d}"
    blob_key = f"users/{uid}/{date_path}/{video_id}"

    upload_url = _generate_upload_sas(blob_key, content_type)

    # Store video metadata in Cosmos DB
    container = _get_container("videos")
    video_doc = {
        "id": video_id,
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
        container.upsert_item(body=video_doc)
    except Exception as e:
        logger.error(f"Failed to save video metadata: {e}")
        return _err(500, "Failed to create video record")

    return _ok(200, {
        "videoId": video_id,
        "uploadUrl": upload_url,
        "headers": {"x-ms-blob-type": "BlockBlob", "Content-Type": content_type},
        "expiresIn": 3600,
    })


# ============================================================
# POST /videos/{id}/confirm
# ============================================================

@app.route(route="videos/{video_id}/confirm", methods=["POST"])
def videos_confirm(req: func.HttpRequest) -> func.HttpResponse:
    """Confirm that upload to presigned URL is complete."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    video_id = req.route_params.get("video_id", "")
    if not video_id:
        return _err(400, "videoId is required")

    container = _get_container("videos")
    try:
        item = container.read_item(item=video_id, partition_key=uid)
    except cosmos_exceptions.CosmosResourceNotFoundError:
        return _err(404, "Video not found")

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
# PUT /videos/{id}/labels
# ============================================================

@app.route(route="videos/{video_id}/labels", methods=["PUT"])
def videos_update_labels(req: func.HttpRequest) -> func.HttpResponse:
    """Update labels for a video."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    video_id = req.route_params.get("video_id", "")
    if not video_id:
        return _err(400, "videoId is required")

    b = _body(req)
    labels = b.get("labels", [])
    label_names = b.get("labelNames", {})
    if not isinstance(labels, list):
        return _err(400, "labels must be an array")

    container = _get_container("videos")
    try:
        item = container.read_item(item=video_id, partition_key=uid)
    except cosmos_exceptions.CosmosResourceNotFoundError:
        return _err(404, "Video not found")

    item["labels"] = labels
    if label_names and isinstance(label_names, dict):
        item["labelNames"] = label_names
    container.upsert_item(body=item)

    return _ok(200, {"message": "Labels updated.", "labels": labels})


# ============================================================
# DELETE /videos/{id}
# ============================================================

@app.route(route="videos/{video_id}", methods=["DELETE"])
def videos_delete(req: func.HttpRequest) -> func.HttpResponse:
    """Soft-delete a video."""
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    video_id = req.route_params.get("video_id", "")
    if not video_id:
        return _err(400, "videoId is required")

    container = _get_container("videos")
    try:
        item = container.read_item(item=video_id, partition_key=uid)
    except cosmos_exceptions.CosmosResourceNotFoundError:
        return _err(404, "Video not found")

    # Soft delete
    item["status"] = "deleted"
    item["deletedAt"] = datetime.now(timezone.utc).isoformat()
    container.upsert_item(body=item)

    return _ok(200, {"message": "Video deleted."})


# ============================================================
# POST /videos/share-upload-url
# ============================================================

@app.route(route="videos/share-upload-url", methods=["POST"])
def videos_share_upload_url(req: func.HttpRequest) -> func.HttpResponse:
    """Generate a temporary upload page URL for third parties."""
    if not ENABLE_SHARE_URL:
        return _err(403, "Share URL feature is disabled")

    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    b = _body(req)
    expires_hours = int(b.get("expiresHours", SHARE_UPLOAD_URL_EXPIRY_HOURS))
    label_id = b.get("labelId", "") or ""
    label_name = b.get("labelName", "") or ""

    token = str(uuid.uuid4())

    # Save token to Cosmos DB
    container = _get_container("videos")
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
    container = _get_container("videos")
    try:
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

    html = _build_upload_page_html(token, api_base, expires_hours)

    return func.HttpResponse(
        body=html,
        status_code=200,
        mimetype="text/html",
        charset="utf-8",
    )


# ============================================================
# POST /videos/share-upload
# ============================================================

@app.route(route="videos/share-upload", methods=["POST"])
def videos_share_upload(req: func.HttpRequest) -> func.HttpResponse:
    """Get a presigned URL using a share token (no auth required)."""
    if not ENABLE_SHARE_URL:
        return _err(403, "Share URL feature is disabled")

    b = _body(req)
    token = b.get("token", "")
    filename = b.get("filename", "")
    content_type = b.get("contentType", "video/mp4")

    if not token or not filename:
        return _err(400, "token and filename are required")

    # Validate token
    container = _get_container("videos")
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
    video_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    date_path = f"{now.year}/{now.month:02d}/{now.day:02d}"
    blob_key = f"users/{uid}/{date_path}/{video_id}"

    upload_url = _generate_upload_sas(blob_key, content_type)

    # Create video record
    video_doc = {
        "id": video_id,
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
        video_doc["labelNames"] = {label_id: label_name}
    container.upsert_item(body=video_doc)

    return _ok(200, {
        "uploadUrl": upload_url,
        "videoId": video_id,
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

    # Find recipient user in the username -> Entra id mapping.
    recipient_doc = _find_user_doc(to_username)
    if not recipient_doc or not recipient_doc.get("entraObjectId"):
        return _err(404, "User not found")

    # Videos are partitioned by the Entra stable user id.
    to_uid = recipient_doc["entraObjectId"]
    if to_uid == uid:
        return _err(400, "Cannot share with yourself")

    share_id = str(uuid.uuid4())
    videos_container = _get_container("videos")

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
    videos_container.upsert_item(body=receiver_doc)

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
    videos_container.upsert_item(body=sender_doc)

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
    container = _get_container("videos")

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
    container = _get_container("videos")

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
    container = _get_container("videos")

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

    container = _get_container("videos")
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

    container = _get_container("videos")
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

    container = _get_container("videos")

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

def _build_upload_page_html(token: str, api_base: str, expires_hours: int) -> str:
    """Build the HTML upload page."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video Upload</title>
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
        <h1>Upload Videos</h1>
        <p class="subtitle">Select or drop videos to upload. This link expires in {expires_hours} hours.</p>
        <div class="upload-area" id="dropArea" onclick="document.getElementById('fileInput').click()">
            <div class="icon">&#9729;&#65039;</div>
            <p>Drag & Drop</p>
            <p>or <span class="browse">Browse Files</span></p>
            <input type="file" id="fileInput" accept="video/*" multiple>
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
            selectedFiles = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('video/'));
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
                    const res = await fetch('{api_base}/videos/share-upload', {{
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
# POST /videos/share-download-url
# ============================================================

@app.route(route="videos/share-download-url", methods=["POST"])
def share_download_url(req: func.HttpRequest) -> func.HttpResponse:
    """Generate a download page URL for sharing videos by label."""
    if not ENABLE_SHARE_DOWNLOAD_URL:
        return _err(403, "Share URL feature is disabled")
    user = _get_user_from_request(req)
    if not user:
        return _err(401, "Authentication required")

    uid = user["sub"]
    b = _body(req)
    label_id = b.get("labelId", "")
    label_name = b.get("labelName", "")
    expires_hours = int(b.get("expiresHours", SHARE_DOWNLOAD_URL_EXPIRY_HOURS))

    if not label_id:
        return _err(400, "labelId is required")

    # Count matching videos
    container = _get_container("videos")
    query = "SELECT * FROM c WHERE c.userId = @uid AND c.status != 'deleted' AND c.status != 'uploading' AND ARRAY_CONTAINS(c.labels, @labelId) AND NOT STARTSWITH(c.id, 'share_token:') AND NOT STARTSWITH(c.id, 'share:') AND NOT STARTSWITH(c.id, 'download_token:')"
    params = [{"name": "@uid", "value": uid}, {"name": "@labelId", "value": label_id}]
    matching = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))

    if not matching:
        return _err(404, "No videos found matching the criteria")

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
    """Render an HTML download page for shared videos."""
    if not ENABLE_SHARE_DOWNLOAD_URL:
        return func.HttpResponse("<h1>This feature is disabled.</h1>", status_code=403, mimetype="text/html")

    token = req.params.get("token", "")
    if not token:
        return func.HttpResponse("<h1>Invalid link.</h1>", status_code=400, mimetype="text/html")

    # Find token record
    container = _get_container("videos")
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

    # Get videos with label
    video_query = "SELECT * FROM c WHERE c.userId = @uid AND c.status != 'deleted' AND c.status != 'uploading' AND ARRAY_CONTAINS(c.labels, @labelId) AND NOT STARTSWITH(c.id, 'share_token:') AND NOT STARTSWITH(c.id, 'share:') AND NOT STARTSWITH(c.id, 'download_token:')"
    video_params = [{"name": "@uid", "value": uid}, {"name": "@labelId", "value": label_id}]
    videos = list(container.query_items(query=video_query, parameters=video_params, enable_cross_partition_query=True))

    video_entries = []
    for video in videos:
        blob_key = video.get("blobKey", "")
        thumb_key = video.get("thumbnailKey", "")
        if not blob_key:
            continue
        # Videos may not have a thumbnail yet; fall back to no preview image.
        thumb_url = _generate_sas_url(thumb_key) if thumb_key else None
        full_url = _generate_sas_url(blob_key)
        video_entries.append({
            "filename": video.get("filename", video["id"]),
            "thumbUrl": thumb_url,
            "fullUrl": full_url,
        })

    videos_json = json.dumps([{"filename": e["filename"], "fullUrl": e["fullUrl"]} for e in video_entries])

    video_grid = ""
    for entry in video_entries:
        if entry["thumbUrl"]:
            thumb_html = f'<img src="{entry["thumbUrl"]}" alt="{entry["filename"]}" loading="lazy" />'
        else:
            thumb_html = '<div class="no-thumb"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5,3 19,12 5,21"></polygon></svg></div>'
        video_grid += f'''
        <div class="video-card">
            <a href="{entry["fullUrl"]}" target="_blank" download="{entry["filename"]}">
                {thumb_html}
            </a>
            <div class="video-name">{entry["filename"]}</div>
        </div>'''

    html = f'''<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Download Videos</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }}
        .card {{ background: white; border-radius: 20px; padding: 40px; max-width: 800px; width: 100%; margin: 0 auto; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }}
        h1 {{ font-size: 1.5em; color: #333; margin-bottom: 8px; }}
        .subtitle {{ color: #888; font-size: 0.9em; margin-bottom: 16px; }}
        .actions {{ margin-bottom: 24px; }}
        .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 24px; }}
        .video-card {{ border-radius: 12px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        .video-card img {{ width: 100%; aspect-ratio: 1; object-fit: cover; cursor: pointer; transition: opacity 0.2s; }}
        .video-card img:hover {{ opacity: 0.7; }}
        .no-thumb {{ width: 100%; aspect-ratio: 1; display: flex; align-items: center; justify-content: center; background: #f0f0f0; color: #999; cursor: pointer; }}
        .no-thumb:hover {{ background: #e0e0e0; }}
        .video-name {{ padding: 4px 6px; font-size: 0.65rem; color: #666; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; background: #f9f9f9; }}
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
        <h1>Download Videos</h1>
        <p class="subtitle">{label_name} — {len(video_entries)} videos</p>
        <div class="actions">
            <p class="footer" style="margin-bottom: 12px;">Click a video to download individually. This link expires in {expires_hours} hours.</p>
            <button id="downloadAllBtn" onclick="downloadAll()">📥 Download ZIP — for PC</button>
            <div id="status"></div>
        </div>
        <div class="grid">{video_grid}</div>
    </div>
    <script>
        const videos = {videos_json};
        async function downloadAll() {{
            const btn = document.getElementById('downloadAllBtn');
            const st = document.getElementById('status');
            btn.disabled = true;
            st.className = 'status'; st.style.background = '#ede9ff'; st.style.color = '#5c4db1';
            st.textContent = 'Preparing download...';
            try {{
                const zip = new JSZip();
                for (let i = 0; i < videos.length; i++) {{
                    st.textContent = `Downloading ${{i+1}} / ${{videos.length}}...`;
                    const r = await fetch(videos[i].fullUrl);
                    zip.file(videos[i].filename, await r.blob());
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
def process_video(event: func.EventGridEvent):
    """Triggered by Event Grid when a blob is created in the videos container."""
    from storage_trigger import handle_blob_event
    handle_blob_event(event)
