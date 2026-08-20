# ============================================================
# Cloud Functions (Gen2)
# ============================================================

# ── Package source code ──

data "archive_file" "api_source" {
  type        = "zip"
  source_dir  = "${path.module}/../functions"
  output_path = "${path.module}/.build/api_source.zip"
}

data "archive_file" "trigger_source" {
  type        = "zip"
  source_dir  = "${path.module}/../trigger"
  output_path = "${path.module}/.build/trigger_source.zip"
}

# Upload source archives. The object name includes the content hash so a
# code change produces a new object and forces the function to redeploy.
resource "google_storage_bucket_object" "api_source" {
  name   = "sources/api-${data.archive_file.api_source.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.api_source.output_path
}

resource "google_storage_bucket_object" "trigger_source" {
  name   = "sources/trigger-${data.archive_file.trigger_source.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.trigger_source.output_path
}

# ── Main HTTP API function ──

resource "google_cloudfunctions2_function" "api" {
  name     = local.api_function_name
  project  = var.project_id
  location = var.region

  build_config {
    runtime         = "python312"
    entry_point     = "main_handler"
    service_account = google_service_account.build.id
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.api_source.name
      }
    }
  }

  service_config {
    available_memory      = "256M"
    timeout_seconds       = 60
    ingress_settings      = "ALLOW_ALL"
    max_instance_count    = 100
    service_account_email = google_service_account.runtime.email

    environment_variables = {
      VIDEOS_BUCKET             = google_storage_bucket.videos.name
      GCP_PROJECT               = var.project_id
      FIREBASE_API_KEY          = google_apikeys_key.identity.key_string
      REQUIRE_EMAIL             = tostring(var.require_email)
      REQUIRE_PHONE             = tostring(var.require_phone)
      ENABLE_SHARE_URL          = tostring(var.enable_share_url)
      ENABLE_SHARE_DOWNLOAD_URL = tostring(var.enable_share_download_url)
      ENABLE_LABEL_SHARING      = tostring(var.enable_label_sharing)
      SHARE_UPLOAD_URL_EXPIRY_HOURS   = tostring(var.share_upload_url_expiry_hours)
      SHARE_DOWNLOAD_URL_EXPIRY_HOURS = tostring(var.share_download_url_expiry_hours)
      APP_DISPLAY_NAME          = var.app_display_name
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_service_account_iam_member.runtime_token_creator_self,
    google_project_iam_member.build_log_writer,
    google_project_iam_member.build_artifact_writer,
    google_project_iam_member.build_storage_viewer,
    time_sleep.wait_build_iam,
  ]
}

# Allow unauthenticated invocation of the HTTP API (Cloud Run backing service).
resource "google_cloud_run_service_iam_member" "api_public" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ── Storage trigger function (thumbnail via ffmpeg frame extraction) ──

resource "google_cloudfunctions2_function" "trigger" {
  name     = local.trigger_function_name
  project  = var.project_id
  location = var.region

  build_config {
    runtime         = "python312"
    entry_point     = "storage_trigger_handler"
    service_account = google_service_account.build.id
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.trigger_source.name
      }
    }
  }

  service_config {
    available_memory      = "512M"
    timeout_seconds       = 120
    max_instance_count    = 100
    service_account_email = google_service_account.runtime.email

    environment_variables = {
      VIDEOS_BUCKET = google_storage_bucket.videos.name
      GCP_PROJECT   = var.project_id
    }
  }

  event_trigger {
    trigger_region        = var.region
    event_type            = "google.cloud.storage.object.v1.finalized"
    retry_policy          = "RETRY_POLICY_DO_NOT_RETRY"
    service_account_email = google_service_account.runtime.email

    event_filters {
      attribute = "bucket"
      value     = google_storage_bucket.videos.name
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_project_iam_member.eventarc_receiver,
    google_project_iam_member.run_invoker,
    google_project_iam_member.artifact_reader,
    google_project_iam_member.gcs_pubsub_publisher,
    google_project_iam_member.build_log_writer,
    google_project_iam_member.build_artifact_writer,
    google_project_iam_member.build_storage_viewer,
    time_sleep.wait_build_iam,
  ]
}
