# ============================================================
# Firestore (Native mode)
# ============================================================

resource "google_firestore_database" "default" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  depends_on = [google_project_service.enabled]
}

# Composite index for userId + photoId queries on the videos collection.
resource "google_firestore_index" "videos_user_photo" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "videos"

  fields {
    field_path = "userId"
    order      = "ASCENDING"
  }
  fields {
    field_path = "photoId"
    order      = "ASCENDING"
  }
}
