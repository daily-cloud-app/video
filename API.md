# Daily Cloud Photo — API & Data Reference

REST API specification and data schema that all cloud provider backends must implement.

## Base URL

Endpoint URL generated after deployment.  
Example: `https://your-api-endpoint.example.com/v1`

## Authentication

Token-based authentication. Use the `accessToken` from signin response:
```
Authorization: Bearer <accessToken>
```

---

## Server Info

### GET /info

Returns server configuration. No authentication required.

**Response 200:**
```json
{
  "name": "Daily Cloud Photo Backend",
  "version": "1.0.0",
  "signupFields": ["username", "password", "email"],
  "features": ["share-upload-url", "share-download-url", "label-sharing"]
}
```

- `name`: Display name shown in the app
- `signupFields`: Fields required for signup (dynamic based on server config)
- `features`: Enabled features. Possible values: `share-upload-url`, `share-download-url`, `label-sharing`

---

## Auth

### POST /auth/signup

Register a new user.

**Request Body:**
```json
{
  "username": "string (required)",
  "password": "string (required)",
  "email": "string (required when RequireEmail=true)",
  "phone": "string (required when RequirePhone=true)"
}
```

**Response 201:**
```json
{
  "message": "User created. Confirmation may be required.",
  "confirmationRequired": true
}
```

### POST /auth/confirm

Confirm signup with verification code.

**Request Body:**
```json
{
  "username": "string",
  "confirmationCode": "string"
}
```

**Response 200:**
```json
{
  "message": "User confirmed."
}
```

### POST /auth/signin

Sign in and receive tokens.

**Request Body:**
```json
{
  "username": "string",
  "password": "string"
}
```

**Response 200:**
```json
{
  "accessToken": "string",
  "refreshToken": "string",
  "expiresIn": 3600
}
```

### POST /auth/refresh

Refresh an expired access token.

**Request Body:**
```json
{
  "refreshToken": "string"
}
```

**Response 200:**
```json
{
  "accessToken": "string",
  "expiresIn": 3600
}
```

### POST /auth/forgot-password

Request a password reset code.

**Request Body:**
```json
{
  "username": "string"
}
```

**Response 200:**
```json
{
  "message": "Confirmation code sent."
}
```

### POST /auth/reset-password

Reset password with confirmation code.

**Request Body:**
```json
{
  "username": "string",
  "confirmationCode": "string",
  "newPassword": "string"
}
```

**Response 200:**
```json
{
  "message": "Password reset successful."
}
```

---

## Photos

### GET /photos

List photos for the authenticated user. Includes shared photos from accepted label shares.

**Query Parameters:**
- `limit` (int, optional, default: 100)
- `cursor` (string, optional) — pagination cursor

**Response 200:**
```json
{
  "photos": [
    {
      "id": "string (photoId / cloudId)",
      "filename": "string",
      "contentType": "image/jpeg",
      "size": 1234567,
      "createdAt": "2025-01-01T00:00:00Z",
      "thumbnailUrl": "string (presigned URL, 1hr expiry)",
      "fullUrl": "string (presigned URL, 1hr expiry)",
      "labels": ["custom:123", "year:2025"],
      "labelNames": {"custom:123": "Family"},
      "shared": false,
      "sharedFrom": ""
    }
  ],
  "nextCursor": "string | null"
}
```

- Photos with `status: deleted` are excluded from this response
- Shared photos have `"shared": true` and `"sharedFrom": "username"`
- `labelNames` maps label IDs to their display names (for restoring names on other devices)

### GET /photos/{id}

Get a single photo's metadata and fresh presigned URLs.

**Response 200:**
```json
{
  "id": "string",
  "filename": "string",
  "contentType": "image/jpeg",
  "size": 1234567,
  "createdAt": "2025-01-01T00:00:00Z",
  "fullUrl": "string (presigned URL)",
  "thumbnailUrl": "string (presigned URL)"
}
```

### POST /photos/upload-url

Get a presigned URL for uploading a photo.

**Request Body:**
```json
{
  "filename": "IMG_20250101_120000.jpg",
  "contentType": "image/jpeg",
  "createdAt": "2025-01-01T12:00:00Z (optional, defaults to now)",
  "photoId": "string (optional, UUID — used for re-upload to same path)"
}
```

**Response 200:**
```json
{
  "photoId": "string",
  "uploadUrl": "string (presigned PUT URL)",
  "headers": {"x-ms-blob-type": "BlockBlob", "Content-Type": "image/jpeg"},
  "expiresIn": 3600
}
```

