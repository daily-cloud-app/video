# ============================================================
# IAM
# ============================================================
#
# Instead of relying on the Compute Engine default service account
# (which may not exist in a brand-new project without the Compute API),
# we create a dedicated user-managed service account and assign it the
# minimum roles required. This SA is used as the runtime identity for
# both Cloud Functions and as the Eventarc trigger identity.

# ── Dedicated runtime service account ──

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "daily-cloud-video-runtime"
  display_name = "Daily Cloud Video Runtime"

  depends_on = [google_project_service.enabled]
}

# Allow the runtime SA to sign blobs as itself (V4 signed URL generation).
resource "google_service_account_iam_member" "runtime_token_creator_self" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.runtime.email}"
}

# Receive Eventarc events (storage finalize → trigger function).
resource "google_project_iam_member" "eventarc_receiver" {
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# Invoke the backing Cloud Run services (Eventarc → Run).
resource "google_project_iam_member" "run_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# Pull the built container image (required for Cloud Functions Gen2 / Eventarc).
resource "google_project_iam_member" "artifact_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# Read/write objects in the videos bucket (videos + thumbnails).
resource "google_storage_bucket_iam_member" "runtime_videos_admin" {
  bucket = google_storage_bucket.videos.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

# Firestore access for metadata and username mapping.
resource "google_project_iam_member" "runtime_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# Firebase Authentication admin access. The API calls the Firebase Admin SDK
# (get_user / get_user_by_email in /auth/confirm, and user lookups elsewhere),
# which require admin permissions on Identity Platform / Firebase Auth.
resource "google_project_iam_member" "runtime_firebaseauth_admin" {
  project = var.project_id
  role    = "roles/firebaseauth.admin"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# ── Dedicated Cloud Build service account ──
#
# Separate from the runtime SA. Used only by Cloud Build to build the
# Cloud Functions (Gen2) container images. Roles follow Google's documented
# minimum for a user-specified build service account.

resource "google_service_account" "build" {
  project      = var.project_id
  account_id   = "daily-cloud-video-build"
  display_name = "Daily Cloud Video Build"

  depends_on = [google_project_service.enabled]
}

# Google's official docs for a user-specified Cloud Build service account
# (Cloud Run functions / Cloud Functions Gen2 from source) require these
# three roles explicitly. In practice, roles/run.builder alone did NOT grant
# read access to the gcf-v2-sources-* build bucket, so we assign the three
# documented roles directly.
#   - logging.logWriter    : write build logs
#   - artifactregistry.writer : push the built image
#   - storage.objectViewer : read source from the gcf-v2-sources bucket
resource "google_project_iam_member" "build_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.build.email}"
}

resource "google_project_iam_member" "build_artifact_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.build.email}"
}

resource "google_project_iam_member" "build_storage_viewer" {
  project = var.project_id
  role    = "roles/storage.objectViewer"
  member  = "serviceAccount:${google_service_account.build.email}"
}

# ── Cloud Storage service agent ──

# The GCS service agent must be able to publish object events to
# Eventarc/Pub-Sub for the storage finalize trigger to fire.
data "google_storage_project_service_account" "gcs" {
  project = var.project_id

  depends_on = [google_project_service.enabled]
}

resource "google_project_iam_member" "gcs_pubsub_publisher" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}
