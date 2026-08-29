#!/bin/bash
# Daily Cloud Video — Azure deployment (Bicep + Microsoft Entra External ID)
#
# This script is a thin wrapper around:
#   1. Bicep     — provisions all Azure infrastructure (Flex Consumption).
#   2. Graph API — configures the Entra External ID app registration.
#   3. OneDeploy — publishes the function code to the Flex deployment container.
#
# Authentication is delegated to Microsoft Entra External ID (native
# authentication). This script never creates a JWT secret and never uses the
# legacy "az functionapp deployment source config-zip" content-share flow.
#
# Prerequisites:
#   - Azure CLI (az) logged in:  az login
#   - zip, curl, python3 (pre-installed in Cloud Shell)
#   - An existing Microsoft Entra *external* tenant (see README). Creating an
#     external tenant is not scriptable end-to-end, so its subdomain + id are
#     taken as input; everything after that is automated.
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
#
# Configuration is read from environment variables (with sensible defaults):
#   RESOURCE_GROUP        (default: daily-cloud-video-rg)
#   LOCATION              (default: eastus)  — must be a Flex Consumption region
#   APP_NAME              (default: dailycloudvideo)
#   ENTRA_TENANT_SUBDOMAIN   external tenant subdomain (e.g. "contoso")
#   ENTRA_TENANT_ID          external tenant (directory) GUID
#   ENTRA_CLIENT_ID          (optional) existing app registration to reuse
#   ENTRA_APP_DISPLAY_NAME   (default: "Daily Cloud Video (native auth)")

set -euo pipefail

# ── Disable interactive extension prompts ──
az config set extension.use_dynamic_install=no_without_prompt >/dev/null 2>&1 || true

# ── Configuration ──
RESOURCE_GROUP="${RESOURCE_GROUP:-daily-cloud-video-rg}"
LOCATION="${LOCATION:-eastus}"
APP_NAME="${APP_NAME:-dailycloudvideo}"
BICEP_MAIN="./bicep/main.bicep"
FUNCTION_APP_DIR="./function_app"

# Entra External ID inputs.
ENTRA_TENANT_SUBDOMAIN="${ENTRA_TENANT_SUBDOMAIN:-}"
ENTRA_TENANT_ID="${ENTRA_TENANT_ID:-}"
ENTRA_CLIENT_ID="${ENTRA_CLIENT_ID:-}"
ENTRA_APP_DISPLAY_NAME="${ENTRA_APP_DISPLAY_NAME:-Daily Cloud Video (native auth)}"
ENTRA_USER_FLOW_NAME="${ENTRA_USER_FLOW_NAME:-DailyCloudVideoSignUpSignIn}"

# External ID tenant handling. By default (no env vars) deploy.sh creates a new
# external (CIAM) tenant so a plain `./deploy.sh` works end to end. To reuse an
# existing tenant, pass ENTRA_TENANT_ID=<guid>; creation is then skipped
# automatically. Set CREATE_TENANT=false to force skipping creation.
CREATE_TENANT="${CREATE_TENANT:-true}"
TENANT_RESOURCE_NAME="${TENANT_RESOURCE_NAME:-dcv$(date +%s | tail -c 8)}"
TENANT_DISPLAY_NAME="${TENANT_DISPLAY_NAME:-Daily Cloud Video External ID}"
TENANT_DATA_LOCATION="${TENANT_DATA_LOCATION:-United States}"
TENANT_COUNTRY_CODE="${TENANT_COUNTRY_CODE:-US}"

echo "=============================================="
echo " Daily Cloud Video — Azure Deployment (Bicep)"
echo "=============================================="
echo ""
echo " Resource Group: $RESOURCE_GROUP"
echo " Location:       $LOCATION"
echo " App Name:       $APP_NAME"
echo " External tenant: ${ENTRA_TENANT_SUBDOMAIN:-<not set>}"
echo ""

# ============================================================
# Step 1: Prerequisites
# ============================================================
echo "[1/7] Checking prerequisites..."

if ! command -v az &> /dev/null; then
    echo "ERROR: Azure CLI (az) is not installed."
    echo "Install: https://learn.microsoft.com/cli/azure/install-azure-cli"
    exit 1
fi

if ! az account show &> /dev/null; then
    echo "Not logged in to Azure. Running 'az login'..."
    az login
fi

echo "  Logged in as: $(az account show --query user.name -o tsv)"
echo "  Subscription: $(az account show --query name -o tsv)"

# Remember a REAL subscription so we can pin it on every infrastructure-side az
# call. This matters because the external-tenant login in Step 3 switches the
# active CLI context to a tenant-level account (no subscription), where
# 'az account show --query id' returns the tenant id, not a subscription id.
# We therefore pick an enabled subscription whose id differs from its tenant id.
if [ -z "${AZURE_SUBSCRIPTION_ID:-}" ]; then
    AZURE_SUBSCRIPTION_ID=$(az account list --all --query \
        "[?state=='Enabled' && id!=tenantId] | [0].id" -o tsv 2>/dev/null || echo "")
fi
if [ -z "$AZURE_SUBSCRIPTION_ID" ]; then
    echo "  ERROR: No usable Azure subscription found for the signed-in user."
    echo "  Run 'az login' with an account that has an active subscription."
    exit 1
fi
SUB_ARG=(--subscription "$AZURE_SUBSCRIPTION_ID")
echo "  Using subscription: $AZURE_SUBSCRIPTION_ID"
echo ""

# ============================================================
# Step 2: Register resource providers
# ============================================================
echo "[2/7] Registering resource providers..."

