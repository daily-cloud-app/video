#!/bin/bash
# Daily Cloud Photo 窶・Azure Deployment Script
# Deploys the complete backend infrastructure and function app code.
#
# Prerequisites:
#   - Azure CLI installed and logged in (az login)
#   - zip command available (pre-installed in Cloud Shell)
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh [RESOURCE_GROUP] [LOCATION] [APP_NAME]

set -e

# 笏笏 Disable interactive extension prompts 笏笏
az config set extension.use_dynamic_install=no_without_prompt >/dev/null 2>&1 || true

# 笏笏 Configuration 笏笏
RESOURCE_GROUP="${1:-daily-cloud-photo-rg}"
LOCATION="${2:-eastus}"
APP_NAME="${3:-dailycloudphoto}"
TEMPLATE_FILE="./azuredeploy.json"
FUNCTION_APP_DIR="./function_app"

echo "=============================================="
echo " Daily Cloud Photo 窶・Azure Deployment"
echo "=============================================="
echo ""
echo " Resource Group: $RESOURCE_GROUP"
echo " Location:       $LOCATION"
echo " App Name:       $APP_NAME"
echo ""

# ============================================================
# Utility Functions
# ============================================================

# 笏笏 ensure_provider_registered 笏笏
# Ensures a resource provider namespace is registered.
# Usage: ensure_provider_registered "Microsoft.Quota"
ensure_provider_registered() {
    local namespace="$1"
    local max_wait="${2:-300}"  # default 5 minutes
    local interval=5

    local state
    state=$(az provider show --namespace "$namespace" --query "registrationState" -o tsv 2>/dev/null || echo "NotRegistered")

    if [ "$state" = "Registered" ]; then
        return 0
    fi

    echo "  Registering resource provider: $namespace ..."
    az provider register --namespace "$namespace" 2>/dev/null || true

    local elapsed=0
    while [ "$elapsed" -lt "$max_wait" ]; do
        state=$(az provider show --namespace "$namespace" --query "registrationState" -o tsv 2>/dev/null || echo "")
        if [ "$state" = "Registered" ]; then
            echo "  $namespace registered."
            return 0
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done

    echo "ERROR: Timed out waiting for $namespace to register (${max_wait}s)."
    echo "  Current state: $state"
    echo "  Try again later or register manually:"
    echo "    az provider register --namespace $namespace --wait"
    exit 1
}

