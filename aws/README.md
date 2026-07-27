# Daily Cloud Photo — AWS Backend

- [English](#english)
- [日本語](#日本語)

---

## English

> Requires an AWS account ([create free](https://aws.amazon.com/free/))

### Quick Start

[![Open in CloudShell](https://img.shields.io/badge/AWS-CloudShell-orange?logo=amazonaws)](https://console.aws.amazon.com/cloudshell/home)

1. Click the **CloudShell** button above
2. Run the following commands:
   ```bash
   curl -sO https://raw.githubusercontent.com/daily-cloud-app/photo/main/aws/template.yaml
   aws cloudformation deploy --stack-name daily-cloud-photo \
     --template-file template.yaml \
     --capabilities CAPABILITY_NAMED_IAM
   ```
3. After completion, get the endpoint URL:
   ```bash
   aws cloudformation describe-stacks --stack-name daily-cloud-photo \
     --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text
   ```
4. Copy the API endpoint URL from the output into the app

### Parameters

[![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home#/stacks/create) 

You can also deploy via the Console GUI — upload `template.yaml` and fill in parameters through the form.

| Parameter | Default | Description |
|-----------|---------|-------------|
| AppName | `daily-cloud-photo` | Prefix for all resource names |
| RequireEmail | `true` | Require email for signup |
| RequirePhone | `false` | Require phone number for signup |
| PhotosBucketName | (auto-generated) | S3 bucket name for photos |
| EnableShareUrl | `true` | Enable upload URL sharing feature |
| EnableLabelSharing | `true` | Enable label sharing between users |
| AppDisplayName | `Daily Cloud Photo Backend` | Display name shown in the app |

### Connecting the App

1. **Settings** → Enter the endpoint URL → **Save**
2. Run **Connection Test**
3. **Login** → Create account

### Deleting Resources

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name daily-cloud-photo \
  --query "Stacks[0].Outputs[?OutputKey=='PhotosBucketName'].OutputValue" --output text)
python3 -c "
import boto3
s3 = boto3.resource('s3').Bucket('$BUCKET')
s3.object_versions.delete()
"
aws cloudformation delete-stack --stack-name daily-cloud-photo
```

### Architecture

```
User → API Gateway (HTTP API) → Lambda (unified handler)
                                    ├── Cognito (auth)
                                    ├── S3 (photo storage + thumbnails)
                                    ├── DynamoDB (metadata)
                                    └── S3 Trigger Lambda (EXIF + thumbnail generation)
```

- Single Lambda handles all API routes (path-based routing)
- User photos isolated under `users/{cognito_sub}/` prefix
- Direct upload to S3 via presigned URLs (no Lambda proxy)
- S3 trigger automatically extracts EXIF date + generates thumbnails
- Bootstrap Lambda fetches source code + builds Pillow layer at deploy time

### Cost Estimate

All services are pay-per-use. Low usage typically falls within AWS Free Tier.

These are estimates only. Actual costs depend on usage patterns and may vary. Always monitor your cloud provider's billing dashboard.

| Service | Free Tier |
|---------|-----------|
| Lambda | 1M requests/month |
| API Gateway | 1M requests/month |
| DynamoDB | 25GB + 25 WCU/RCU |
| S3 | 5GB (12 months) |
| Cognito | 50,000 MAU |

### Security Recommendations for Production

These are examples only — not an exhaustive list. Evaluate your own requirements and apply additional measures as needed.

- **API Gateway throttling**: Add rate limits to prevent brute-force attacks ([docs](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-throttling.html))
- **WAF**: Place WAF in front of API Gateway to block malicious requests ([docs](https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-control-access-aws-waf.html))
- **CORS restriction**: Limit `AllowOrigins` to specific domains ([docs](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-cors.html))
- **Access logging**: Enable API Gateway access logs via CloudWatch ([docs](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-logging.html))
- **IAM least privilege**: Remove unnecessary actions
- **Share URL limits**: Consider adding file size limits, upload count limits, and Content-Type validation

---

## 日本語

> AWS アカウントが必要です（[無料で作成](https://aws.amazon.com/free/)）

### クイックスタート

[![Open in CloudShell](https://img.shields.io/badge/AWS-CloudShell-orange?logo=amazonaws)](https://console.aws.amazon.com/cloudshell/home)

1. 上記の **CloudShell** ボタンをクリック
2. 以下を実行:
   ```bash
   curl -sO https://raw.githubusercontent.com/daily-cloud-app/photo/main/aws/template.yaml
   aws cloudformation deploy --stack-name daily-cloud-photo \
     --template-file template.yaml \
     --capabilities CAPABILITY_NAMED_IAM
   ```
3. 完了後、エンドポイントを確認:
   ```bash
   aws cloudformation describe-stacks --stack-name daily-cloud-photo \
     --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text
   ```
4. 出力された API エンドポイント URL をアプリに入力

### パラメータ一覧

[![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home#/stacks/create) 

GUI でデプロイする場合は、コンソールから `template.yaml` をアップロードし、フォームでパラメータを入力できます。

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| AppName | `daily-cloud-photo` | リソース名のプレフィックス |
| RequireEmail | `true` | サインアップ時にメール必須 |
| RequirePhone | `false` | サインアップ時に電話番号必須 |
| PhotosBucketName | (自動生成) | 写真用 S3 バケット名 |
| EnableShareUrl | `true` | アップロード URL　共有機能 |
| EnableLabelSharing | `true` | ラベル共有機能 |
| AppDisplayName | `Daily Cloud Photo Backend` | アプリでの表示名 |

### アプリでの接続

1. **設定** → エンドポイント URL を入力 → **保存**
2. **接続テスト** で確認
3. **ログイン** からアカウント作成

### リソースの削除

[![Open in CloudShell](https://img.shields.io/badge/AWS-CloudShell-orange?logo=amazonaws)](https://console.aws.amazon.com/cloudshell/home)

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name daily-cloud-photo \
  --query "Stacks[0].Outputs[?OutputKey=='PhotosBucketName'].OutputValue" --output text)
python3 -c "
import boto3
s3 = boto3.resource('s3').Bucket('$BUCKET')
s3.object_versions.delete()
"
aws cloudformation delete-stack --stack-name daily-cloud-photo
```

### アーキテクチャ

```
ユーザー → API Gateway (HTTP API) → Lambda (統合ハンドラー)
                                        ├── Cognito (認証)
                                        ├── S3 (写真保存 + サムネイル)
                                        ├── DynamoDB (メタデータ)
                                        └── S3 Trigger Lambda (EXIF抽出 + サムネイル生成)
```

- 全 API を1つの Lambda で処理（パスベースルーティング）
- ユーザーの写真は `users/{cognito_sub}/` プレフィックスで分離
- presigned URL で S3 に直接アップロード（Lambda を経由しない）
- S3 トリガーで自動的に EXIF 解析 + サムネイル生成 + DynamoDB 登録
- デプロイ時に Bootstrap Lambda がソースコード取得 ＋ Pillow Layer 自動ビルド

### コスト目安

すべて従量課金。少人数であれば AWS 無料枠内に収まります。

以下はあくまで目安です。実際の費用は利用状況により異なります。各クラウドプロバイダーの請求ダッシュボードを定期的に確認してください。

| サービス | 無料枠 |
|----------|--------|
| Lambda | 月100万リクエスト |
| API Gateway | 月100万リクエスト |
| DynamoDB | 25GB + 25WCU/25RCU |
| S3 | 5GB（12ヶ月間） |
| Cognito | 月50,000 MAU |

### 本番運用時のセキュリティ推奨事項

以下は一例であり、これだけで十分というわけではありません。要件に応じて追加の対策を検討してください。

- **API Gatewayスロットリング**: ステージ設定でレート制限を追加 ([docs](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-throttling.html))
- **WAF**: API Gateway の前に WAF を配置 ([docs](https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-control-access-aws-waf.html))
- **CORS の制限**: `AllowOrigins` を特定ドメインに限定 ([docs](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-cors.html))
- **アクセスログ**: API Gateway のアクセスログを CloudWatch で有効化 ([docs](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-logging.html))
- **IAM ロール最小権限化**: 不要なアクションの除去
- **共有 URL の制限**: ファイルサイズ制限、アップロード回数制限、Content-Type 検証の追加を検討
