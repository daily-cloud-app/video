# Daily Cloud Video — Infrastructure Samples

- [English](#english)
- [日本語](#日本語)

---

## English

Sample backend implementations for the Daily Cloud Video app.
These are reference implementations to help you get started — feel free to modify or use them as a base for your own setup.

All implementations have been verified with the app for basic operations (signup, upload, cloud sync, label sharing, storage trigger).

### Providers

These implementations are provided as samples.
You are not limited to these providers — any server that implements the [API specification](API.md) will work with the app.

- [**AWS**](aws/README.md)
- [**GCP**](gcp/README.md)
- [**Azure**](azure/README.md)

### Service Comparison

| Component | AWS | GCP | Azure |
|-----------|-----|-----|-------|
| API | API Gateway | Cloud Run | Functions HTTP Trigger |
| Logic | Lambda | Cloud Functions | Azure Functions |
| Database | DynamoDB | Firestore | Cosmos DB |
| File Storage | S3 | Cloud Storage | Blob Storage |
| Auth | Cognito | Firebase Auth | Custom JWT |
| IaC | CloudFormation | gcloud CLI | ARM Template |
| Thumbnail Trigger | S3 Event | Eventarc | Blob Trigger |

---

## 日本語

Daily Cloud Video アプリ用のバックエンドサンプル実装です。
これらはリファレンス実装であり、自由に改変したりベースとして利用できます。

すべての実装はアプリの基本操作（サインアップ、アップロード、クラウド同期、ラベル共有、ストレージトリガー）で動作確認済みです。

### プロバイダー

以下の実装はサンプルとして提供されています。
これらのプロバイダーに限定されるわけではなく、[API 仕様](API.md)を実装していれば任意のサーバーでアプリと連携できます。

- [**AWS**](aws/README.md)
- [**GCP**](gcp/README.md)
- [**Azure**](azure/README.md)

### サービス比較

| コンポーネント | AWS | GCP | Azure |
|--------------|-----|-----|-------|
| API | API Gateway | Cloud Run | Functions HTTP Trigger |
| ロジック | Lambda | Cloud Functions | Azure Functions |
| データベース | DynamoDB | Firestore | Cosmos DB |
| ファイルストレージ | S3 | Cloud Storage | Blob Storage |
| 認証 | Cognito | Firebase Auth | Custom JWT |
| IaC | CloudFormation | gcloud CLI | ARM Template |
| サムネイルトリガー | S3 Event | Eventarc | Blob Trigger |