register_provider() {
    local ns="$1"
    local state
    state=$(az provider show --namespace "$ns" --query registrationState -o tsv 2>/dev/null || echo "NotRegistered")
    if [ "$state" != "Registered" ]; then
        echo "  Registering $ns ..."
        az provider register --namespace "$ns" --wait 2>/dev/null || true
    fi
}

register_provider "Microsoft.Web"
register_provider "Microsoft.DocumentDB"
register_provider "Microsoft.OperationalInsights"
register_provider "Microsoft.Insights"
register_provider "Microsoft.ManagedIdentity"
register_provider "Microsoft.EventGrid"
echo "  Providers ready."
echo ""

# ============================================================
# Step 3: Provision & configure Entra External ID (Bicep + Graph)
# ============================================================
echo "[3/7] Provisioning & configuring Microsoft Entra External ID..."

# Call Microsoft Graph with the external-tenant token.
# Usage: graph_call <METHOD> <URL> [<json-body>]
#   - prints the response body on stdout
#   - returns 0 only for HTTP 2xx; non-2xx returns 1 and logs the status/body
#     to stderr so callers can reliably detect failures (no silent success).
# We append the HTTP status as a trailing line (via -w) and split it off, which
# works across curl versions (older curl lacks --fail-with-body).
GRAPH_TOKEN=""
graph_call() {
    local method="$1" url="$2" body="${3:-}"
    local raw http_code resp
    if [ -n "$body" ]; then
        raw=$(curl -sS -w $'\n%{http_code}' -X "$method" "$url" \
            -H "Authorization: Bearer $GRAPH_TOKEN" \
            -H "Content-Type: application/json" \
            -d "$body")
    else
        raw=$(curl -sS -w $'\n%{http_code}' -X "$method" "$url" \
            -H "Authorization: Bearer $GRAPH_TOKEN")
    fi
    http_code="${raw##*$'\n'}"   # last line
    resp="${raw%$'\n'*}"         # everything before the last line
    printf '%s' "$resp"
    case "$http_code" in
        2*) return 0 ;;
        *)
            echo "  Graph $method $url -> HTTP $http_code" >&2
            [ -n "$resp" ] && echo "  Response: $resp" >&2
            return 1
            ;;
    esac
}

json_get() {
    # json_get <key> — read a top-level key from stdin JSON.
    python3 -c "import sys,json
try:
    print(json.load(sys.stdin).get('$1',''))
except Exception:
    print('')" 2>/dev/null || echo ""
}

# ── 3a. Create the external tenant (opt-in) ──────────────────
# The external (CIAM) tenant is created via the ARM REST API directly, not
# Bicep: the ciamDirectories preview provider is not correctly handled by the
# Bicep/ARM template path (it fails preflight with an empty domain suffix),
# whereas the raw PUT succeeds. It requires a DELEGATED user token, so it runs
# as the currently signed-in user. Skipped entirely when an existing tenant is
# provided. The SKU name must be 'Base' (tier 'A0').
if [ "$CREATE_TENANT" = "true" ] && [ -z "$ENTRA_TENANT_ID" ]; then
    echo "  Creating external (CIAM) tenant '$TENANT_DISPLAY_NAME' (name=$TENANT_RESOURCE_NAME)..."
    az group create --name "$RESOURCE_GROUP" --location "$LOCATION" "${SUB_ARG[@]}" --output none

    CIAM_URL="https://management.azure.com/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.AzureActiveDirectory/ciamDirectories/$TENANT_RESOURCE_NAME?api-version=2023-05-17-preview"
    CIAM_BODY=$(python3 - "$TENANT_DATA_LOCATION" "$TENANT_DISPLAY_NAME" "$TENANT_COUNTRY_CODE" <<'PY'
import json, sys
loc, display, cc = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({
    "location": loc,
    "sku": {"name": "Base", "tier": "A0"},
    "properties": {"createTenantProperties": {"displayName": display, "countryCode": cc}},
}))
PY
)

    CREATE_RESP=$(az rest --method PUT --url "$CIAM_URL" --body "$CIAM_BODY" -o json 2>&1) || {
        echo "  ERROR: External tenant creation failed:"
        echo "  $CREATE_RESP"
        exit 1
    }

    # Creation is asynchronous. Poll until provisioningState is Succeeded and a
    # real tenantId is assigned.
    echo "  Waiting for the tenant to finish provisioning..."
    ENTRA_TENANT_ID=""
    for _ in $(seq 1 30); do
        POLL=$(az rest --method GET --url "$CIAM_URL" -o json 2>/dev/null || echo "{}")
        STATE=$(echo "$POLL" | python3 -c "import sys,json;print((json.load(sys.stdin).get('properties') or {}).get('provisioningState',''))" 2>/dev/null || echo "")
        TID=$(echo "$POLL" | python3 -c "import sys,json;print((json.load(sys.stdin).get('properties') or {}).get('tenantId',''))" 2>/dev/null || echo "")
        if [ "$STATE" = "Succeeded" ] && [ -n "$TID" ] && [ "$TID" != "00000000-0000-0000-0000-000000000000" ]; then
            ENTRA_TENANT_ID="$TID"
            break
        fi
        if [ "$STATE" = "Failed" ] || [ "$STATE" = "Canceled" ]; then
            echo "  ERROR: Tenant provisioning reported state: $STATE"
            exit 1
        fi
        sleep 10
    done

    if [ -z "$ENTRA_TENANT_ID" ]; then
        echo "  ERROR: Tenant did not finish provisioning in time."
        echo "  Check the resource '$TENANT_RESOURCE_NAME' in resource group '$RESOURCE_GROUP'."
        exit 1
    fi
    echo "  External tenant created. tenant_id=$ENTRA_TENANT_ID"
fi

