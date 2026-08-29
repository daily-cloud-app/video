// Least-privilege role assignments for the Function App's managed identity.
// The identity needs blob data access for the Flex Consumption deployment
// container and for the Functions host storage (AzureWebJobsStorage via MI).
@description('Name of the existing storage account to scope role assignments to.')
param storageAccountName string

@description('Principal (object) id of the user-assigned managed identity.')
param principalId string

// Built-in role definition ids.
// Storage Blob Data Owner — required by the Functions host and deployment
// when AzureWebJobsStorage/deployment use managed identity.
var storageBlobDataOwnerRoleId = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
// Storage Blob Data Contributor — read/write blob data.
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
// Storage Queue Data Contributor — used by the host for internal queues.
var storageQueueDataContributorRoleId = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource blobOwnerAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, principalId, storageBlobDataOwnerRoleId)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataOwnerRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource blobContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, principalId, storageBlobDataContributorRoleId)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource queueContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, principalId, storageQueueDataContributorRoleId)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageQueueDataContributorRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
