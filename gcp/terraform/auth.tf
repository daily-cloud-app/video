# ============================================================
# Identity Platform (Firebase Auth) configuration
# ============================================================

# Enables Identity Platform with Email/Password sign-in.
# Equivalent to the manual "Get Started" + enabling email/password
# that the old deploy.sh performed via REST calls.
resource "google_identity_platform_config" "default" {
  provider = google-beta
  project  = var.project_id

  sign_in {
    allow_duplicate_emails = false

    email {
      enabled           = true
      password_required = true
    }
  }

  depends_on = [google_project_service.enabled]
}

# ============================================================
# Dedicated API key for Identity Platform REST calls
# ============================================================
#
# The Cloud Function calls the Identity Toolkit / Secure Token REST APIs
# (signUp, signInWithPassword, sendOobCode, resetPassword, token refresh),
# which require a browser-style API key. We create a dedicated key
# restricted to only those APIs instead of reusing an arbitrary project key.

# API key names are retained for ~30 days after deletion (soft-delete), which
# blocks re-creating a key with the same fixed name when a project is reused.
# A random suffix keeps the name unique across destroy/apply cycles.
resource "random_id" "identity_key" {
  byte_length = 4
}

resource "google_apikeys_key" "identity" {
  provider     = google-beta
  project      = var.project_id
  name         = "daily-cloud-video-identity-key-${random_id.identity_key.hex}"
  display_name = "Daily Cloud Video — Identity Toolkit"

  restrictions {
    api_targets {
      service = "identitytoolkit.googleapis.com"
    }
    api_targets {
      service = "securetoken.googleapis.com"
    }
  }

  depends_on = [google_project_service.enabled]
}