# 笏笏 ensure_appservice_quota 笏笏
# Checks and (if needed) increases the App Service plan quota for a given SKU.
# This prevents SubscriptionIsOverQuotaForSku during ARM deployment.
#
# Usage: ensure_appservice_quota "Y1" 1
#   $1 = SKU resource name (e.g. Y1, B1, EP1)
#   $2 = minimum required limit (default: 1)
ensure_appservice_quota() {
    local sku_name="${1:-Y1}"
    local required_limit="${2:-1}"
    local max_wait=300  # 5 minutes
    local interval=10

    local subscription_id
    subscription_id=$(az account show --query id -o tsv)

    local scope="/subscriptions/${subscription_id}/providers/Microsoft.Web/locations/${LOCATION}"

    echo "  Checking App Service quota: $sku_name (location: $LOCATION) ..."

    # Ensure the quota extension is available
    if ! az extension show --name quota >/dev/null 2>&1; then
        echo "  Installing Azure CLI quota extension..."
        az extension add --name quota --allow-preview true --yes 2>/dev/null || {
            echo "ERROR: Failed to install the 'quota' extension for Azure CLI."
            echo "  Try manually: az extension add --name quota --allow-preview true"
            exit 1
        }
    fi

    # Get current quota for the SKU
    local current_limit
    current_limit=$(az quota show \
        --resource-name "$sku_name" \
        --scope "$scope" \
        --query "properties.limit.value" \
        -o tsv 2>/dev/null || echo "")

    # If we can't read the quota, try listing all and grep
    if [ -z "$current_limit" ] || [ "$current_limit" = "None" ]; then
        current_limit=$(az quota list \
            --scope "$scope" \
            --query "[?properties.name.value=='$sku_name'].properties.limit.value | [0]" \
            -o tsv 2>/dev/null || echo "")
    fi

    if [ -z "$current_limit" ] || [ "$current_limit" = "None" ]; then
        echo "  WARNING: Could not retrieve current quota for $sku_name."
        echo "  The Microsoft.Quota API may not support Microsoft.Web in this region."
        echo "  Proceeding with ARM deployment 窶・it may fail if quota is 0."
        echo ""
        echo "  If deployment fails with SubscriptionIsOverQuotaForSku:"
        echo "    1. Go to https://portal.azure.com 竊・Quotas 竊・Compute"
        echo "    2. Request an increase for Dynamic VM quota in '$LOCATION'"
        echo "    3. Or try a different region (e.g., eastus, westeurope)"
        echo ""
        return 0
    fi

    echo "  Current $sku_name quota: $current_limit (required: >= $required_limit)"

    if [ "$current_limit" -ge "$required_limit" ] 2>/dev/null; then
        echo "  Quota is sufficient."
        return 0
    fi

    # Quota is insufficient 窶・request an increase
    echo "  Requesting quota increase: $sku_name 竊・$required_limit ..."

    local update_result
    update_result=$(az quota update \
        --resource-name "$sku_name" \
        --scope "$scope" \
        --limit-object value="$required_limit" \
        --no-wait false \
        -o json 2>&1)

    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        echo "ERROR: Failed to update quota for $sku_name."
        echo "$update_result"
        echo ""
        echo "  Possible causes:"
        echo "    - Subscription type doesn't allow self-service quota increase"
        echo "    - Microsoft.Quota API doesn't support Microsoft.Web in this region"
        echo ""
        echo "  Manual fix:"
        echo "    1. Go to https://portal.azure.com 竊・Quotas"
        echo "    2. Search for 'Dynamic' or '$sku_name' under Compute"
        echo "    3. Request increase to at least $required_limit"
        echo "    4. Re-run this script after approval"
        exit 1
    fi

    echo "  Quota update request submitted. Waiting for propagation..."

    # Poll until quota is updated
    local elapsed=0
    while [ "$elapsed" -lt "$max_wait" ]; do
        sleep "$interval"
        elapsed=$((elapsed + interval))

        current_limit=$(az quota show \
            --resource-name "$sku_name" \
            --scope "$scope" \
            --query "properties.limit.value" \
            -o tsv 2>/dev/null || echo "0")

        if [ "$current_limit" -ge "$required_limit" ] 2>/dev/null; then
            echo "  $sku_name quota is now $current_limit. Ready to deploy."
            return 0
        fi
        echo "  Still waiting... ($elapsed/${max_wait}s, current: $current_limit)"
    done

    echo "ERROR: Timed out waiting for quota increase to take effect."
    echo "  Check status: az quota request list --scope \"$scope\""
    echo "  Or visit: https://portal.azure.com 竊・Quotas"
    exit 1
}

# ============================================================
# Step 1: Check prerequisites
# ============================================================
echo "[1/8] Checking prerequisites..."

if ! command -v az &> /dev/null; then
    echo "ERROR: Azure CLI (az) is not installed."
    echo "Install: https://docs.microsoft.com/en-us/cli/azure/install-azure-cli"
    exit 1
fi

# Check if logged in
if ! az account show &> /dev/null; then
    echo "Not logged in to Azure. Running 'az login'..."
    az login
fi

echo "  Logged in as: $(az account show --query user.name -o tsv)"
echo "  Subscription: $(az account show --query name -o tsv)"
echo ""

# ============================================================
# Step 2: Register resource providers
# ============================================================
echo "[2/8] Registering resource providers..."

ensure_provider_registered "Microsoft.Web"
ensure_provider_registered "Microsoft.DocumentDB"
ensure_provider_registered "microsoft.operationalinsights"
ensure_provider_registered "Microsoft.Quota"

echo "  All providers registered."
echo ""

# ============================================================
# Step 3: Ensure App Service quota
# ============================================================
echo "[3/8] Ensuring App Service quota (Y1 Dynamic) ..."

ensure_appservice_quota "Y1" 1

echo ""

# ============================================================
# Step 4: Create Resource Group
# ============================================================
echo "[4/8] Creating resource group..."

az group create \
    --name "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --output none
echo "  Resource group '$RESOURCE_GROUP' ready."
echo ""

