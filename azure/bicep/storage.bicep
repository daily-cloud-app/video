// Storage account for video blobs, the Flex Consumption deployment package,
// and the Functions host storage (AzureWebJobsStorage via managed identity).
@description('Azure region for all resources.')
param location string

@description('Storage account name (3-24 chars, lowercase alphanumerics).')
param storageAccountName string

@description('Blob container that holds user videos.')
param videosContainerName string = 'videos'

@description('Blob container that holds the Flex Consumption deployment package.')
param deploymentContainerName string = 'deploymentpackage'

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    // Shared keys remain enabled because the app logic generates SAS URLs with
    // the account key. Platform access (deployment/host) uses managed identity.
    allowSharedKeyAccess: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    cors: {
      corsRules: [
        {
          allowedOrigins: [ '*' ]
          allowedMethods: [ 'GET', 'PUT', 'POST', 'HEAD', 'OPTIONS' ]
          allowedHeaders: [ '*' ]
          exposedHeaders: [ '*' ]
          maxAgeInSeconds: 3600
        }
      ]
    }
  }
}

resource videosContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: videosContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: deploymentContainerName
  properties: {
    publicAccess: 'None'
  }
}

output id string = storageAccount.id
output name string = storageAccount.name
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob
output deploymentContainerName string = deploymentContainerName
output videosContainerName string = videosContainerName

// Connection string for the app logic (SAS generation needs the account key).
// Built inside the module so listKeys() resolves against a concrete resource.
@secure()
output connectionString string = 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};EndpointSuffix=${environment().suffixes.storage};AccountKey=${storageAccount.listKeys().keys[0].value}'
