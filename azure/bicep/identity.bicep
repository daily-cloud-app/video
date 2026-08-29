// User-assigned managed identity used by the Function App to authenticate to
// the platform storage account (deployment package + AzureWebJobsStorage) and
// to Application Insights, without any secrets.
@description('Azure region for all resources.')
param location string

@description('Name of the user-assigned managed identity.')
param identityName string

resource userAssignedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

output id string = userAssignedIdentity.id
output principalId string = userAssignedIdentity.properties.principalId
output clientId string = userAssignedIdentity.properties.clientId
output name string = userAssignedIdentity.name