# ============================================================
# Step 5: Deploy ARM Template
# ============================================================
echo "[5/8] Deploying ARM template (this may take 3-5 minutes)..."
DEPLOYMENT_OUTPUT=$(az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --template-file "$TEMPLATE_FILE" \
    --parameters appName="$APP_NAME" \
    --query "properties.outputs" \
    --output json)

FUNCTION_APP_NAME=$(echo "$DEPLOYMENT_OUTPUT" | python3 -c "import sys, json; print(json.load(sys.stdin)['functionAppName']['value'])" 2>/dev/null || echo "")
API_ENDPOINT=$(echo "$DEPLOYMENT_OUTPUT" | python3 -c "import sys, json; print(json.load(sys.stdin)['apiEndpoint']['value'])" 2>/dev/null || echo "")
STORAGE_ACCOUNT=$(echo "$DEPLOYMENT_OUTPUT" | python3 -c "import sys, json; print(json.load(sys.stdin)['storageAccountName']['value'])" 2>/dev/null || echo "")

if [ -z "$FUNCTION_APP_NAME" ]; then
    echo "ERROR: Deployment failed. Check the Azure Portal for details."
    exit 1
fi

echo "  Function App: $FUNCTION_APP_NAME"
echo "  API Endpoint: $API_ENDPOINT"
echo "  Storage:      $STORAGE_ACCOUNT"
echo ""

# ============================================================
# Step 6: Deploy Function App Code
# ============================================================
echo "[6/8] Deploying function app code..."

# ARM 繝・・繝ｭ繧､逶ｴ蠕後・ Function App 縺ｮ貅門ｙ縺悟ｮ御ｺ・＠縺ｦ縺・↑縺・ｴ蜷医′縺ゅｋ
echo "  Waiting for Function App to be ready..."
RETRY=0
while [ $RETRY -lt 24 ]; do
    APP_STATE=$(az functionapp show --name "$FUNCTION_APP_NAME" --resource-group "$RESOURCE_GROUP" --query "state" -o tsv 2>/dev/null || echo "")
    if [ "$APP_STATE" = "Running" ]; then
        # SCM 繧ｵ繧､繝茨ｼ・udu・峨′蠢懃ｭ斐☆繧九∪縺ｧ霑ｽ蜉縺ｧ蠕・▽
        SCM_URL="https://${FUNCTION_APP_NAME}.scm.azurewebsites.net/"
        SCM_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$SCM_URL" 2>/dev/null || echo "000")
        if [ "$SCM_CODE" != "000" ] && [ "$SCM_CODE" != "502" ] && [ "$SCM_CODE" != "503" ]; then
            echo "  Function App is ready (state=$APP_STATE, SCM=$SCM_CODE)."
            break
        fi
    fi
    sleep 5
    RETRY=$((RETRY+1))
done
if [ $RETRY -ge 24 ]; then
    echo "  WARNING: Function App may not be fully ready, attempting deploy anyway..."
fi

# az CLI 縺ｮ zip 繝・・繝ｭ繧､繧剃ｽｿ逕ｨ・・unc 繝・・繝ｫ縺ｮ squashfs 繝・・繝ｭ繧､縺ｯ
# WEBSITE_CONTENT* 險ｭ螳壹ｒ蜑企勁縺吶ｋ縺溘ａ Blob Trigger 縺悟虚菴懊＠縺ｪ縺上↑繧具ｼ・echo "  Creating deployment package..."
DEPLOY_ZIP="/tmp/daily-cloud-photo-azure.zip"
rm -f "$DEPLOY_ZIP"

cd "$FUNCTION_APP_DIR"
zip -r "$DEPLOY_ZIP" . -x "__pycache__/*" "*.pyc" ".venv/*" ".git/*"
cd ..

echo "  Uploading to Azure (remote build)..."
az functionapp deployment source config-zip \
    --resource-group "$RESOURCE_GROUP" \
    --name "$FUNCTION_APP_NAME" \
    --src "$DEPLOY_ZIP" \
    --build-remote true \
    --output none

rm -f "$DEPLOY_ZIP"

echo "  Code deployed successfully."
echo ""

# ============================================================
# Step 7: Create Event Grid Subscription for Storage Trigger
# ============================================================
echo "[7/8] Setting up Event Grid subscription for blob trigger..."

