#!/bin/bash
# ============================================================
# Daily Cloud Photo — GCP One-Command Deployment Script
# Deploys Cloud Functions, Cloud Storage, and Firestore
# ============================================================
set -e

# ── Configuration ──
PROJECT_ID="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GCP_REGION:-asia-northeast1}"
BUCKET_NAME="${PHOTOS_BUCKET:-${PROJECT_ID}-photos}"
FUNCTION_NAME="daily-cloud-photo-api"
TRIGGER_FUNCTION_NAME="daily-cloud-photo-storage-trigger"
FIREBASE_API_KEY="${FIREBASE_API_KEY:-}"

# Feature toggles
REQUIRE_EMAIL="${REQUIRE_EMAIL:-true}"
REQUIRE_PHONE="${REQUIRE_PHONE:-false}"
ENABLE_SHARE_URL="${ENABLE_SHARE_URL:-true}"
ENABLE_LABEL_SHARING="${ENABLE_LABEL_SHARING:-true}"
APP_DISPLAY_NAME="${APP_DISPLAY_NAME:-Daily Cloud Photo Backend}"

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}  Daily Cloud Photo — GCP Deployment${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

# ── Validate prerequisites ──
echo -e "${YELLOW}[1/7] Checking prerequisites...${NC}"

if ! command -v gcloud &> /dev/null; then
    echo -e "${RED}ERROR: gcloud CLI is not installed.${NC}"
    echo "Install from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

if [ -z "$PROJECT_ID" ]; then
    echo -e "${RED}ERROR: No GCP project configured.${NC}"
    echo "Run: gcloud config set project YOUR_PROJECT_ID"
    exit 1
fi

echo -e "  Project: ${GREEN}${PROJECT_ID}${NC}"
echo -e "  Region:  ${GREEN}${REGION}${NC}"
echo -e "  Bucket:  ${GREEN}${BUCKET_NAME}${NC}"
echo ""

# ── Enable required APIs ──
echo -e "${YELLOW}[2/7] Enabling required APIs...${NC}"
gcloud services enable \
    cloudfunctions.googleapis.com \
    cloudbuild.googleapis.com \
    firestore.googleapis.com \
    storage.googleapis.com \
    identitytoolkit.googleapis.com \
    run.googleapis.com \
    eventarc.googleapis.com \
    --project="${PROJECT_ID}" \
    --quiet

echo -e "  ${GREEN}✓ APIs enabled${NC}"
echo ""

# ── Grant IAM permissions for Cloud Functions deployment ──
ACCOUNT=$(gcloud config get-value account 2>/dev/null)
echo -e "  Granting deployment permissions to ${ACCOUNT}..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="user:${ACCOUNT}" \
    --role="roles/iam.serviceAccountUser" \
    --quiet 2>/dev/null || true

# Grant signBlob permission for signed URL generation
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
SA_EMAIL="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/iam.serviceAccountTokenCreator" \
    --quiet 2>/dev/null || true

# ── Create Firestore database (if not exists) ──
echo -e "${YELLOW}[3/7] Setting up Firestore...${NC}"
if gcloud firestore databases describe --project="${PROJECT_ID}" 2>/dev/null; then
    echo -e "  ${GREEN}✓ Firestore already exists${NC}"
else
    gcloud firestore databases create \
        --location="${REGION}" \
        --project="${PROJECT_ID}" \
        --quiet 2>/dev/null || true

    # Wait until Firestore is ready
    echo -e "  Waiting for Firestore to be ready..."
    for i in {1..30}; do
        if gcloud firestore databases describe --project="${PROJECT_ID}" 2>/dev/null | grep -q "FIRESTORE_NATIVE"; then
            break
        fi
        sleep 10
    done
fi

# Create composite index for userId + photoId queries
gcloud firestore indexes composite create \
    --collection-group=photos \
    --field-config field-path=userId,order=ascending \
    --field-config field-path=photoId,order=ascending \
    --project="${PROJECT_ID}" \
    --quiet 2>/dev/null || true
echo -e "  ${GREEN}✓ Firestore indexes configured${NC}"
echo ""

# ── Create Cloud Storage bucket ──
echo -e "${YELLOW}[4/7] Setting up Cloud Storage...${NC}"
if gsutil ls -b "gs://${BUCKET_NAME}" 2>/dev/null; then
    echo -e "  ${GREEN}✓ Bucket already exists${NC}"
else
    gsutil mb -l "${REGION}" -p "${PROJECT_ID}" "gs://${BUCKET_NAME}"
    echo -e "  ${GREEN}✓ Bucket created${NC}"
fi

# Enable versioning for soft-delete support
gsutil versioning set on "gs://${BUCKET_NAME}"

# Set CORS for browser uploads
cat > /tmp/cors.json << 'EOF'
[
  {
    "origin": ["*"],
    "method": ["GET", "PUT", "POST", "OPTIONS"],
    "responseHeader": ["Content-Type", "Authorization"],
    "maxAgeSeconds": 3600
  }
]
EOF
gsutil cors set /tmp/cors.json "gs://${BUCKET_NAME}"
rm -f /tmp/cors.json

echo -e "  ${GREEN}✓ Bucket configured with versioning and CORS${NC}"
echo ""

# ── Set up Identity Platform (Firebase Auth) automatically ──
echo -e "${YELLOW}[5/7] Setting up Identity Platform (Auth)...${NC}"

# Initialize Identity Platform (equivalent to "Get Started" in Console)
ACCESS_TOKEN=$(gcloud auth print-access-token)
curl -s -X POST \
    "https://identitytoolkit.googleapis.com/v2/projects/${PROJECT_ID}/identityPlatform:initializeAuth" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "x-goog-user-project: ${PROJECT_ID}" \
    -H "Content-Type: application/json" \
    -d '{}' > /dev/null 2>&1

# Enable email/password sign-in
curl -s -X PATCH \
    "https://identitytoolkit.googleapis.com/admin/v2/projects/${PROJECT_ID}/config?updateMask=signIn.email" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "x-goog-user-project: ${PROJECT_ID}" \
    -H "Content-Type: application/json" \
    -d '{"signIn":{"email":{"enabled":true,"passwordRequired":true}}}' \
    > /dev/null 2>&1

echo -e "  ${GREEN}✓ Email/Password sign-in enabled${NC}"

# Get API Key automatically
if [ -z "$FIREBASE_API_KEY" ]; then
    KEY_UID=$(gcloud services api-keys list \
        --project="${PROJECT_ID}" \
        --format="value(uid)" \
        --limit=1 2>/dev/null)
    if [ -n "$KEY_UID" ]; then
        FIREBASE_API_KEY=$(gcloud services api-keys get-key-string "$KEY_UID" \
            --project="${PROJECT_ID}" \
            --format="value(keyString)" 2>/dev/null)
    fi

    if [ -z "$FIREBASE_API_KEY" ]; then
        echo -e "  ${YELLOW}⚠ Could not auto-detect API key. Auth endpoints may not work.${NC}"
        FIREBASE_API_KEY="NOT_SET"
    else
        echo -e "  ${GREEN}✓ API Key detected: ${FIREBASE_API_KEY:0:10}...${NC}"
    fi
fi
echo ""

# ── Deploy main API function ──
echo -e "${YELLOW}[6/7] Deploying main API function...${NC}"
gcloud functions deploy "${FUNCTION_NAME}" \
    --gen2 \
    --runtime=python312 \
    --region="${REGION}" \
    --source=./functions \
    --entry-point=main_handler \
    --trigger-http \
    --allow-unauthenticated \
    --memory=256MB \
    --timeout=60s \
    --set-env-vars="PHOTOS_BUCKET=${BUCKET_NAME},GCP_PROJECT=${PROJECT_ID},FIREBASE_API_KEY=${FIREBASE_API_KEY},REQUIRE_EMAIL=${REQUIRE_EMAIL},REQUIRE_PHONE=${REQUIRE_PHONE},ENABLE_SHARE_URL=${ENABLE_SHARE_URL},ENABLE_LABEL_SHARING=${ENABLE_LABEL_SHARING},APP_DISPLAY_NAME=${APP_DISPLAY_NAME}" \
    --project="${PROJECT_ID}" \
    --quiet

API_URL=$(gcloud functions describe "${FUNCTION_NAME}" --region="${REGION}" --gen2 --format='value(serviceConfig.uri)' --project="${PROJECT_ID}")
echo -e "  ${GREEN}✓ API function deployed${NC}"
echo ""

# ── Deploy storage trigger function ──
echo -e "${YELLOW}[7/7] Deploying storage trigger function...${NC}"

# Grant Eventarc permissions to the default compute service account
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
SA_EMAIL="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

# Grant required roles for Eventarc
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/eventarc.eventReceiver" \
    --quiet 2>/dev/null || true

# Grant Cloud Storage pubsub publishing
GCS_SA=$(gsutil kms serviceaccount -p "${PROJECT_ID}")
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${GCS_SA}" \
    --role="roles/pubsub.publisher" \
    --quiet 2>/dev/null || true

# Grant storage access for Eventarc bucket validation
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${GCS_SA}" \
    --role="roles/storage.admin" \
    --quiet 2>/dev/null || true

gcloud functions deploy "${TRIGGER_FUNCTION_NAME}" \
    --gen2 \
    --runtime=python312 \
    --region="${REGION}" \
    --source=./trigger \
    --entry-point=storage_trigger_handler \
    --trigger-event-filters="type=google.cloud.storage.object.v1.finalized" \
    --trigger-event-filters="bucket=${BUCKET_NAME}" \
    --memory=512MB \
    --timeout=120s \
    --set-env-vars="PHOTOS_BUCKET=${BUCKET_NAME},GCP_PROJECT=${PROJECT_ID}" \
    --project="${PROJECT_ID}" \
    --quiet

echo -e "  ${GREEN}✓ Storage trigger function deployed${NC}"
echo ""

# ── Summary ──
echo -e "${BLUE}============================================================${NC}"
echo -e "${GREEN}  Deployment Complete!${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""
echo -e "  API Endpoint: ${GREEN}${API_URL}${NC}"
echo ""
echo -e "  Test with:"
echo -e "    curl ${API_URL}/info"
echo ""
echo -e "  Configure the app:"
echo -e "    Open Drawer → Settings → Enter endpoint URL → Save"
echo ""
if [ "$FIREBASE_API_KEY" = "NOT_SET" ]; then
    echo -e "  ${YELLOW}⚠ Remember to set FIREBASE_API_KEY and redeploy for auth to work.${NC}"
    echo ""
fi
echo -e "${BLUE}============================================================${NC}"