- `headers` (optional): Additional HTTP headers that the client MUST include in the PUT request to the `uploadUrl`. If not present, only `Content-Type` is required. Azure Blob Storage requires `x-ms-blob-type: BlockBlob`; AWS S3 and GCP do not return this field.

The client uploads the file directly to the presigned URL using HTTP PUT with the specified Content-Type header and any additional headers from the `headers` field.

### POST /photos/{id}/confirm

Confirm that upload is complete. Also used to restore a soft-deleted photo.

**Response 200:**
```json
{
  "message": "Upload confirmed.",
  "thumbnailUrl": "string (presigned URL)"
}
```

### PUT /photos/{id}/labels

Update labels for a photo. Replaces all labels with the provided list.

**Request Body:**
```json
{
  "labels": ["custom:123", "custom:456"],
  "labelNames": {
    "custom:123": "Family",
    "custom:456": "Travel"
  }
}
```

- `labels`: Array of label IDs to assign
- `labelNames`: Optional map of labelId → display name (for cross-device label name sync)

**Response 200:**
```json
{
  "message": "Labels updated.",
  "labels": ["custom:123", "custom:456"]
}
```

### DELETE /photos/{id}

Soft-delete a photo. Marks status as `deleted`. Storage data is preserved (recoverable via versioning).

**Response 200:**
```json
{
  "message": "Photo deleted."
}
```

---

## Share Upload URL

Requires `share-upload-url` feature enabled.

### POST /photos/share-upload-url

Generate a temporary upload page URL for third parties (no login required for uploaders).

**Request Body:**
```json
{
  "expiresHours": 24
}
```

**Response 200:**
```json
{
  "shareUrl": "https://api.example.com/v1/upload-page?token=uuid",
  "token": "string",
  "expiresHours": 24
}
```

### GET /upload-page?token={token}

Returns an HTML upload page. No authentication required. Token validated server-side.

### POST /photos/share-upload

Get a presigned URL using a share token (no auth required).

**Request Body:**
```json
{
  "token": "string",
  "filename": "photo.jpg",
  "contentType": "image/jpeg"
}
```

**Response 200:**
```json
{
  "uploadUrl": "string (presigned PUT URL)",
  "photoId": "string"
}
```

---

## Share Download URL

Requires `share-download-url` feature enabled. Allows sharing photos with non-app-users via a browser-accessible download page.

### POST /photos/share-download-url

Generate a temporary download page URL. Photos are filtered by label and optionally by date range.

**Request Body:**
```json
{
  "labelId": "custom:123 (required)",
  "labelName": "Family (optional, for display)",
  "expiresHours": 72,
  "dateFrom": "2025-01-01T00:00:00Z (optional)",
  "dateTo": "2025-12-31T23:59:59Z (optional)"
}
```

**Response 200:**
```json
{
  "downloadUrl": "https://api.example.com/v1/download-page?token=uuid",
  "token": "string",
  "expiresHours": 72,
  "photoCount": 5
}
```

### GET /download-page?token={token}

Returns an HTML page that displays thumbnails and allows downloading photos. No authentication required. Token validated server-side.

The page should:
- Display thumbnails of all photos matching the label (and date range if specified)
- Allow individual photo download
- Allow bulk download (zip)
- Show expiration info
- Be mobile-friendly

---

## Label Sharing

Requires `label-sharing` feature enabled.

### POST /shares

Create a label share request.

**Request Body:**
```json
{
  "toUsername": "recipient@example.com",
  "labelId": "custom:123",
  "labelName": "Family"
}
```

**Response 201:**
```json
{
  "message": "Share request created.",
  "shareId": "string"
}
```

### GET /shares/pending

List pending share requests received (not yet accepted).

**Response 200:**
```json
{
  "shares": [
    {
      "shareId": "string",
      "fromUser": "sender@example.com",
      "labelId": "custom:123",
      "labelName": "Family",
      "createdAt": "2025-01-01T00:00:00Z"
    }
  ]
}
```

### GET /shares

List accepted shares (labels shared with you).

**Response 200:**
```json
{
  "shares": [
    {
      "shareId": "string",
      "fromUser": "sender@example.com",
      "fromUserId": "string",
      "labelId": "custom:123",
      "labelName": "Family",
      "createdAt": "2025-01-01T00:00:00Z"
    }
  ]
}
```

### GET /shares/sent

List shares you have sent to others.

**Response 200:**
```json
{
  "shares": [
    {
      "shareId": "string",
      "toUser": "recipient@example.com",
      "labelId": "custom:123",
      "labelName": "Family",
      "status": "active",
      "createdAt": "2025-01-01T00:00:00Z"
    }
  ]
}
```