if [ -z "$ENTRA_TENANT_ID" ]; then
    echo "  ERROR: No external tenant available."
    echo "  Either set CREATE_TENANT=true to create one, or provide an existing"
    echo "  ENTRA_TENANT_ID (and ideally ENTRA_TENANT_SUBDOMAIN / ENTRA_CLIENT_ID)."
    exit 1
fi

# ── 3b. Make the external tenant the ACTIVE CLI context ──────
# Everything in Step 3 (creating the temporary Graph app, the app registration,
# the user flow) must target the external tenant. It is not enough to merely be
# able to fetch a token for it — the active `az` context (used by `az ad ...`)
# must be the external tenant, otherwise the temporary app gets created in the
# home tenant and its token lacks the external-tenant roles.
#
# Prefer switching to an already-signed-in account for this tenant; otherwise
# perform a one-time interactive login.
if ! az account set --subscription "$ENTRA_TENANT_ID" --only-show-errors 2>/dev/null; then
    # Try to find any existing account whose tenant is the external tenant.
    EXT_ACCOUNT=$(az account list --all \
        --query "[?tenantId=='$ENTRA_TENANT_ID'] | [0].id" -o tsv 2>/dev/null || echo "")
    if [ -n "$EXT_ACCOUNT" ]; then
        az account set --subscription "$EXT_ACCOUNT" --only-show-errors 2>/dev/null || true
    fi
fi

# Verify the active context is now the external tenant; if not, sign in.
ACTIVE_TENANT=$(az account show --query tenantId -o tsv 2>/dev/null || echo "")
if [ "$ACTIVE_TENANT" != "$ENTRA_TENANT_ID" ]; then
    echo "  A one-time sign-in to the external tenant is required."
    echo "  Launching: az login --tenant $ENTRA_TENANT_ID --allow-no-subscriptions"
    az login --tenant "$ENTRA_TENANT_ID" --allow-no-subscriptions --only-show-errors >/dev/null
    ACTIVE_TENANT=$(az account show --query tenantId -o tsv 2>/dev/null || echo "")
fi

if [ "$ACTIVE_TENANT" != "$ENTRA_TENANT_ID" ]; then
    echo "  ERROR: Could not activate the external tenant context ($ENTRA_TENANT_ID)."
    echo "  Run 'az login --tenant $ENTRA_TENANT_ID --allow-no-subscriptions' and retry."
    exit 1
fi

GRAPH_TOKEN=$(az account get-access-token --tenant "$ENTRA_TENANT_ID" \
    --resource-type ms-graph --query accessToken -o tsv 2>/dev/null || echo "")
if [ -z "$GRAPH_TOKEN" ]; then
    echo "  ERROR: Could not obtain a Microsoft Graph token for the external tenant."
    exit 1
fi

