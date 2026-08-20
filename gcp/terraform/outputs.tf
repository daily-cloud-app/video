# ============================================================
# Outputs
# ============================================================

output "api_endpoint" {
  description = "Base URL of the HTTP API function"
  value       = google_cloudfunctions2_function.api.service_config[0].uri
}

output "project_id" {
  description = "GCP project ID"
  value       = var.project_id
}

output "region" {
  description = "Deployment region"
  value       = var.region
}

output "videos_bucket" {
  description = "Cloud Storage bucket for videos"
  value       = google_storage_bucket.videos.name
}

# The Identity Platform API key is embedded in the function environment.
# It is intentionally NOT exposed as a plaintext output.
output "identity_api_key_created" {
  description = "Whether a dedicated Identity Platform API key was created"
  value       = true
}
