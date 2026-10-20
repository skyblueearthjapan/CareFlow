# Google ドライブの接続の準備（勤務表のスプレッドシート）

正典: `docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md` §7-3・§10-2。
PO の Google アカウントで 1 回だけ行う。**秘密の値（クライアントシークレット・API キー）はこの文書・チャット・git に書かない**。

準備が済むまで、らく助の「設定 → Google ドライブ」（`/settings/google-drive`）は「準備ができていません」と案内を出すだけで、ほかの機能（休みの確定・実績の記録）はそのまま使える。勤務表は「未作成」として残り、接続後に作れる。

## 0. 先にお客様へ確かめること
- 会社の Google Workspace で、外部のアプリ（らく助）の接続が許されているか（管理コンソールの「API の制御」）。止められていると許可の画面でエラーになる。
- 勤務表を置く**共有ドライブ**（推奨。作った人が辞めてもファイルが残る）と、その中のフォルダ。

## 1. Google Cloud のプロジェクト
1. https://console.cloud.google.com/ で新しいプロジェクトを作る（例「rakusuke-kinmu」。音声記録の `rakusuke-voice` とは分けてよい）。
2. 「API とサービス → ライブラリ」で次の 3 つを**有効にする**: Google Drive API・Google Sheets API・Google Picker API。
3. 「IAM と管理 → 設定」で**プロジェクト番号**（数字）を控える → `.env` の `GOOGLE_PICKER_APP_ID`。

## 2. OAuth の同意画面（Google Auth Platform）
1. 「API とサービス → OAuth 同意画面」（新しい画面では「Google Auth Platform → ブランディング／対象」）。
2. 対象（User type）: お客様の Workspace の中だけで使うなら「内部」、そうでなければ「外部」。
3. アプリ名「らく助」・サポートのメール・ホームページ `https://carelink.kaipoke-api.net`・承認済みドメイン `kaipoke-api.net`。
4. スコープ（データアクセス）に **`https://www.googleapis.com/auth/drive.file`** だけを足す（drive.file は「機密ではない」スコープなので Google の審査は要らない）。
5. 「外部」の場合は**「本番環境にする（アプリを公開）」**を押す。「テスト中」のままだと許可が **7 日**で切れ、らく助の画面に「もう一度接続してください」が出続ける。

## 3. OAuth のクライアント
1. 「認証情報 → 認証情報を作成 → OAuth クライアント ID」・種類は**ウェブ アプリケーション**。
2. 承認済みのリダイレクト URI に次を 1 つだけ入れる（一字一句同じに）:
   `https://carelink.kaipoke-api.net/api/v1/google-drive/callback`
3. 作ったら**クライアント ID** と**クライアント シークレット**を控える → `.env` の `GOOGLE_DRIVE_CLIENT_ID`・`GOOGLE_DRIVE_CLIENT_SECRET`。

## 4. Picker の API キー
1. 「認証情報 → 認証情報を作成 → API キー」。
2. キーの制限: **アプリケーションの制限 = ウェブサイト（HTTP リファラー）**で `https://carelink.kaipoke-api.net/*`。**API の制限 = Google Picker API** だけ。
3. キーを控える → `.env` の `GOOGLE_PICKER_API_KEY`。

## 5. VPS の `.env`（`/opt/carelink/.env`）
`docs/deployment/env-template.md` の「Google ドライブ」の段に値を入れる。

| 名前 | 中身 |
|---|---|
| `GOOGLE_DRIVE_CLIENT_ID` | 3 のクライアント ID |
| `GOOGLE_DRIVE_CLIENT_SECRET` | 3 のクライアント シークレット（秘密） |
| `GOOGLE_DRIVE_REDIRECT_URI` | `https://carelink.kaipoke-api.net/api/v1/google-drive/callback` |
| `GOOGLE_PICKER_API_KEY` | 4 の API キー |
| `GOOGLE_PICKER_APP_ID` | 1 のプロジェクト番号 |
| `GOOGLE_DRIVE_TOKEN_SECRET` | リフレッシュトークンを暗号化する鍵の元。`openssl rand -hex 32` で作る（秘密）。変えると接続し直しになる |

- 秘密の値を別のファイル（`/opt/carelink/secrets/` の下）に置く場合も、**ディレクトリの権限は 711 のまま**にする（700 にすると backend から読めない・音声記録の Vertex が止まった前例）。置いたら backend のコンテナから読めることを確かめる。
- 入れたら backend を作り直す: `docker compose ... up -d --force-recreate backend`。

## 6. Cloudflare（公開ルート）
- Google からの戻りは、接続ボタンを押した管理者のブラウザーが `https://carelink.kaipoke-api.net/api/v1/google-drive/callback` を開く形で届く。トンネルの既存のルート（`^/api/v1/.*` → backend）に入るので、**新しいルートは要らない見込み**。
- デプロイの前に本番で確かめる: ログイン中のブラウザーで `https://carelink.kaipoke-api.net/api/v1/google-drive/callback?error=access_denied` を開き、らく助の `/settings/google-drive` に「許可されませんでした」と出れば届いている。届かなければ公開ルートの 6 本目として `^/api/v1/google-drive/callback$` → backend を `*` の行より上に足す。
- 定期処理（cron）は作らない。

## 7. つないで確かめる
1. 管理者でログインし「設定 → Google ドライブ」を開く → 「Google ドライブに接続」→ Google の画面で許可（「Google ドライブ」のチェックを入れる）。
2. 戻ってきたら「フォルダを選ぶ」→ Picker で共有ドライブのフォルダを選ぶ → 名前と「（共有ドライブ）」が出る。
3. 「もう一度接続してください」が出たら（許可の取り消し・6 か月使わなかったなど）、同じ画面で「もう一度接続する」。管理者全員のベルにも知らせが届く。