# Register Event Grid provider
az provider register --namespace Microsoft.EventGrid 2>/dev/null || true

# blobs_extension 繧ｷ繧ｹ繝・Β繧ｭ繝ｼ繧貞叙蠕暦ｼ・ebhook 隱崎ｨｼ逕ｨ・・echo "  Waiting for function keys to be available..."
BLOB_KEY=""
KEY_RETRY=0
while [ -z "$BLOB_KEY" ] && [ $KEY_RETRY -lt 12 ]; do
    BLOB_KEY=$(az functionapp keys list --name "$FUNCTION_APP_NAME" --resource-group "$RESOURCE_GROUP" --query "systemKeys.eventgrid_extension" -o tsv 2>/dev/null || echo "")
    if [ -z "$BLOB_KEY" ] || [ "$BLOB_KEY" = "null" ]; then
        BLOB_KEY=""
        sleep 10
        KEY_RETRY=$((KEY_RETRY+1))
    fi
done

if [ -z "$BLOB_KEY" ]; then
    echo "  WARNING: Could not retrieve eventgrid_extension key."
    echo "  Event Grid subscription must be created manually."
    echo "  See: https://learn.microsoft.com/en-us/azure/azure-functions/functions-event-grid-blob-trigger"
else
    ENDPOINT_URL="https://${FUNCTION_APP_NAME}.azurewebsites.net/runtime/webhooks/eventgrid?functionName=process_photo&code=${BLOB_KEY}"
    STORAGE_ID=$(az storage account show --name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" --query "id" -o tsv 2>/dev/null)

    # 譌｢蟄倥・繧ｵ繝悶せ繧ｯ繝ｪ繝励す繝ｧ繝ｳ縺後≠繧後・蜑企勁縺励※蜀堺ｽ懈・
    az eventgrid event-subscription delete \
        --name photo-upload-trigger \
        --source-resource-id "$STORAGE_ID" \
        --output none 2>/dev/null || true

    az eventgrid event-subscription create \
        --name photo-upload-trigger \
        --source-resource-id "$STORAGE_ID" \
        --endpoint "$ENDPOINT_URL" \
        --endpoint-type webhook \
        --included-event-types Microsoft.Storage.BlobCreated \
        --subject-begins-with "/blobServices/default/containers/photos/blobs/users/" \
        --output none 2>/dev/null

    if [ $? -eq 0 ]; then
        echo "  Event Grid subscription created."
    else
        echo "  WARNING: Failed to create Event Grid subscription."
        echo "  You may need to create it manually in the Azure Portal."
    fi
fi
echo ""

# ============================================================
# Step 8: Wait for function app to start & summary
# ============================================================
echo "[8/8] Waiting for function app to start..."
sleep 10

# Test the /info endpoint
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$API_ENDPOINT/info" 2>/dev/null || echo "000")
RETRY=0
while [ "$HTTP_CODE" != "200" ] && [ $RETRY -lt 6 ]; do
    echo "  Waiting for app to respond (attempt $((RETRY+1))/6)..."
    sleep 10
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$API_ENDPOINT/info" 2>/dev/null || echo "000")
    RETRY=$((RETRY+1))
done

if [ "$HTTP_CODE" = "200" ]; then
    echo "  Function app is running!"
else
    echo "  WARNING: Function app not responding yet (HTTP $HTTP_CODE)."
    echo "  It may take a few more minutes for cold start. Check Azure Portal."
fi
echo ""

echo "=============================================="
echo " DEPLOYMENT SUMMARY"
echo "=============================================="
echo ""
echo " API Endpoint:    $API_ENDPOINT"
echo " Function App:    $FUNCTION_APP_NAME"
echo " Resource Group:  $RESOURCE_GROUP"
echo ""
echo " To connect the app:"
echo "   1. Open app -> Drawer -> Settings"
echo "   2. Enter: $API_ENDPOINT"
echo "   3. Save and run Connection Test"
echo ""
echo " To view logs:"
echo "   az functionapp log tail --name $FUNCTION_APP_NAME --resource-group $RESOURCE_GROUP"
echo ""
echo " To delete all resources:"
echo "   az group delete --name $RESOURCE_GROUP --yes --no-wait"
echo ""
echo "=============================================="
