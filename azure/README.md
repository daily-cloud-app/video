# Daily Cloud Video — Azure Backend

- [English](#english)
- [日本語](#日本語)

---

## English

> Requires an Azure account ([create free](https://azure.microsoft.com/free/))

### Quick Start

[![Open in Cloud Shell](https://img.shields.io/badge/Azure-Cloud_Shell-blue?logo=microsoftazure)](https://shell.azure.com)

1. Click the **Cloud Shell** button above
2. Clone the repo and run the deploy script:
   ```bash
   git clone https://github.com/daily-cloud-app/video.git
   cd video/azure
   chmod +x deploy.sh && ./deploy.sh
   ```
3. When prompted, complete the one-time sign-in to the new sign-in directory
4. Copy the API endpoint URL from the output into the app

> [!NOTE]
> Sign-up, email verification, sign-in and password reset are handled by
> Microsoft Entra External ID. By default the script creates a new sign-in
> directory for you. To reuse an existing one, run `ENTRA_TENANT_ID=<guid> ./deploy.sh`.
> You need to be an administrator of that directory.

### Parameters

You can customize the deployment by setting environment variables before running deploy.sh:

   ```bash
   APP_NAME="myalbum" LOCATION="japaneast" ./deploy.sh
   ```

| Parameter | Default | Description |
|-----------|---------|-------------|
| CREATE_TENANT | `true` | Create a new sign-in directory (External ID tenant). Set `false` to skip |
| ENTRA_TENANT_ID | (created) | Reuse an existing sign-in directory instead (skips creation) |
| RESOURCE_GROUP | `daily-cloud-video-rg` | Target resource group |
| LOCATION | `eastus` | Deployment region |
| APP_NAME | `dailycloudvideo` | Base name for all resources |
| REQUIRE_EMAIL | `true` | Require email for signup |
| ENABLE_SHARE_URL | `true` | Enable upload URL sharing feature |
| ENABLE_SHARE_DOWNLOAD_URL | `true` | Enable download URL sharing feature |
| ENABLE_LABEL_SHARING | `true` | Enable label sharing between users |
| SHARE_UPLOAD_URL_EXPIRY_HOURS | `24` | Validity (hours) of issued upload URLs |
| SHARE_DOWNLOAD_URL_EXPIRY_HOURS | `72` | Validity (hours) of issued download URLs |

### Connecting the App

1. **Settings** → Enter the endpoint URL → **Save**
2. Run **Connection Test**
3. **Login** → Create account

### Deleting Resources

[![Open in Cloud Shell](https://img.shields.io/badge/Azure-Cloud_Shell-blue?logo=microsoftazure)](https://shell.azure.com)

```bash
az group delete --name daily-cloud-video-rg --yes --no-wait
```

If you created a sign-in directory with `CREATE_TENANT=true`, delete it
separately from the [Microsoft Entra admin center](https://entra.microsoft.com)
(switch to that directory → **Overview** → **Delete tenant**).

### Architecture

Infrastructure is managed with Bicep (`azure/bicep/`); `deploy.sh` is a wrapper
that provisions Entra External ID (Graph API), deploys the Bicep templates, and
publishes the function code.

```
User → Azure Functions (HTTP) → Main Handler (route dispatch)
                                    ├── Entra External ID (auth)
                                    ├── Blob Storage (video storage + thumbnails)
                                    ├── Cosmos DB (metadata + username mapping)
                                    └── Blob Trigger Function (frame extraction + thumbnail generation)
```

- Single Function App handles all API routes (path-based routing)
- Authentication delegated to Entra External ID (no passwords stored in the app)
- User videos isolated under `users/{uid}/` prefix
- Direct upload to Blob Storage via SAS URLs (no function proxy)
- Blob trigger automatically extracts a thumbnail from a video frame

### Cost Estimate

All services are pay-per-use. Low usage typically falls within the Azure Free Tier.

These are estimates only. Actual costs depend on usage patterns and may vary. Always monitor your cloud provider's billing dashboard.

| Service | Free Tier |
|---------|-----------|
| Azure Functions (Flex Consumption) | Monthly free grant of execution time |
| Cosmos DB (serverless) | Pay per request unit consumed |
| Blob Storage | ~$0.02/GB/month (Hot tier) |
| Application Insights | 5 GB/month |
| Entra External ID | 50,000 MAU |

### Security Recommendations for Production

These are examples only — not an exhaustive list. Evaluate your own requirements and apply additional measures as needed.

- **Managed Identity**: Extend managed-identity access to Cosmos DB to remove connection strings ([docs](https://learn.microsoft.com/azure/cosmos-db/how-to-setup-rbac))
- **Network restrictions**: Use Private Endpoints for Cosmos DB and Storage ([docs](https://learn.microsoft.com/azure/cosmos-db/how-to-configure-private-endpoints))
- **WAF**: Place Azure Front Door with WAF in front of the Function App ([docs](https://learn.microsoft.com/azure/web-application-firewall/overview))
- **CORS restriction**: Limit allowed origins to specific domains
- **Rate limiting**: Configure Azure API Management or custom middleware
- **Share URL limits**: Consider adding file size limits, upload count limits, and Content-Type validation

---

## 日本語

> Azure アカウントが必要です（[無料で作成](https://azure.microsoft.com/free/)）

### クイックスタート

[![Open in Cloud Shell](https://img.shields.io/badge/Azure-Cloud_Shell-blue?logo=microsoftazure)](https://shell.azure.com)

1. 上記の **Cloud Shell** ボタンをクリック
2. リポジトリをクローンしてデプロイスクリプトを実行:
   ```bash
   git clone https://github.com/daily-cloud-app/video.git
   cd video/azure
   chmod +x deploy.sh && ./deploy.sh
   ```
3. 途中で表示されたら、新しいサインイン用ディレクトリへの1回きりのサインインを完了
4. 出力された API エンドポイント URL をアプリに入力

> [!NOTE]
> サインアップ・メール確認・サインイン・パスワードリセットは Microsoft Entra
> External ID が処理します。既定では新しいサインイン用ディレクトリを自動作成します。
> 既存のものを使う場合は `ENTRA_TENANT_ID=<guid> ./deploy.sh` のように実行してください。
> そのディレクトリの管理者である必要があります。

### パラメータ一覧

[deploy.sh](deploy.sh) 実行時に環境変数を指定することでカスタマイズが可能です。

   ```bash
   APP_NAME="myalbum" LOCATION="japaneast" ./deploy.sh
   ```

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| CREATE_TENANT | `true` | サインイン用ディレクトリ（External ID テナント）を新規作成。`false` でスキップ |
| ENTRA_TENANT_ID | (作成) | 既存のサインイン用ディレクトリを再利用する場合に指定（作成をスキップ） |
| RESOURCE_GROUP | `daily-cloud-video-rg` | 対象リソースグループ |
| LOCATION | `eastus` | デプロイリージョン |
| APP_NAME | `dailycloudvideo` | リソース名のベース |
| REQUIRE_EMAIL | `true` | サインアップ時にメール必須 |
| ENABLE_SHARE_URL | `true` | アップロード URL 共有機能 |
| ENABLE_SHARE_DOWNLOAD_URL | `true` | ダウンロード URL 共有機能 |
| ENABLE_LABEL_SHARING | `true` | ラベル共有機能 |
| SHARE_UPLOAD_URL_EXPIRY_HOURS | `24` | アップロード URL の有効期限（時間） |
| SHARE_DOWNLOAD_URL_EXPIRY_HOURS | `72` | ダウンロード URL の有効期限（時間） |

### アプリでの接続

1. **設定** → エンドポイント URL を入力 → **保存**
2. **接続テスト** で確認
3. **ログイン** からアカウント作成

### リソースの削除

[![Open in Cloud Shell](https://img.shields.io/badge/Azure-Cloud_Shell-blue?logo=microsoftazure)](https://shell.azure.com)

```bash
az group delete --name daily-cloud-video-rg --yes --no-wait
```

`CREATE_TENANT=true` でサインイン用ディレクトリを作成した場合は、
[Microsoft Entra 管理センター](https://entra.microsoft.com) から別途削除してください
（対象ディレクトリに切り替え → **概要** → **テナントの削除**）。

### アーキテクチャ

インフラは Bicep（`azure/bicep/`）で管理されます。`deploy.sh` は Entra External ID
のプロビジョニング（Graph API）、Bicep テンプレートのデプロイ、関数コードの発行を
行うラッパーです。

```
ユーザー → Azure Functions (HTTP) → メインハンドラー (ルートディスパッチ)
                                        ├── Entra External ID (認証)
                                        ├── Blob Storage (動画保存 + サムネイル)
                                        ├── Cosmos DB (メタデータ + username マッピング)
                                        └── Blob Trigger 関数 (フレーム抽出 + サムネイル生成)
```

- 全 API を1つの Function App で処理（パスベースルーティング）
- 認証は Entra External ID に委譲（アプリ側にパスワードを保存しない）
- ユーザーの動画は `users/{uid}/` プレフィックスで分離
- SAS URL で Blob Storage に直接アップロード（関数を経由しない）
- Blob トリガーで自動的に動画フレームからサムネイル生成

### コスト目安

すべて従量課金。少人数であれば Azure 無料枠内に収まります。

以下はあくまで目安です。実際の費用は利用状況により異なります。各クラウドプロバイダーの請求ダッシュボードを定期的に確認してください。

| サービス | 無料枠 |
|----------|--------|
| Azure Functions (Flex Consumption) | 毎月の無料実行時間枠 |
| Cosmos DB（サーバーレス） | 消費した要求ユニット分の従量課金 |
| Blob Storage | ~$0.02/GB/月（ホット層） |
| Application Insights | 月5 GB |
| Entra External ID | 月50,000 MAU |

### 本番運用時のセキュリティ推奨事項

以下は一例であり、これだけで十分というわけではありません。要件に応じて追加の対策を検討してください。

- **マネージド ID**: Cosmos DB もマネージド ID アクセスに拡張し接続文字列を排除 ([docs](https://learn.microsoft.com/ja-jp/azure/cosmos-db/how-to-setup-rbac))
- **ネットワーク制限**: Cosmos DB と Storage に Private Endpoints を使用 ([docs](https://learn.microsoft.com/ja-jp/azure/cosmos-db/how-to-configure-private-endpoints))
- **WAF**: Azure Front Door + WAF を Function App の前に配置 ([docs](https://learn.microsoft.com/ja-jp/azure/web-application-firewall/overview))
- **CORS の制限**: 許可するオリジンを特定ドメインに限定
- **レート制限**: Azure API Management またはカスタムミドルウェアで設定
- **共有 URL の制限**: ファイルサイズ制限、アップロード回数制限、Content-Type 検証の追加を検討
