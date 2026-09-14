# セッション引き継ぎ 2026-09-14（月次 MTG「全員ログイン不可」の根治 + モバイルログインカード再発行）

**次のエージェントへ**: カイポケ連携の本線は `session-2026-09-11-HANDOFF.md`（W38 方針・grade_change 初回・月跨ぎ週・残骸ジョブ）。本ファイルは 9/14 の単発インシデント対応のみ。

## ★ 最初の 3 分
0. **本番稼働**: らく助 `4b9dd28`（2026-09-14 18:30 JST・migration 無し・backend/frontend 再作成・healthz 200 両方）。RPA は変更なし `0e00107`。バックアップ `/opt/carelink/backups/pre-deploy-login-ratelimit-20260914-*.sql.gz`。
1. **事象**: 月次 MTG（13:36〜13:48 JST）で職員がスマホからほぼ全員ログイン不可。「メール／スタッフIDまたはパスワードが正しくありません」表示。PO 依頼=「既存職員のアカウント一覧を再作成・パスワード再確認・QR 付き A4 1 枚の HTML」。
2. **真因（確定・ログで証明）**: `/api/v1/auth/login` の slowapi `5/15minutes` は「クライアント IP」キーだが、ブラウザのログインは NextAuth `authorize` の **サーバー側 fetch** で BE に届くため BE から見える IP は常にフロントコンテナ `172.24.0.4`。**全ユーザーが 1 バケット共有** → 13:36〜13:39 に 200×5・13:41〜13:48 に 429×21。FE は 429 を `return null` にして「パスワードが違う」と誤案内。
3. **パスワードは無関係**: 生存 11 アカウント全部が共通 PW と bcrypt 一致・`locked_until` 無し・`must_change_password` 全 false（本番 backend コンテナ内で `verify_password` 実行・読み取りのみ）。
4. **納品済み**: `staff-login-cards.html`（リポジトリ直下・gitignore）= 7 名（S001 川名/S002 熊澤/S004 高岡/S005 本名/S006 宇田川/S007 髙梨/S008 小西）・A4 1 枚・各カードに QR（`/login?identifier=Sxxx&callbackUrl=%2Fm%2Fhome`＝ID 事前入力・PW は QR に含めない）・「ログインできないとき」枠。SendUserFile で松岡様向けに送付済み。

## 1. 根治の内容（コミット `fe049e5` + `4b9dd28`）
- BE `app/core/rate_limit.py`: `client_ip` = `CF-Connecting-IP` → `X-Forwarded-For` **右端** → ピア IP。`login_key` = IP + sha256(識別子)[:16]。定数 `LOGIN_LIMIT_PER_IDENTIFIER="5/15minutes"` / `LOGIN_LIMIT_PER_IP="100/15minutes"`。
- BE `/auth/login`: 二段リミット（識別子スコープ 5/15min + IP 100/15min）。`X-Login-Identifier` が本文の識別子と不一致なら 400（ヘッダでバケットを広げられない）。
- FE `lib/auth.ts`: `authorize(raw, request)` が `CF-Connecting-IP`（Cloudflare が上書き＝偽装不可）と `X-Login-Identifier` を転送。429/423 は `CredentialsSignin` サブクラス（`code=rate_limited` / `locked`）で画面へ → `lib/login-messages.ts` の専用文言。
- FE `/login?identifier=…` で ID 事前入力（QR 用）。
- tests: `tests/test_auth.py` に 5 件（識別子スコープ / CF-Connecting-IP / XFF 左端回転無効 / ヘッダ不一致 400 / IP 上限）。
- **レビュー（code-reviewer Opus）で CRITICAL を捕捉**: `/api/v1/*` は cloudflared がフロントを経由せず BE に直結＝公開エンドポイントなので、XFF **左端**を信用すると偽装でリミット無効化できる → CF-Connecting-IP 優先 + XFF 右端に是正。APPROVE 取得後にデプロイ。

## 2. 本番実測（デプロイ後・偽 ID で検証・実アカウントに影響なし）
- 直叩き: 同一 ID 401×5 → 429。同 IP の別 ID → 401。ヘッダ不一致 → 400。
- NextAuth 経由: 偽 ID 6 回目で `Location=/login?error=CredentialsSignin&code=rate_limited`（5 回目までは `code=credentials`）。直叩き 12 回の後でも FE 経由の別 ID は通る＝識別子スコープが効いている。
- S002 実ログイン（FE 経由）→ 302・session に `username=s002 role=staff`。`/login?identifier=S002` は 200。

## 3. 残タスク・注意
- 既知の既存 fail（本変更と無関係）: `test_login_locks_after_5_failed_attempts`（SQLite naive datetime 比較）・`test_admin_create_user_defaults_must_change_password_true`（staff の username 必須化に未追随）。
- リミッタはインプロセスのメモリストア（uvicorn 単一ワーカーなので正確・デプロイでリセット）。複数ワーカー化するなら Redis ストレージへ。
- `?identifier=` はクエリ文字列（アクセスログに残る）。スタッフコードは秘匿情報ではないので許容（レビュー LOW）。
- 松岡様のブラウザキャッシュ: frontend を再作成したので現場は初回ハードリロード推奨（PWA 自己回復あり）。
- カード再発行手順: scratchpad の `gen_login_cards.py`（segno）+ 本番 `users⋈staff` の json_agg。memory `careflow-login-ratelimit-incident` に記載。

## 4. 参照
- memory: `careflow-login-ratelimit-incident` / `careflow-unified-password` / `careflow-staff-account-linking`
- 前セッション: `session-2026-09-11-HANDOFF.md`
