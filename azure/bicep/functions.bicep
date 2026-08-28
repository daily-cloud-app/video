// Application Insights + Log Analytics, the Flex Consumption plan, and the
// Function App itself (Python) with managed-identity-based platform storage.
@description('Azure region for all resources.')
param location string

@description('Function App name.')
param functionAppName string

@description('Flex Consumption plan name.')
param planName string

@description('Application Insights component name.')
param appInsightsName string

@description('Log Analytics workspace name.')
param logAnalyticsName string

@description('Resource id of the user-assigned managed identity.')
param managedIdentityId string

@description('Client id of the user-assigned managed identity.')
param managedIdentityClientId string

@description('Storage account name (platform + deployment storage).')
param storageAccountName string

@description('Blob endpoint of the storage account.')
param storageBlobEndpoint string

@description('Deployment package blob container name.')
param deploymentContainerName string

@description('Python runtime version for Flex Consumption.')
param pythonVersion string = '3.11'

@description('Maximum number of Flex Consumption instances.')
param maximumInstanceCount int = 100

@description('Per-instance memory in MB (512, 2048, or 4096).')
param instanceMemoryMB int = 2048

@description('Application settings that carry the app configuration (Entra, Cosmos, Storage, feature flags). Marked secure because it includes connection strings.')
@secure()
param appConfigSettings object

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    Request_Source: 'rest'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: planName
  location: location
  kind: 'functionapp'
  sku: {
    tier: 'FlexConsumption'
    name: 'FC1'
  }
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      minTlsVersion: '1.2'
      cors: {
        allowedOrigins: [ '*' ]
        supportCredentials: false
      }
    }
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storageBlobEndpoint}${deploymentContainerName}'
          authentication: {
            type: 'UserAssignedIdentity'
            userAssignedIdentityResourceId: managedIdentityId
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: maximumInstanceCount
        instanceMemoryMB: instanceMemoryMB
      }
      runtime: {
        name: 'python'
        version: pythonVersion
      }
    }
  }
}

// Platform + Application Insights settings use managed identity (no secrets).
// The app-specific configuration (Entra, Cosmos, Storage, flags) is merged in.
var platformSettings = {
  AzureWebJobsStorage__accountName: storageAccountName
  AzureWebJobsStorage__credential: 'managedidentity'
  AzureWebJobsStorage__clientId: managedIdentityClientId
  APPLICATIONINSIGHTS_CONNECTION_STRING: appInsights.properties.ConnectionString
  APPLICATIONINSIGHTS_AUTHENTICATION_STRING: 'ClientId=${managedIdentityClientId};Authorization=AAD'
}

resource appSettings 'Microsoft.Web/sites/config@2024-04-01' = {
  parent: functionApp
  name: 'appsettings'
  properties: union(platformSettings, appConfigSettings)
}

output functionAppName string = functionApp.name
output defaultHostName string = functionApp.properties.defaultHostName