# Resolve the tenant subdomain from its verified onmicrosoft.com domain when
# not explicitly provided (Native Auth base URL needs the subdomain).
if [ -z "$ENTRA_TENANT_SUBDOMAIN" ]; then
    DOMAINS_JSON=$(graph_call GET "https://graph.microsoft.com/v1.0/domains")
    ENTRA_TENANT_SUBDOMAIN=$(echo "$DOMAINS_JSON" | python3 -c "
import sys,json
try:
    doms=[d['id'] for d in json.load(sys.stdin).get('value',[])]
    onms=[d for d in doms if d.endswith('.onmicrosoft.com')]
    print(onms[0].split('.onmicrosoft.com')[0] if onms else '')
except Exception:
    print('')" 2>/dev/null || echo "")
    [ -n "$ENTRA_TENANT_SUBDOMAIN" ] && echo "  Resolved tenant subdomain: $ENTRA_TENANT_SUBDOMAIN"
fi

# ── 3b2. Graph automation app (application permissions) ──────
# The Azure CLI first-party token lacks the high-privilege delegated scopes
# (Policy.ReadWrite.AuthenticationMethod, EventListener.ReadWrite.All) required
# to configure the Email OTP policy and user flows — even for a Global Admin.
# So we provision a short-lived dedicated app with those APPLICATION permissions,
# grant admin consent, and use its client-credentials token for the Graph calls
# below. The app is deleted at the end of Step 3.
GRAPH_MSGRAPH_APPID="00000003-0000-0000-c000-000000000000"
GRAPH_AUTOMATION_APPID=""
GRAPH_AUTOMATION_OBJECT_ID=""

echo "  Provisioning temporary Graph automation app (application permissions)..."
AUTO_APP=$(az ad app create --display-name "DCV Graph Automation (temp)" \
    --sign-in-audience AzureADMyOrg -o json 2>/dev/null || echo "")
GRAPH_AUTOMATION_APPID=$(echo "$AUTO_APP" | json_get appId)
GRAPH_AUTOMATION_OBJECT_ID=$(echo "$AUTO_APP" | json_get id)

if [ -z "$GRAPH_AUTOMATION_APPID" ]; then
    echo "  ERROR: Could not create the Graph automation app."
    exit 1
fi

az ad sp create --id "$GRAPH_AUTOMATION_APPID" >/dev/null 2>&1 || true
AUTO_SPID=$(az ad sp show --id "$GRAPH_AUTOMATION_APPID" --query id -o tsv 2>/dev/null || echo "")
GRAPH_SP_ID=$(az ad sp show --id "$GRAPH_MSGRAPH_APPID" --query id -o tsv)

# Resolve the app-role (application permission) ids from the Graph SP.
role_id() { az ad sp show --id "$GRAPH_MSGRAPH_APPID" --query "appRoles[?value=='$1'].id | [0]" -o tsv; }
ROLE_POLICY=$(role_id "Policy.ReadWrite.AuthenticationMethod")
ROLE_FLOW=$(role_id "EventListener.ReadWrite.All")
ROLE_APP=$(role_id "Application.ReadWrite.All")
# Required to create the oauth2PermissionGrant (tenant-wide admin consent) for
# the app. Application.ReadWrite.All does NOT cover writing delegated grants,
# so without this role the POST fails with 403 Authorization_RequestDenied
# and sign-in later fails with consent_required.
ROLE_GRANT=$(role_id "DelegatedPermissionGrant.ReadWrite.All")

# Grant admin consent by assigning the app roles to the automation SP.
grant_role() {
    az rest --method POST \
        --url "https://graph.microsoft.com/v1.0/servicePrincipals/$AUTO_SPID/appRoleAssignments" \
        --headers "Content-Type=application/json" \
        --body "{\"principalId\":\"$AUTO_SPID\",\"resourceId\":\"$GRAPH_SP_ID\",\"appRoleId\":\"$1\"}" \
        --only-show-errors >/dev/null 2>&1 || true
}
grant_role "$ROLE_POLICY"
grant_role "$ROLE_FLOW"
grant_role "$ROLE_APP"
grant_role "$ROLE_GRANT"

# Short-lived client secret for the client-credentials grant.
AUTO_SECRET=$(az ad app credential reset --id "$GRAPH_AUTOMATION_APPID" --append \
    --query password -o tsv --only-show-errors 2>/dev/null || echo "")
if [ -z "$AUTO_SECRET" ]; then
    echo "  ERROR: Could not create a client secret for the automation app."
    exit 1
fi

# Delete the automation app on exit (best effort), so no standing credential
# is left behind after deployment.
cleanup_graph_app() {
    [ -n "$GRAPH_AUTOMATION_OBJECT_ID" ] && \
        az ad app delete --id "$GRAPH_AUTOMATION_OBJECT_ID" --only-show-errors 2>/dev/null || true
}
trap cleanup_graph_app EXIT

# Acquire the client-credentials token (retry until consent propagates).
echo "  Waiting for admin consent to propagate..."
GRAPH_TOKEN=""
for _ in $(seq 1 12); do
    sleep 10
    GRAPH_TOKEN=$(curl -s -X POST \
        "https://login.microsoftonline.com/$ENTRA_TENANT_ID/oauth2/v2.0/token" \
        -d "client_id=$GRAPH_AUTOMATION_APPID" \
        -d "scope=https://graph.microsoft.com/.default" \
        -d "client_secret=$AUTO_SECRET" \
        -d "grant_type=client_credentials" \
        | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || echo "")
    # Verify the token actually carries the required app roles before using it.
    if [ -n "$GRAPH_TOKEN" ]; then
        HAS_ROLE=$(echo "$GRAPH_TOKEN" | cut -d. -f2 | tr '_-' '/+' | \
            python3 -c "
import sys,base64,json
required={'Policy.ReadWrite.AuthenticationMethod','EventListener.ReadWrite.All','Application.ReadWrite.All','DelegatedPermissionGrant.ReadWrite.All'}
s=sys.stdin.read().strip()
s+='='*(-len(s)%4)
try:
    d=json.loads(base64.b64decode(s))
    roles=set(d.get('roles') or [])
    print('yes' if required.issubset(roles) else 'no')
except Exception:
    print('no')" 2>/dev/null || echo "no")
        [ "$HAS_ROLE" = "yes" ] && break
        GRAPH_TOKEN=""
    fi
done

if [ -z "$GRAPH_TOKEN" ]; then
    echo "  ERROR: Could not obtain an app token with the required Graph roles."
    echo "  Admin consent may not have propagated. Re-run in a minute."
    exit 1
fi
echo "  Graph automation token ready."

# ── 3c. App registration (public client + native authentication) ──
if [ -n "$ENTRA_CLIENT_ID" ]; then
    echo "  Using existing app registration: $ENTRA_CLIENT_ID"
    APP_JSON=$(graph_call GET "https://graph.microsoft.com/v1.0/applications(appId='$ENTRA_CLIENT_ID')"); RC=$?
    if [ $RC -ne 0 ]; then
        echo "  ERROR: Could not read the existing app registration ($ENTRA_CLIENT_ID)."
        exit 1
    fi
    ENTRA_APP_OBJECT_ID=$(echo "$APP_JSON" | json_get id)
else
    echo "  Creating app registration '$ENTRA_APP_DISPLAY_NAME' ..."
    # Public client (isFallbackPublicClient) + native auth enabled. The
    # nativeAuthenticationApisEnabled flag turns on the native-auth API.
    APP_BODY=$(python3 - "$ENTRA_APP_DISPLAY_NAME" <<'PY'
import json, sys
print(json.dumps({
    "displayName": sys.argv[1],
    "signInAudience": "AzureADMyOrg",
    "isFallbackPublicClient": True,
    "publicClient": {"redirectUris": ["https://login.microsoftonline.com/common/oauth2/nativeclient"]},
    "nativeAuthenticationApisEnabled": "all",
}))
PY
)
    CREATED_APP=$(graph_call POST "https://graph.microsoft.com/v1.0/applications" "$APP_BODY"); RC=$?
    ENTRA_CLIENT_ID=$(echo "$CREATED_APP" | json_get appId)
    ENTRA_APP_OBJECT_ID=$(echo "$CREATED_APP" | json_get id)

    if [ $RC -ne 0 ] || [ -z "$ENTRA_CLIENT_ID" ]; then
        echo "  ERROR: App registration failed."
        exit 1
    fi
    echo "  App registration created. client_id=$ENTRA_CLIENT_ID"
fi

# Ensure native authentication is enabled on the app (idempotent for reuse).
# The create body already sets nativeAuthenticationApisEnabled, but we confirm
# it here (and it is required for the reuse path). A freshly created app object
# is eventually consistent across Graph replicas, so a PATCH immediately after
# creation can transiently return 404 — retry with backoff before giving up.
if [ -n "$ENTRA_APP_OBJECT_ID" ]; then
    NATIVE_OK=""
    for _ in $(seq 1 12); do
        if graph_call PATCH "https://graph.microsoft.com/v1.0/applications/$ENTRA_APP_OBJECT_ID" \
            '{"nativeAuthenticationApisEnabled":"all","isFallbackPublicClient":true}' >/dev/null 2>&1; then
            NATIVE_OK="yes"
            break
        fi
        sleep 5
    done
    if [ -z "$NATIVE_OK" ]; then
        echo "  ERROR: Failed to enable native authentication on the app (after retries)."
        exit 1
    fi
else
    echo "  ERROR: Could not resolve the app object id; cannot enable native auth."
    exit 1
fi

# The app registration needs a matching service principal in the tenant,
# otherwise associating it with a user flow fails with "application id ...
# is invalid". Create it if missing (idempotent) and wait until it is visible.
#
# sp_object_id() prints the SP id (or empty) and always returns 0, so a "not
# found" (404) never trips `set -e` / `pipefail`.
sp_object_id() {
    local out
    out=$(graph_call GET \
        "https://graph.microsoft.com/v1.0/servicePrincipals(appId='$ENTRA_CLIENT_ID')" 2>/dev/null || true)
    printf '%s' "$out" | json_get id
    return 0
}

echo "  Ensuring the app has a service principal..."
# A freshly created app registration is eventually consistent across Graph
# replicas. A single "create SP" POST can land on a replica that has not yet
# seen the app (404), leaving the SP permanently uncreated while the GET loop
# waits forever. Retry BOTH the create POST and the existence GET on every
# attempt (~3 minutes = 36 * 5s), so a replication gap on any one attempt is
# recovered by the next.
SP_EXISTS=""
for _ in $(seq 1 36); do
    SP_EXISTS="$(sp_object_id)"
    [ -n "$SP_EXISTS" ] && break
    graph_call POST "https://graph.microsoft.com/v1.0/servicePrincipals" \
        "{\"appId\":\"$ENTRA_CLIENT_ID\"}" >/dev/null 2>&1 || true
    SP_EXISTS="$(sp_object_id)"
    [ -n "$SP_EXISTS" ] && break
    sleep 5
done
if [ -z "$SP_EXISTS" ]; then
    echo "  ERROR: The app's service principal did not become available."
    exit 1
fi
echo "  Service principal ready."
APP_SP_ID="$SP_EXISTS"

# Grant tenant-wide admin consent so the app can be issued tokens. Without this,
# the final /oauth2/v2.0/token step during sign-in fails with AADSTS65001
# (consent_required) even though the username, password and flow are all valid.
# We create an oauth2PermissionGrant (AllPrincipals) for the delegated OpenID
# Connect scopes against Microsoft Graph. Idempotent: skip if one exists.
#
# A freshly created external (CIAM) tenant reports provisioningState=Succeeded
# before its directory "organization" object is fully initialized. Writes in
# that window fail with 404 Directory_ObjectNotFound
# ("Unable to read the company information from the directory"). We therefore
# (a) wait until GET /organization returns a record, and (b) treat the grant as
# successful whenever it EXISTS (checked before and after the POST), never
# trusting the POST status alone — a retry that hits 409 Conflict still means
# the grant is present. Retry window: up to ~8 minutes (48 * 10s).
echo "  Granting admin consent to the app (OpenID Connect scopes)..."
GRAPH_RES_SP_ID=$(graph_call GET \
    "https://graph.microsoft.com/v1.0/servicePrincipals(appId='$GRAPH_MSGRAPH_APPID')" 2>/dev/null | json_get id)

# Returns non-empty when an oauth2PermissionGrant exists for our SP.
#
# We read the SP navigation property servicePrincipals/{id}/oauth2PermissionGrants
# instead of the collection with $filter. On a freshly created tenant the
# $filter index lags behind replication for minutes, so a filtered query
# returns an empty list even when the grant already exists — which made the
# old check report a false failure and burn the whole retry window. The
# navigation property reflects the grant immediately.
grant_exists() {
    graph_call GET \
        "https://graph.microsoft.com/v1.0/servicePrincipals/$APP_SP_ID/oauth2PermissionGrants" 2>/dev/null \
        | python3 -c "import sys,json
try:
    print((json.load(sys.stdin).get('value') or [{}])[0].get('id',''))
except Exception:
    print('')" 2>/dev/null || echo ""
}

if [ -z "$GRAPH_RES_SP_ID" ]; then
    echo "  WARNING: Could not resolve the Microsoft Graph service principal; skipping consent."
else
    GRANT_BODY="{\"clientId\":\"$APP_SP_ID\",\"consentType\":\"AllPrincipals\",\"resourceId\":\"$GRAPH_RES_SP_ID\",\"scope\":\"openid offline_access profile\"}"
    # Consent is created by POSTing the grant directly and verifying existence.
    #
    # We do NOT gate this on GET /organization: that requires the app-only
    # Organization.Read.All permission (not granted to the automation app), so
    # it returns 403 forever and the write is never attempted. Instead, POST
    # directly and classify the HTTP status:
    #   - transient (freshly created tenant still initializing): 404
    #     Directory_ObjectNotFound, 429, 5xx  -> sleep and retry
    #   - 409 Conflict: the grant already exists -> re-check existence
    #   - hard failure: 400 / 401 / 403 -> log the body and stop retrying
    # DelegatedPermissionGrant.ReadWrite.All is sufficient for this POST per the
    # Graph docs; no additional Graph permission is needed here.
    CONSENT_OK=""
    CONSENT_FATAL=""
    for _ in $(seq 1 48); do
        # Already present (earlier attempt, or a prior run)? Done.
        if [ -n "$(grant_exists)" ]; then
            CONSENT_OK="yes"
            break
        fi
        # POST the grant and capture HTTP status + body (last line = status).
        RAW=$(curl -sS -w $'\n%{http_code}' -X POST \
            "https://graph.microsoft.com/v1.0/oauth2PermissionGrants" \
            -H "Authorization: Bearer $GRAPH_TOKEN" \
            -H "Content-Type: application/json" \
            -d "$GRANT_BODY" 2>/dev/null || true)
        HTTP_CODE="${RAW##*$'\n'}"
        RESP="${RAW%$'\n'*}"
        case "$HTTP_CODE" in
            2*)
                CONSENT_OK="yes"
                break
                ;;
            409)
                # Already exists — confirm via the navigation property.
                if [ -n "$(grant_exists)" ]; then
                    CONSENT_OK="yes"
                    break
                fi
                ;;
            404|429|5*)
                # Directory still initializing / throttled / transient. The
                # grant may still have landed, so re-check before waiting.
                if [ -n "$(grant_exists)" ]; then
                    CONSENT_OK="yes"
                    break
                fi
                ;;
            400|401|403)
                echo "  ERROR: admin consent POST failed with HTTP $HTTP_CODE"
                [ -n "$RESP" ] && echo "  Response: $(printf '%s' "$RESP" | head -c 500)"
                CONSENT_FATAL="yes"
                break
                ;;
            *)
                # Unknown/empty status — treat as transient and re-check.
                if [ -n "$(grant_exists)" ]; then
                    CONSENT_OK="yes"
                    break
                fi
                ;;
        esac
        sleep 10
    done
    if [ -n "$CONSENT_OK" ]; then
        echo "  Admin consent granted."
    elif [ -n "$CONSENT_FATAL" ]; then
        echo "  WARNING: Admin consent could not be granted (see error above); sign-in may fail with consent_required."
    else
        echo "  WARNING: Failed to grant admin consent after retries; sign-in may fail with consent_required."
    fi
