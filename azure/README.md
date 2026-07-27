# Daily Cloud Photo — Azure Backend

- [English](#english)
- [日本語](#日本語)

---

## English

> Requires an Azure account ([create free](https://azure.microsoft.com/free/))

### Quick Start

[![Open in Cloud Shell](https://img.shields.io/badge/Azure-Cloud_Shell-blue?logo=microsoftazure)](https://shell.azure.com)

1. Click the **Cloud Shell** button above
2. Clone and deploy:
   ```bash
   [ -d photo ] || git clone https://github.com/daily-cloud-app/photo.git
   cd photo && git checkout -- . && git pull --ff-only && cd azure
   chmod +x deploy.sh && ./deploy.sh daily-cloud-photo-rg japaneast
   ```
3. Copy the API endpoint URL from the output into the app

### Parameters

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Fdaily-cloud-app%2Fphoto%2Fmain%2Fazure%2Fazuredeploy.json) 

You can also deploy via the Azure Portal GUI — fill in all parameters through the form.

| Parameter | Default | Description |
|-----------|---------|-------------|
| appName | `dailycloudphoto` | Base name for all resources |
| location | Resource group location | Azure region |
| jwtSecret | (auto-generated) | Secret key for JWT signing |
| accessTokenExpireMinutes | `60` | Access token lifetime (minutes) |
| refreshTokenExpireDays | `30` | Refresh token lifetime (days) |
| requireEmail | `true` | Require email for signup |
| requirePhone | `false` | Require phone number for signup |
| enableShareUrl | `true` | Enable upload URL sharing feature |
| enableLabelSharing | `true` | Enable label sharing between users |
| appDisplayName | `Daily Cloud Photo Backend` | Display name shown in the app |

### Connecting the App

1. **Settings** → Enter the endpoint URL → **Save**
2. Run **Connection Test**
3. **Login** → Create account

### Deleting Resources

[![Open in Cloud Shell](https://img.shields.io/badge/Azure-Cloud_Shell-blue?logo=microsoftazure)](https://shell.azure.com)

```bash
az group delete --name daily-cloud-photo-rg --yes --no-wait
```

### Architecture

```
User → Azure Functions (HTTP) → Main Handler (route dispatch)
                                    ├── Custom JWT Auth (bcrypt + PyJWT)
                                    ├── Blob Storage (photo storage + thumbnails)
                                    ├── Cosmos DB (metadata)
                                    └── Blob Trigger Function (EXIF + thumbnail generation)
```

- Single Function App handles all API routes (path-based routing)
- User photos isolated under `users/{uid}/` prefix
- Direct upload to Blob Storage via SAS URLs (no function proxy)
- Blob trigger automatically extracts EXIF date + generates thumbnails

### Cost Estimate

All services use serverless/consumption pricing. Low usage is extremely cheap.

These are estimates only. Actual costs depend on usage patterns and may vary. Always monitor your cloud provider's billing dashboard.

| Service | Free Tier |
|---------|-----------|
| Azure Functions | 1M executions/month |
| Cosmos DB | First 1000 RU/s free tier available |
| Blob Storage | ~$0.02/GB/month (Hot tier) |
| Application Insights | 5 GB/month |

### Security Recommendations for Production

These are examples only — not an exhaustive list. Evaluate your own requirements and apply additional measures as needed.

- **JWT Secret rotation**: Periodically rotate the JWT_SECRET app setting
- **Network restrictions**: Use Azure Private Endpoints for Cosmos DB ([docs](https://learn.microsoft.com/en-us/azure/cosmos-db/how-to-configure-private-endpoints))
- **WAF**: Place Azure Front Door with WAF in front of the Function App ([docs](https://learn.microsoft.com/en-us/azure/web-application-firewall/overview))
- **CORS restriction**: Limit allowed origins to specific domains
- **Rate limiting**: Configure Azure API Management or custom middleware
- **Key Vault**: Store secrets in Azure Key Vault instead of app settings ([docs](https://learn.microsoft.com/en-us/azure/key-vault/general/overview))
- **Share URL limits**: Consider adding file size limits, upload count limits, and Content-Type validation

---

## 日本語

> Azure アカウントが必要です（[無料で作成](https://azure.microsoft.com/free/)）

### クイックスタート

[![Open in Cloud Shell](https://img.shields.io/badge/Azure-Cloud_Shell-blue?logo=microsoftazure)](https://shell.azure.com)

1. 上記の **Cloud Shell** ボタンをクリック
2. クローンしてデプロイ:
   ```bash
   [ -d photo ] || git clone https://github.com/daily-cloud-app/photo.git
   cd photo && git checkout -- . && git pull --ff-only && cd azure
   chmod +x deploy.sh && ./deploy.sh daily-cloud-photo-rg japaneast
   ```
3. 出力された API エンドポイント URL をアプリに入力

### パラメータ一覧

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Fdaily-cloud-app%2Fphoto%2Fmain%2Fazure%2Fazuredeploy.json) 

GUI でデプロイする場合は、Azure ポータルからパラメータをフォームで入力できます。

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| appName | `dailycloudphoto` | リソース名のベース |
| location | リソースグループの場所 | Azure リージョン |
| jwtSecret | (自動生成) | JWT 署名用シークレット |
| accessTokenExpireMinutes | `60` | アクセストークン有効期間（分） |
| refreshTokenExpireDays | `30` | リフレッシュトークン有効期間（日） |
| requireEmail | `true` | サインアップ時にメール必須 |
| requirePhone | `false` | サインアップ時に電話番号必須 |
| enableShareUrl | `true` | アップロード URL 共有機能 |
| enableLabelSharing | `true` | ラベル共有機能 |
| appDisplayName | `Daily Cloud Photo Backend` | アプリでの表示名 |

### アプリでの接続

1. **設定** → エンドポイント URL を入力 → **保存**
2. **接続テスト** で確認
3. **ログイン** からアカウント作成

### リソースの削除

[![Open in Cloud Shell](https://img.shields.io/badge/Azure-Cloud_Shell-blue?logo=microsoftazure)](https://shell.azure.com)

```bash
az group delete --name daily-cloud-photo-rg --yes --no-wait
```

### アーキテクチャ

```
ユーザー → Azure Functions (HTTP) → メインハンドラー (ルートディスパッチ)
                                        ├── Custom JWT Auth (bcrypt + PyJWT)
                                        ├── Blob Storage (写真保存 + サムネイル)
                                        ├── Cosmos DB (メタデータ)
                                        └── Blob Trigger 関数 (EXIF + サムネイル生成)
```

- 全 API を1つの Function App で処理（パスベースルーティング）
- ユーザーの写真は `users/{uid}/` プレフィックスで分離
- SAS URL で Blob Storage に直接アップロード（関数を経由しない）
- Blob トリガーで自動的に EXIF 解析 + サムネイル生成

### コスト目安

すべてサーバーレス従量課金。少量利用なら非常に安価。

以下はあくまで目安です。実際の費用は利用状況により異なります。各クラウドプロバイダーの請求ダッシュボードを定期的に確認してください。

| サービス | 無料枠 |
|----------|--------|
| Azure Functions | 月100万実行 |
| Cosmos DB | 1000 RU/s 無料枠あり |
| Blob Storage | ~$0.02/GB/月（ホット層） |
| Application Insights | 月5 GB |

### 本番運用時のセキュリティ推奨事項

以下は一例であり、これだけで十分というわけではありません。要件に応じて追加の対策を検討してください。

- **JWT シークレットのローテーション**: 定期的に JWT_SECRET を変更 
- **ネットワーク制限**: Cosmos DB に Azure Private Endpoints を使用 ([docs](https://learn.microsoft.com/en-us/azure/cosmos-db/how-to-configure-private-endpoints))
- **WAF**: Azure Front Door + WAF を Function App の前に配置 ([docs](https://learn.microsoft.com/en-us/azure/web-application-firewall/overview))
- **CORS の制限**: 許可するオリジンを特定ドメインに限定
- **レート制限**: Azure API Management またはカスタムミドルウェアで設定
- **Key Vault**: アプリ設定の代わりに Azure Key Vault でシークレット管理 ([docs](https://learn.microsoft.com/en-us/azure/key-vault/general/overview))
- **共有 URL の制限**: ファイルサイズ制限、アップロード回数制限、Content-Type 検証の追加を検討
