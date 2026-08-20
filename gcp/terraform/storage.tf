# ============================================================
# Cloud Storage
# ============================================================

# Videos bucket — stores user videos and generated thumbnails.
resource "google_storage_bucket" "videos" {
  name          = local.videos_bucket
  project       = var.project_id
  location      = var.region
  # Sample project: allow `terraform destroy` to remove the bucket even if it
  # still contains videos, so teardown is a single command. Set to false if you
  # want to protect against accidental deletion of user data.
  force_destroy = true

  uniform_bucket_level_access = true

  # Soft-delete support via object versioning
  versioning {
    enabled = true
  }

  # Allow browser-based uploads/downloads via signed URLs.
  # NOTE: origin "*" is permissive; restrict to specific domains in production.
  cors {
    origin          = ["*"]
    method          = ["GET", "PUT", "POST", "OPTIONS"]
    response_header = ["Content-Type", "Authorization"]
    max_age_seconds = 3600
  }

  depends_on = [google_project_service.enabled]
}

# Source bucket — holds zipped Cloud Function source archives.
resource "google_storage_bucket" "function_source" {
  name          = "${var.project_id}-function-source"
  project       = var.project_id
  location      = var.region
  force_destroy = true

  uniform_bucket_level_access = true

  depends_on = [google_project_service.enabled]
}