fi

# ── 3d. Enable Email OTP tenant policy (required for SSPR) ──
# Email one-time passcode must be enabled tenant-wide so native credential
# recovery (self-service password reset) works. Idempotent PATCH.
echo "  Enabling Email OTP authentication method (SSPR prerequisite)..."
if ! graph_call PATCH \
    "https://graph.microsoft.com/v1.0/policies/authenticationMethodsPolicy/authenticationMethodConfigurations/email" \
    '{"@odata.type":"#microsoft.graph.emailAuthenticationMethodConfiguration","state":"enabled","allowExternalIdToUseEmailOtp":"enabled"}' >/dev/null; then
    echo "  ERROR: Failed to enable the Email OTP policy (SSPR prerequisite)."
    echo "  The automation app needs the 'Policy.ReadWrite.AuthenticationMethod' application permission."
    exit 1
fi

# ── 3e. Sign-up/sign-in user flow (Email+Password) + app association ──
# Idempotent: create the flow if absent; if a flow with our name already exists,
# converge its state by ensuring the current app is in includeApplications.
echo "  Configuring sign-up/sign-in user flow '$ENTRA_USER_FLOW_NAME'..."
# Note: the authenticationEventsFlows endpoint does not support $select, so we
# fetch the full list and match by displayName client-side.
FLOWS_JSON=$(graph_call GET \
    "https://graph.microsoft.com/v1.0/identity/authenticationEventsFlows"); RC=$?
