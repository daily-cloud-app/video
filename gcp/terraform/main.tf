# ============================================================
# Enable required Google Cloud APIs
# ============================================================

locals {
  required_apis = [
    "cloudfunctions.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "firestore.googleapis.com",
    "storage.googleapis.com",
    "identitytoolkit.googleapis.com",
    "run.googleapis.com",
    "eventarc.googleapis.com",
    "iamcredentials.googleapis.com",
    "pubsub.googleapis.com",
    "apikeys.googleapis.com",
    "serviceusage.googleapis.com",
  ]
}

resource "google_project_service" "enabled" {
  for_each = toset(local.required_apis)

  project = var.project_id
  service = each.value

  # Keep APIs enabled even if this resource is destroyed, to avoid
  # accidentally disrupting other workloads in the project.
  disable_on_destroy = false
}
