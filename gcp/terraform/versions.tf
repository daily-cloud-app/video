# ============================================================
# Terraform & Provider version constraints
# ============================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0.0, < 7.0.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 5.0.0, < 7.0.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5.0"
    }
  }

  # Remote state is stored in a GCS bucket.
  # The bucket name is passed at init time via:
  #   terraform init -backend-config="bucket=<PROJECT_ID>-tfstate"
  # (deploy.sh creates the bucket before running init)
  backend "gcs" {
    prefix = "terraform/state"
  }
}