if [ $RC -ne 0 ]; then
    echo "  ERROR: Could not list user flows."
    echo "  Ensure the automation app has the 'EventListener.ReadWrite.All' application permission."
    exit 1
fi
EXISTING_FLOW_ID=$(echo "$FLOWS_JSON" | python3 -c "
import sys,json
name='$ENTRA_USER_FLOW_NAME'
try:
    for f in json.load(sys.stdin).get('value',[]):
        if f.get('displayName')==name:
            print(f.get('id','')); break
except Exception:
    pass" 2>/dev/null || echo "")

if [ -n "$EXISTING_FLOW_ID" ]; then
    echo "  Found existing user flow (id=$EXISTING_FLOW_ID); converging configuration..."
    ENTRA_USER_FLOW_ID="$EXISTING_FLOW_ID"

    # Read the flow's current application association.
    FLOW_JSON=$(graph_call GET \
        "https://graph.microsoft.com/v1.0/identity/authenticationEventsFlows/$EXISTING_FLOW_ID"); RC=$?
    if [ $RC -ne 0 ]; then
        echo "  ERROR: Could not read the existing user flow."
        exit 1
    fi

    APP_INCLUDED=$(echo "$FLOW_JSON" | python3 -c "
import sys,json
app_id='$ENTRA_CLIENT_ID'
try:
    d=json.load(sys.stdin)
    apps=(d.get('conditions') or {}).get('applications') or {}
    inc=apps.get('includeApplications') or []
    print('yes' if any(a.get('appId')==app_id for a in inc) else 'no')
except Exception:
    print('no')" 2>/dev/null || echo "no")

    if [ "$APP_INCLUDED" = "yes" ]; then
        echo "  App $ENTRA_CLIENT_ID is already associated with the flow."
    else
        echo "  Associating app $ENTRA_CLIENT_ID with the existing flow..."
        # Merge the current app into includeApplications (don't drop existing).
        COND_BODY=$(echo "$FLOW_JSON" | python3 -c "
import sys,json
app_id='$ENTRA_CLIENT_ID'
try:
    d=json.load(sys.stdin)
except Exception:
    d={}
apps=((d.get('conditions') or {}).get('applications') or {})
inc=apps.get('includeApplications') or []
if not any(a.get('appId')==app_id for a in inc):
    inc.append({'appId': app_id})
print(json.dumps({'conditions': {'applications': {'includeApplications': inc}}}))")
        if ! graph_call PATCH \
            "https://graph.microsoft.com/v1.0/identity/authenticationEventsFlows/$EXISTING_FLOW_ID" \
            "$COND_BODY" >/dev/null; then
            echo "  ERROR: Failed to associate the app with the existing user flow."
            exit 1
        fi
        echo "  App association added to the existing flow."
    fi
else
    FLOW_BODY=$(python3 - "$ENTRA_USER_FLOW_NAME" "$ENTRA_CLIENT_ID" <<'PY'
import json, sys
name, app_id = sys.argv[1], sys.argv[2]
print(json.dumps({
    "@odata.type": "#microsoft.graph.externalUsersSelfServiceSignUpEventsFlow",
    "displayName": name,
    "conditions": {"applications": {"includeApplications": [{"appId": app_id}]}},
    "onInteractiveAuthFlowStart": {
        "@odata.type": "#microsoft.graph.onInteractiveAuthFlowStartExternalUsersSelfServiceSignUp",
        "isSignUpAllowed": True,
    },
    "onAuthenticationMethodLoadStart": {
        "@odata.type": "#microsoft.graph.onAuthenticationMethodLoadStartExternalUsersSelfServiceSignUp",
        "identityProviders": [{"id": "EmailPassword-OAUTH"}],
    },
    "onAttributeCollection": {
        "@odata.type": "#microsoft.graph.onAttributeCollectionExternalUsersSelfServiceSignUp",
        "attributes": [
            {"id": "email", "displayName": "Email Address", "description": "Email address of the user",
             "userFlowAttributeType": "builtIn", "dataType": "string"},
            {"id": "displayName", "displayName": "Display Name", "description": "Display Name of the User.",
             "userFlowAttributeType": "builtIn", "dataType": "string"},
        ],
        "attributeCollectionPage": {"views": [{"inputs": [
            {"attribute": "email", "label": "Email Address", "inputType": "text", "hidden": True,
             "editable": False, "writeToDirectory": True, "required": True,
             "validationRegEx": "^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9-]+(?:\\.[a-zA-Z0-9-]+)*$"},
            {"attribute": "displayName", "label": "Display Name", "inputType": "text", "hidden": False,
             "editable": True, "writeToDirectory": True, "required": False,
             "validationRegEx": "^[a-zA-Z_][0-9a-zA-Z_ ]*[0-9a-zA-Z_]+$"},
        ]}]},
    },
}))
PY
)
    CREATED_FLOW=$(graph_call POST \
        "https://graph.microsoft.com/v1.0/identity/authenticationEventsFlows" "$FLOW_BODY"); RC=$?
    ENTRA_USER_FLOW_ID=$(echo "$CREATED_FLOW" | json_get id)

    if [ $RC -ne 0 ] || [ -z "$ENTRA_USER_FLOW_ID" ]; then
        echo "  ERROR: User flow creation failed."
        echo "  Verify the signed-in user has the 'External ID User Flow Administrator' role."
        exit 1
    fi
    echo "  User flow created and associated with the app (id=$ENTRA_USER_FLOW_ID)."
fi

if [ -z "$ENTRA_CLIENT_ID" ] || [ -z "$ENTRA_TENANT_SUBDOMAIN" ]; then
    echo "  ERROR: Missing ENTRA_CLIENT_ID or ENTRA_TENANT_SUBDOMAIN after setup."
    exit 1
fi

# Delete the temporary Graph automation app now, while still in the external
# tenant context, so no standing credential remains. Clear the EXIT trap since
# cleanup is done here explicitly.
echo "  Removing temporary Graph automation app..."
cleanup_graph_app
trap - EXIT
GRAPH_TOKEN=""

# Switch the active CLI context back to the subscription for infra deployment.
# The external-tenant login in Step 3 left the active context on a tenant-level
# account; restore the real subscription so Step 4 targets the right place.
if ! az account set --subscription "$AZURE_SUBSCRIPTION_ID" --only-show-errors; then
    echo "  ERROR: Could not switch back to subscription $AZURE_SUBSCRIPTION_ID."
    echo "  Run 'az login' (home tenant) and re-run; the external-tenant login"
    echo "  changed the active context."
    exit 1
fi
echo ""

# ============================================================
# Step 4: Deploy infrastructure with Bicep
# ============================================================
echo "[4/7] Creating resource group and deploying Bicep..."

# SUB_ARG (defined in Step 1) pins the subscription: an external-tenant login
# in Step 3 may have changed the active CLI context.
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" "${SUB_ARG[@]}" --output none

# Request the app's own resource scope so native auth issues an access token
# whose audience (aud) is THIS app registration, not Microsoft Graph. With the
# default "openid offline_access" the token's aud is Graph
# (00000003-0000-0000-c000-000000000000), which the backend cannot validate
# against the tenant JWKS, so every authenticated API returns 401. Pointing the
# scope at "<clientId>/.default" makes aud == clientId, which the backend
# accepts (it verifies signature + issuer). clientId is only known at deploy
# time, so it is passed in here rather than hardcoded in the Bicep template.
ENTRA_SCOPES="${ENTRA_SCOPES:-openid offline_access ${ENTRA_CLIENT_ID}/.default}"

DEPLOYMENT_OUTPUT=$(az deployment group create \
    "${SUB_ARG[@]}" \
    --resource-group "$RESOURCE_GROUP" \
    --template-file "$BICEP_MAIN" \
    --parameters \
        appName="$APP_NAME" \
        entraTenantSubdomain="$ENTRA_TENANT_SUBDOMAIN" \
        entraTenantId="$ENTRA_TENANT_ID" \
        entraClientId="$ENTRA_CLIENT_ID" \
        entraScopes="$ENTRA_SCOPES" \
    --query properties.outputs \
    --output json)

get_output() {
    echo "$DEPLOYMENT_OUTPUT" | python3 -c "import sys,json;print(json.load(sys.stdin).get('$1',{}).get('value',''))" 2>/dev/null || echo ""
}

FUNCTION_APP_NAME=$(get_output functionAppName)
API_ENDPOINT=$(get_output apiEndpoint)
STORAGE_ACCOUNT=$(get_output storageAccountName)

if [ -z "$FUNCTION_APP_NAME" ]; then
    echo "ERROR: Bicep deployment failed. Check the Azure Portal for details."
    exit 1
fi

echo "  Function App: $FUNCTION_APP_NAME"
echo "  API Endpoint: $API_ENDPOINT"
echo "  Storage:      $STORAGE_ACCOUNT"
echo ""

# ============================================================
# Step 5: Deploy function code (Flex Consumption OneDeploy)
# ============================================================
echo "[5/7] Deploying function code (remote build)..."

DEPLOY_ZIP="$(mktemp -d)/app.zip"
(
    cd "$FUNCTION_APP_DIR"
    zip -r "$DEPLOY_ZIP" . -x "__pycache__/*" "*.pyc" ".venv/*" ".git/*" "local.settings.json" >/dev/null
)

# For Flex Consumption, the package is uploaded to the deployment storage
# container configured in the Bicep functionAppConfig, and Azure performs a
# remote (Oryx) build. This is the documented Flex deploy command; --build-remote
# requests the server-side build (required for Python).
if ! az functionapp deployment source config-zip \
    "${SUB_ARG[@]}" \
    --resource-group "$RESOURCE_GROUP" \
    --name "$FUNCTION_APP_NAME" \
    --src "$DEPLOY_ZIP" \
    --build-remote true \
    --output none; then
    echo "  ERROR: Function code deployment failed."
    rm -f "$DEPLOY_ZIP"
    exit 1
fi

rm -f "$DEPLOY_ZIP"
echo "  Code deployed."
echo ""

# ============================================================
# Step 6: Event Grid subscription for the blob (thumbnail) trigger
# ============================================================
echo "[6/7] Wiring Event Grid blob trigger..."

BLOB_KEY=""
for _ in $(seq 1 12); do
    BLOB_KEY=$(az functionapp keys list "${SUB_ARG[@]}" --name "$FUNCTION_APP_NAME" --resource-group "$RESOURCE_GROUP" \
        --query "systemKeys.eventgrid_extension" -o tsv 2>/dev/null || echo "")
    if [ -n "$BLOB_KEY" ] && [ "$BLOB_KEY" != "null" ]; then break; fi
    BLOB_KEY=""
    sleep 10
done

if [ -z "$BLOB_KEY" ]; then
    echo "  WARNING: eventgrid_extension key unavailable; create the Event Grid"
    echo "  subscription manually (see README). Thumbnails will not generate yet."
else
    ENDPOINT_URL="https://${FUNCTION_APP_NAME}.azurewebsites.net/runtime/webhooks/eventgrid?functionName=process_video&code=${BLOB_KEY}"
    STORAGE_ID=$(az storage account show "${SUB_ARG[@]}" --name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" --query id -o tsv)

    az eventgrid event-subscription delete \
        --name video-upload-trigger --source-resource-id "$STORAGE_ID" \
        --output none 2>/dev/null || true

    az eventgrid event-subscription create \
        "${SUB_ARG[@]}" \
        --name video-upload-trigger \
        --source-resource-id "$STORAGE_ID" \
        --endpoint "$ENDPOINT_URL" \
        --endpoint-type webhook \
        --included-event-types Microsoft.Storage.BlobCreated \
        --subject-begins-with "/blobServices/default/containers/videos/blobs/users/" \
        --output none 2>/dev/null && echo "  Event Grid subscription created." \
        || echo "  WARNING: Event Grid subscription failed; create it manually."
fi
echo ""

# ============================================================
# Step 7: Smoke test
# ============================================================
echo "[7/7] Verifying the API..."
sleep 10
HTTP_CODE="000"
for i in $(seq 1 6); do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$API_ENDPOINT/info" 2>/dev/null || echo "000")
    [ "$HTTP_CODE" = "200" ] && break
    echo "  Waiting for cold start (attempt $i/6)..."
    sleep 10
done
[ "$HTTP_CODE" = "200" ] && echo "  API is live." || echo "  WARNING: API not responding yet (HTTP $HTTP_CODE)."
echo ""

echo "=============================================="
echo " DEPLOYMENT SUMMARY"
echo "=============================================="
echo ""
echo " API Endpoint:   $API_ENDPOINT"
echo " Function App:   $FUNCTION_APP_NAME"
echo " Resource Group: $RESOURCE_GROUP"
echo " External tenant: $ENTRA_TENANT_SUBDOMAIN ($ENTRA_TENANT_ID)"
echo " App (client) ID: $ENTRA_CLIENT_ID"
echo ""
echo " Connect the app:"
echo "   1. Open app -> Drawer -> Settings"
echo "   2. Enter: $API_ENDPOINT"
echo "   3. Save and run Connection Test"
echo ""
echo " Logs:   az functionapp log tail --name $FUNCTION_APP_NAME --resource-group $RESOURCE_GROUP"
echo " Delete: az group delete --name $RESOURCE_GROUP --yes --no-wait"
echo ""
echo "=============================================="