### POST /shares/{shareId}/accept

Accept a pending share request. Photos with the shared label become visible via GET /photos.

**Response 200:**
```json
{
  "message": "Share accepted."
}
```

### POST /shares/{shareId}/reject

Reject a pending share request.

**Response 200:**
```json
{
  "message": "Share rejected."
}
```

### DELETE /shares/{shareId}

Remove a share (works for both sender and receiver). Bidirectional cleanup.

**Response 200:**
```json
{
  "message": "Share removed."
}
```

---

## Error Responses

All endpoints return errors in this format:

```json
{
  "error": "ErrorCode",
  "message": "Human readable description"
}
```

**HTTP Status Codes:**
- 400 — Validation error
- 401 — Authentication error (invalid/expired token)
- 403 — Forbidden (feature disabled, token expired)
- 404 — Resource not found
- 409 — Conflict (e.g. username already exists)
- 500 — Server error

---

## Data Schema

### Object Storage (S3 / Cloud Storage / Blob Storage)

Photos are stored with versioning enabled for soft-delete recovery.

**Path format:**
```
users/{userId}/{YYYY}/{MM}/{DD}/{photoId}
```

- `userId`: Auth provider user ID (Cognito sub, Firebase UID, etc.)
- Date path: Derived from photo's `createdAt`
- `photoId`: UUID generated by the app at upload time

**Thumbnails:**
```
thumbnails/{userId}/{YYYY}/{MM}/{DD}/{photoId}
```

Generated automatically by the storage trigger function when a new photo is uploaded.

**CORS:** Must allow `GET`, `PUT`, `HEAD` from any origin for presigned URL uploads.

### Database (DynamoDB / Firestore / Cosmos DB)

Single table/collection storing all records. Differentiated by `photoId` prefix.

**Primary key:** `userId` (partition) + `photoId` (sort)

#### Photo Record

| Field | Type | Description |
|-------|------|-------------|
| userId | string | Owner's auth user ID |
| photoId | string | UUID (app-generated) or path-based ID (storage trigger) |
| filename | string | Original filename |
| contentType | string | MIME type (e.g. `image/jpeg`) |
| s3Key / gcsKey / blobKey | string | Full storage path |
| size | number | File size in bytes |
| status | string | `uploading`, `uploaded`, `deleted` |
| createdAt | string (ISO 8601) | Photo capture date (EXIF > path > upload time) |
| labels | array of string | Label IDs assigned to this photo |
| labelNames | map (string → string) | Label ID → display name mapping |
| thumbnailKey | string | Storage path for generated thumbnail |
| deletedAt | string (ISO 8601) | When soft-deleted (only when status=deleted) |

#### Share Token Record

| Field | Type | Description |
|-------|------|-------------|
| userId | string | Token owner's user ID |
| photoId | string | `share_token:{uuid}` |
| status | string | `active` or `expired` |
| createdAt | string | Token creation time |
| expiresHours | number | Validity period |

#### Label Share Record (sent)

| Field | Type | Description |
|-------|------|-------------|
| userId | string | Sender's user ID |
| photoId | string | `sent_share:{shareId}` |
| toUser | string | Recipient username |
| labelId | string | Shared label ID |
| labelName | string | Label display name |
| status | string | `active` |
| createdAt | string | Share creation time |

#### Label Share Record (received)

| Field | Type | Description |
|-------|------|-------------|
| userId | string | Recipient's user ID |
| photoId | string | `share:{shareId}` |
| fromUser | string | Sender's user ID |
| fromUsername | string | Sender's username |
| labelId | string | Shared label ID |
| labelName | string | Label display name |
| status | string | `pending` or `accepted` |
| createdAt | string | Share creation time |

### Storage Trigger

Automatically fires when a new object is created in the photos storage path (`users/` prefix).

**Behavior:**
1. Skip if file size is 0 (folder placeholder)
2. Skip if path contains `thumbnails/`
3. Determine content type from file extension; if no extension, check storage metadata
4. Extract capture date: EXIF DateTimeOriginal → path date (`/YYYY/MM/DD/`) → current time
5. Generate thumbnail (JPEG, max 300px)
6. Create or update database record with `status: uploaded`

---

## Label ID Conventions

| Pattern | Example | Description |
|---------|---------|-------------|
| `year:{YYYY}` | `year:2025` | Auto-generated year label |
| `month:{MM}` | `month:06` | Auto-generated month label |
| `day:{DD}` | `day:22` | Auto-generated day label |
| `custom:{timestamp}` | `custom:1782776468976` | User-created custom label |
