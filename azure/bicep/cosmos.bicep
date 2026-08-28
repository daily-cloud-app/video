// Cosmos DB (serverless) with the SQL database and the two containers used by
// the backend: "users" (username -> email / Entra id mapping) and "videos".
@description('Azure region for all resources.')
param location string

@description('Cosmos DB account name.')
param cosmosAccountName string

@description('SQL database name.')
param databaseName string = 'dailycloudvideo'

@description('Users mapping container name.')
param usersContainerName string = 'users'

@description('Videos container name.')
param videosContainerName string = 'videos'

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: cosmosAccountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
      }
    ]
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmosAccount
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

// The users container is partitioned by /username so the backend can read a
// mapping document directly by username (id == username).
resource usersContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: usersContainerName
  properties: {
    resource: {
      id: usersContainerName
      partitionKey: {
        paths: [ '/username' ]
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [
          { path: '/username/?' }
          { path: '/email/?' }
          { path: '/entraObjectId/?' }
        ]
        excludedPaths: [
          { path: '/*' }
        ]
      }
    }
  }
}

// Videos are partitioned by /userId, which holds the stable Entra object id.
resource videosContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: videosContainerName
  properties: {
    resource: {
      id: videosContainerName
      partitionKey: {
        paths: [ '/userId' ]
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [
          { path: '/userId/?' }
          { path: '/status/?' }
          { path: '/createdAt/?' }
          { path: '/labels/?' }
        ]
        excludedPaths: [
          { path: '/*' }
        ]
      }
    }
  }
}

output id string = cosmosAccount.id
output name string = cosmosAccount.name
output databaseName string = databaseName
output documentEndpoint string = cosmosAccount.properties.documentEndpoint

// Connection string for the Cosmos SDK used by the app logic. Built inside the
// module so listConnectionStrings() resolves against a concrete resource.
@secure()
output connectionString string = cosmosAccount.listConnectionStrings().connectionStrings[0].connectionString
