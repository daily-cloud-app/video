# Daily Cloud Photo — Infrastructure Samples

Sample backend implementations for the Daily Cloud Photo app. 
These are reference implementations to help you get started — feel free to modify or use them as a base for your own setup.

All implementations have been verified with the app for basic operations (signup, upload, cloud sync, label sharing, storage trigger).

## Providers

These implementations are provided as samples.
You are not limited to these providers — any server that implements the [API specification](API.md) will work with the app.

- [**AWS**](aws/README.md)
- [**GCP**](gcp/README.md)
- [**Azure**](azure/README.md)



## Service Comparison

| Component | AWS | GCP | Azure |
|-----------|-----|-----|-------|
| API | API Gateway | Cloud Run | Functions HTTP Trigger |
| Logic | Lambda | Cloud Functions | Azure Functions |
| Database | DynamoDB | Firestore | Cosmos DB |
| File Storage | S3 | Cloud Storage | Blob Storage |
| Auth | Cognito | Firebase Auth | Custom JWT |
| IaC | CloudFormation | gcloud CLI | ARM Template |
| Thumbnail Trigger | S3 Event | Eventarc | Blob Trigger |
