using './main.bicep'

// Base name for all resources.
param appName = 'dailycloudvideo'

// ── Microsoft Entra External ID (native authentication) ──
// Fill these in from an existing external tenant + app registration, or let
// deploy.sh populate them after it provisions/creates them via Graph API.
param entraTenantSubdomain = ''
param entraTenantId = ''
param entraClientId = ''
param entraScopes = 'openid offline_access'

// ── Feature flags ──
param requireEmail = 'true'
param requirePhone = 'false'
param enableShareUrl = 'true'
param enableShareDownloadUrl = 'true'
param enableLabelSharing = 'true'
param shareUploadUrlExpiryHours = 24
param shareDownloadUrlExpiryHours = 72

// ── Flex Consumption sizing ──
param maximumInstanceCount = 100
param instanceMemoryMB = 2048
param pythonVersion = '3.11'
