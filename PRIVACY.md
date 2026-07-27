# Privacy Policy — Daily Cloud Photo

- [English](#english)
- [日本語](#日本語)

---

## English

Last updated: June 14, 2026

### 1. Introduction

Daily Cloud Photo ("the App") is an application for backing up photos to a cloud server that the user sets up and manages themselves.

The developer does not collect or manage user data on any developer-operated server. All data is stored in the user's own cloud environment.

### 2. Information We Collect

#### Information You Provide

| Data | Purpose | Storage |
|------|---------|---------|
| Email address | Account registration & login | User's own server |
| Password | Authentication | User's own server (hashed) |
| Phone number (optional, server-dependent) | Two-factor auth | User's own server |

These are never sent to the developer's servers — only to the backend server configured by the user.

#### Information from Your Device

| Data | Purpose | Destination |
|------|---------|-------------|
| Photo files | Cloud backup | User's own server |
| Photo capture date | Timeline display & auto-labeling | User's own server |
| File name | Photo identification | User's own server |

#### Information Stored on Device

| Data | Purpose |
|------|---------|
| Auth tokens | Maintaining login state |
| Photo metadata (ID, date, sync status) | Offline display & sync management |
| Thumbnail cache | Fast image display |
| Server endpoint URL | Connection target |
| Label information | Photo organization & filtering |

All local data is stored in the app's private storage area, inaccessible to other apps. `android:allowBackup="false"` prevents inclusion in device backups.

### 3. Information We Do Not Collect

- Location (GPS)
- Contacts
- Microphone or camera (no capture feature)
- Advertising ID
- Analytics data
- Crash reports
- Device identifiers (IMEI, Android ID, etc.)
- Cookies

No analytics SDK (Firebase Analytics, Google Analytics, etc.) or crash reporting SDK (Crashlytics, etc.) is included.

### 4. Data Destinations

The App communicates only with the server endpoint configured by the user. No data is sent to any developer-operated server.

Communication occurs during:
- Account creation & login
- Photo upload (automatic on Wi-Fi)
- Retrieving cloud photo list
- Label sync
- Sharing features

All communication is encrypted via HTTPS (TLS).

### 5. Background Processing

The App uses Android WorkManager for:
- **Automatic photo upload**: Runs only on Wi-Fi
- **New photo detection**: Detects newly added photos for upload

Background processing follows OS battery optimization and does not interfere with device operation.

### 6. Photo Access

| Permission | Target | Purpose |
|------------|--------|---------|
| `READ_MEDIA_IMAGES` | Android 13+ | Photo library read access |
| `READ_MEDIA_VISUAL_USER_SELECTED` | Android 14+ | Access to user-selected photos only |
| `READ_EXTERNAL_STORAGE` | Android 12 and below | Photo library read access (compatibility) |

Photos are uploaded only to the user's own server. Photo content is not analyzed or sent to third parties.

### 7. Third-Party Sharing

The App does not provide, sell, or share user data with third parties.

The "label sharing" feature allows sharing photos with other users on the same server, but only through explicit user action.

### 8. Data Deletion

- **In-app deletion**: Photos become hidden. Recoverable from trash.
- **Empty trash**: Permanently removes data from the app's database.
- **Cloud data**: Server-side photos are not deleted by app actions. Cloud resource management is the user's responsibility.
- **Sync data reset**: Deletes all local data (cache, database). Does not affect device photos or server images.

### 9. Security

- All external communication encrypted via HTTPS (TLS)
- Passwords hashed on the server
- Auth credentials stored in app-private storage on device
- `android:allowBackup="false"` prevents backup data leakage

### 10. Children

The App is not intended for users under 13 years of age.

### 11. Disclaimer

The App provides functionality to upload photos to cloud storage managed by the user. The developer is not liable for data loss, upload failures, cloud storage fees, configuration errors, or other damages arising from use of the App.

The App may be modified or discontinued without notice for improvements or bug fixes.

### 12. Changes to This Policy

This policy may be updated as needed. Significant changes will be communicated through app updates.

### 13. Contact

- GitHub: https://github.com/daily-cloud-app/photo/issues

---

## 日本語

最終更新日: 2026年6月14日

### 1. はじめに

Daily Cloud Photo（以下「本アプリ」）は、ユーザーが自身で用意・管理するクラウドサーバーに写真をバックアップするためのアプリケーションです。

本アプリの開発者（以下「開発者」）はユーザーのデータを自社サーバーで収集・管理することはありません。すべてのデータはユーザー自身が管理するクラウド環境に保存されます。

### 2. 収集する情報

#### ユーザーが入力する情報

| データ | 目的 | 保存先 |
|--------|------|--------|
| メールアドレス | アカウント登録・ログイン | ユーザー自身のサーバー |
| パスワード | 認証 | ユーザー自身のサーバー（ハッシュ化） |
| 電話番号（サーバー設定により任意） | 二段階認証等 | ユーザー自身のサーバー |

※ これらの情報は開発者のサーバーには送信されません。ユーザーが自身で構築したバックエンドサーバーにのみ送信されます。

#### 端末から取得する情報

| データ | 目的 | 送信先 |
|--------|------|--------|
| 写真ファイル | クラウドバックアップ | ユーザー自身のサーバー |
| 写真の撮影日時 | 時系列表示・自動ラベル生成 | ユーザー自身のサーバー |
| ファイル名 | 写真の識別 | ユーザー自身のサーバー |

#### 端末内に保存する情報

| データ | 目的 |
|--------|------|
| 認証トークン | ログイン状態の維持 |
| 写真メタデータ（ID、日時、同期状態） | オフライン表示・同期管理 |
| サムネイルキャッシュ | 高速な画像表示 |
| サーバーエンドポイントURL | 接続先の記憶 |
| ラベル情報 | 写真の整理・フィルタリング |

これらはすべて端末内のアプリ専用領域に保存され、他のアプリからアクセスできません。`android:allowBackup="false"` により、端末バックアップにも含まれません。

### 3. 収集しない情報

- 位置情報（GPS）
- 連絡先
- マイク・カメラ（撮影機能なし）
- 広告ID
- アナリティクスデータ
- クラッシュレポート
- 端末識別子（IMEI、Android ID 等）
- Cookie

本アプリにはアナリティクス SDK およびクラッシュレポート SDK は組み込まれていません。

### 4. データの送信先

本アプリはユーザー自身が設定したサーバーエンドポイントにのみ通信します。開発者が運営するサーバーへのデータ送信は一切ありません。

通信が発生する場面:
- アカウント作成・ログイン時
- 写真のアップロード時（Wi-Fi 接続時に自動実行）
- クラウドの写真一覧を取得する時
- ラベル情報の同期時
- 共有機能の利用時

すべての通信は HTTPS（TLS）で暗号化されます。

### 5. バックグラウンド処理

本アプリは Android の WorkManager を使用し、以下のバックグラウンド処理を行います:

- **写真の自動アップロード**: Wi-Fi 接続時にのみ実行されます
- **新しい写真の検出**: 端末に追加された写真を検出し、アップロード対象として登録します

バックグラウンド処理は OS のバッテリー最適化に従い、端末の動作を阻害しません。

### 6. 写真へのアクセス

| 権限 | 対象バージョン | 用途 |
|------|--------------|------|
| `READ_MEDIA_IMAGES` | Android 13以降 | 写真ライブラリへの読み取り |
| `READ_MEDIA_VISUAL_USER_SELECTED` | Android 14以降 | ユーザーが選択した写真のみへのアクセス |
| `READ_EXTERNAL_STORAGE` | Android 12以下 | 写真ライブラリへの読み取り（互換用） |

取得した写真はユーザー自身のサーバーにのみアップロードされます。写真の内容を解析したり、第三者に送信することはありません。

### 7. 第三者への提供

本アプリはユーザーデータを第三者に提供、販売、共有しません。

「ラベル共有」機能により同じサーバーの他のユーザーと写真を共有できますが、ユーザー自身が明示的に操作した場合にのみ行われます。

### 8. データの削除

- **アプリ内での削除**: 写真を削除するとアプリ上で非表示になります。ゴミ箱機能から復元も可能です。
- **ゴミ箱の消去**: ゴミ箱を空にすると、アプリ内のデータが完全に削除されます。
- **クラウド上のデータ**: サーバー上の写真ファイルはアプリ操作では削除されません。クラウドリソースの管理はユーザー自身の責任となります。
- **同期データの初期化**: 設定画面から「同期データを初期化」を実行すると、アプリ内の全データ（キャッシュ、データベース）が削除されます。端末の写真やサーバーの画像には影響しません。

### 9. セキュリティ

- すべての外部通信は HTTPS（TLS）で暗号化されています
- パスワードはサーバー上でハッシュ化して保存されます
- 認証情報は端末内のアプリ専用領域に保存されます
- `android:allowBackup="false"` により、端末バックアップへのデータ混入を防止しています

### 10. 子どもの利用

本アプリは13歳未満の方を対象としておらず、13歳未満の方の利用は想定していません。

### 11. 免責事項

本アプリは、ユーザー自身が管理するクラウドストレージへ写真をアップロードする機能を提供するものです。

開発者は、本アプリの利用に起因するデータの消失、アップロードの失敗、クラウドストレージの利用料金、設定の誤り、その他の損害について責任を負いません。

ユーザーは自身の責任においてサーバーの設定・運用およびデータの管理を行うものとします。

本アプリは機能改善や不具合修正のため、予告なく仕様変更や提供停止を行う場合があります。

### 12. 本ポリシーの変更

本ポリシーは必要に応じて更新される場合があります。重要な変更がある場合はアプリのアップデート時にお知らせします。

### 13. お問い合わせ

- GitHub: https://github.com/daily-cloud-app/photo/issues
