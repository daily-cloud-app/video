# Daily Cloud Photo — GCP Backend

- [English](#english)
- [日本語](#日本語)

---

## English

> Requires a Google Cloud account ([create free](https://cloud.google.com/free))

### Quick Start

[![Open in Cloud Shell](https://gstatic.com/cloudssh/images/open-btn.svg)](https://shell.cloud.google.com/cloudshell/editor?cloudshell_git_repo=https://github.com/daily-cloud-app/photo&cloudshell_working_dir=gcp&cloudshell_tutorial=README.md&cloudshell_open_in_editor=functions/main.py)

1. Click the **Cloud Shell** button above
2. Create a project and link billing:
   ```bash
   gcloud projects create daily-cloud-app --name="Daily Cloud App"
   gcloud config set project daily-cloud-app
   BILLING_ID=$(gcloud billing accounts list --format="value(ACCOUNT_ID)" --limit=1)
   gcloud billing projects link daily-cloud-app --billing-account=$BILLING_ID
   ```
3. Run the deploy script:
   ```bash
   chmod +x deploy.sh && ./deploy.sh
   ```
4. Copy the API endpoint URL from the output into the app

### Parameters

You can customize the deployment by setting environment variables before running deploy.sh:

   ```bash
   APP_DISPLAY_NAME="My Album" ./deploy.sh
   ```

| Parameter | Default | Description |
|-----------|---------|-------------|
| PROJECT_ID | (current project) | GCP project ID |
| REGION | `asia-northeast1` | Deployment region |
| BUCKET_NAME | `{project}-photos` | Cloud Storage bucket for photos |
| REQUIRE_EMAIL | `true` | Require email for signup |
| REQUIRE_PHONE | `false` | Require phone number for signup |
| ENABLE_SHARE_URL | `true` | Enable upload URL sharing feature |
| ENABLE_LABEL_SHARING | `true` | Enable label sharing between users |
| APP_DISPLAY_NAME | `Daily Cloud Photo Backend` | Display name shown in the app |

### Connecting the App

1. **Settings** → Enter the endpoint URL → **Save**
2. Run **Connection Test**
3. **Login** → Create account

### Deleting Resources

```bash
gcloud config set project daily-cloud-app
gcloud functions delete daily-cloud-photo-api --region=asia-northeast1 --gen2 -q
gcloud functions delete daily-cloud-photo-storage-trigger --region=asia-northeast1 --gen2 -q
gsutil -m rm -r gs://daily-cloud-app-photos
gsutil rb gs://daily-cloud-app-photos
gcloud firestore databases delete --database="(default)"
```

### Architecture

```
User → Cloud Functions (HTTP) → Main Handler (Flask routing)
                                    ├── Firebase Auth (auth)
                                    ├── Cloud Storage (photo storage + thumbnails)
                                    ├── Firestore (metadata)
                                    └── Storage Trigger Function (EXIF + thumbnail generation)
```

- Single Cloud Function handles all API routes (Flask-based routing)
- User photos isolated under `users/{firebase_uid}/` prefix
- Direct upload to Cloud Storage via signed URLs (no function proxy)
- Storage trigger automatically extracts EXIF date + generates thumbnails

### Cost Estimate

All services are pay-per-use. Low usage typically falls within GCP Free Tier.

These are estimates only. Actual costs depend on usage patterns and may vary. Always monitor your cloud provider's billing dashboard.

| Service | Free Tier |
|---------|-----------|
| Cloud Functions | 2M invocations/month |
| Firestore | 1 GiB storage, 50K reads/day, 20K writes/day |
| Cloud Storage | 5 GB (Standard), 5K Class A ops/month |
| Firebase Auth | 50,000 MAU |
| Networking | 1 GB egress/month |

### Security Recommendations for Production

These are examples only — not an exhaustive list. Evaluate your own requirements and apply additional measures as needed.

- **IAM**: Use service accounts with least-privilege roles ([docs](https://cloud.google.com/iam/docs/understanding-roles))
- **VPC Connector**: Place functions behind a VPC for internal-only access ([docs](https://cloud.google.com/functions/docs/networking/connecting-vpc))
- **Cloud Armor**: Add WAF rules in front of Cloud Load Balancer ([docs](https://cloud.google.com/armor/docs/configure-waf))
- **CORS restriction**: Limit allowed origins to specific domains
- **Audit Logging**: Enable Cloud Audit Logs for all API calls ([docs](https://cloud.google.com/logging/docs/audit))
- **Rate Limiting**: Configure Cloud Functions concurrency limits ([docs](https://cloud.google.com/functions/docs/configuring/max-instances))
- **Share URL limits**: Consider adding file size limits, upload count limits, and Content-Type validation

---

## 日本語

> Google Cloud アカウントが必要です（[無料で作成](https://cloud.google.com/free)）

### クイックスタート

[![Open in Cloud Shell](https://gstatic.com/cloudssh/images/open-btn.svg)](https://shell.cloud.google.com/cloudshell/editor?cloudshell_git_repo=https://github.com/daily-cloud-app/photo&cloudshell_working_dir=gcp&cloudshell_tutorial=README.md&cloudshell_open_in_editor=functions/main.py)

1. 上記の **Cloud Shell** ボタンをクリック
2. プロジェクトを作成し、課金を有効化:
   ```bash
   gcloud projects create daily-cloud-app --name="Daily Cloud App"
   gcloud config set project daily-cloud-app
   BILLING_ID=$(gcloud billing accounts list --format="value(ACCOUNT_ID)" --limit=1)
   gcloud billing projects link daily-cloud-app --billing-account=$BILLING_ID
   ```
3. デプロイスクリプトを実行:
   ```bash
   chmod +x deploy.sh && ./deploy.sh
   ```
4. 出力された API エンドポイント URL をアプリに入力

### パラメータ一覧

[deploy.sh](deploy.sh) 実行時にパラメータを指定することでカスタマイズが可能です。

   ```bash
   APP_DISPLAY_NAME="My Album" ./deploy.sh
   ```

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| PROJECT_ID | (現在のプロジェクト) | GCP プロジェクト ID |
| REGION | `asia-northeast1` | デプロイリージョン |
| BUCKET_NAME | `{project}-photos` | 写真用 Cloud Storage バケット |
| REQUIRE_EMAIL | `true` | サインアップ時にメール必須 |
| REQUIRE_PHONE | `false` | サインアップ時に電話番号必須 |
| ENABLE_SHARE_URL | `true` | アップロード URL 共有機能 |
| ENABLE_LABEL_SHARING | `true` | ラベル共有機能 |
| APP_DISPLAY_NAME | `Daily Cloud Photo Backend` | アプリでの表示名 |

### アプリでの接続

1. **設定** → エンドポイント URL を入力 → **保存**
2. **接続テスト** で確認
3. **ログイン** からアカウント作成

### リソースの削除

[![Open in Cloud Shell](https://gstatic.com/cloudssh/images/open-btn.svg)](https://shell.cloud.google.com/cloudshell/editor?cloudshell_git_repo=https://github.com/daily-cloud-app/photo&cloudshell_working_dir=gcp&cloudshell_tutorial=README.md&cloudshell_open_in_editor=functions/main.py)

```bash
gcloud config set project daily-cloud-app
gcloud functions delete daily-cloud-photo-api --region=asia-northeast1 --gen2 -q
gcloud functions delete daily-cloud-photo-storage-trigger --region=asia-northeast1 --gen2 -q
gsutil -m rm -r gs://daily-cloud-app-photos
gsutil rb gs://daily-cloud-app-photos
gcloud firestore databases delete --database="(default)"
```

### アーキテクチャ

```
ユーザー → Cloud Functions (HTTP) → メインハンドラー (Flask ルーティング)
                                        ├── Firebase Auth (認証)
                                        ├── Cloud Storage (写真保存 + サムネイル)
                                        ├── Firestore (メタデータ)
                                        └── Storage Trigger 関数 (EXIF + サムネイル生成)
```

- 全 API を1つの Cloud Function で処理（Flask ベースのルーティング）
- ユーザーの写真は `users/{firebase_uid}/` プレフィックスで分離
- 署名付き URL で Cloud Storage に直接アップロード（関数を経由しない）
- ストレージトリガーで自動的に EXIF 解析 + サムネイル生成

### コスト目安

すべて従量課金。少人数であれば GCP 無料枠内に収まります。

以下はあくまで目安です。実際の費用は利用状況により異なります。各クラウドプロバイダーの請求ダッシュボードを定期的に確認してください。

| サービス | 無料枠 |
|----------|--------|
| Cloud Functions | 月200万呼び出し |
| Firestore | 1 GiB ストレージ、5万読み取り/日、2万書き込み/日 |
| Cloud Storage | 5 GB（Standard）、月5,000 Class A オペレーション |
| Firebase Auth | 50,000 MAU |
| ネットワーク | 月1 GB エグレス |

### 本番運用時のセキュリティ推奨事項

以下は一例であり、これだけで十分というわけではありません。要件に応じて追加の対策を検討してください。

- **IAM**: 最小権限のサービスアカウントを使用 ([docs](https://cloud.google.com/iam/docs/understanding-roles))
- **VPC コネクタ**: 関数を VPC 内に配置し内部アクセスのみに制限 ([docs](https://cloud.google.com/functions/docs/networking/connecting-vpc))
- **Cloud Armor**: Cloud Load Balancer の前に WAF ルールを追加 ([docs](https://cloud.google.com/armor/docs/configure-waf))
- **CORS の制限**: 許可するオリジンを特定ドメインに限定
- **監査ログ**: 全 API 呼び出しに Cloud Audit Logs を有効化 ([docs](https://cloud.google.com/logging/docs/audit))
- **レート制限**: Cloud Functions の同時実行数制限を設定 ([docs](https://cloud.google.com/functions/docs/configuring/max-instances))
- **共有 URL の制限**: ファイルサイズ制限、アップロード回数制限、Content-Type 検証の追加を検討
