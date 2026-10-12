# らく助 使い捨て試験環境（staging）

エージェント（rk）の**書き込みの流れ**（案 → 了承 → バックアップ → 反映 → 照合 → 元に戻す）を、本番に触らずに試すための環境。
PO 決定（2026-10-12・設計 §9 Q1）: 「VPS に使い捨ての Postgres（最新のバックアップ）＋使い捨てのらく助を立て、試験後に消す。本番 DB・カイポケのロボットにはつながない」。

**実行にはディレクターの了承が要る。** 立てたら、試験が終わったその日のうちに必ず消す（`stg-down.sh`）。

## 中身

| ファイル | 役割 |
|---|---|
| `docker-compose.staging.yml` | `stg-postgres`（postgres:16-alpine・512MB 上限）と `stg-backend`（今の本番と同じ backend の image・768MB 上限・`127.0.0.1:18101`） |
| `stg-up.sh` | 立てる: 安全確認 → 最新バックアップの流し込み → backend 起動 → 疎通 |
| `stg-down.sh` | 消す: コンテナ・網・volume・`.env.staging`・流し込みログ → 消えたことの確認 |
| `stg-create-agent-user.sh` ＋ `stg_create_agent_user.py` | 試験環境にだけエージェント用の管理者ユーザーを作り、パスワードを 1 回だけ表示 |
| `.env.staging` | `stg-up.sh` が作る（git 管理外・600）。`stg-down.sh` が消す |

## 安全の仕組み

- **本番 DB につながない**: 試験用の Postgres（`stg-postgres`・volume `carelink-stg-pg`）を新しく作り、バックアップの写しを流し込む。backend の `DATABASE_URL` は `stg-postgres` だけを向く（起動後に向き先を検査し、違えば止めて片付ける）。
- **カイポケのロボットにつながない**: 網は `carelink-stg` だけ（RPA の網 `playwrighttest1_default` に入らない）。`KAIPOKE_API_BASE_URL` は塞いだ宛先、`KAIPOKE_API_TOKEN`・`KAIPOKE_CRED_SECRET` は空、JWT 鍵も本番と別なので DB の中のカイポケのログイン情報は復号できない。さらに `kaipoke-api.net` などのホスト名を `127.0.0.1` に向けて二重に塞ぐ。
- **外部のサービスに出さない**: Google 地図の鍵は空、音声（Vertex）は `VOICE_AI_PROVIDER=none`・鍵ファイルなし。本番の写真・音声・サイン・鍵のフォルダは mount しない。
- **外から見えない**: Postgres はポートを出さない。backend は `127.0.0.1:18101` だけ（cloudflared・frontend なし）。
- **本番を圧迫しない**: メモリ上限（合計約 1.3GB）。空きメモリ（MemAvailable）が 1.5GB 未満、ディスク 3GB 未満なら立てない。`restart: "no"`（サーバー再起動で勝手に上がらない）。cron なし。
- **本番の秘密を写さない**: `.env.staging` には本番 `.env` から無害な設定（JWT の有効期限・ロックアウト回数など）だけを写す。DB のパスワードと JWT 鍵は試験用に新しく作る。
- 途中で失敗したら `stg-up.sh` が自動で `stg-down.sh` を流す。

`.env.staging` で空にする／塞ぐ変数:
`KAIPOKE_API_BASE_URL`（塞いだ宛先）・`KAIPOKE_API_TOKEN`・`KAIPOKE_CRED_SECRET`・`KAIPOKE_RPA_SERVICE_BRANCH_ENABLED=false`・`KAIPOKE_EXPORT_DIR`（/tmp）・`KAIPOKE_NOVNC_URL`・`INTEGRATION_BASE_URL`・`GOOGLE_MAPS_API_KEY`・`VOICE_AI_PROVIDER=none`・`VERTEX_PROJECT_ID`・`GOOGLE_APPLICATION_CREDENTIALS`（存在しないパス）・`VISIT_PHOTOS_DIR`/`VISIT_AUDIO_DIR`/`VISIT_SIGNATURES_DIR`（/tmp）。
新しく作る: `POSTGRES_PASSWORD`・`DATABASE_URL`・`JWT_SECRET`。`APP_ENV=staging`。写さない: `NEXTAUTH_*`・`NEXT_PUBLIC_*`・`BACKEND_API_BASE_URL`・`CORS_ORIGINS`（試験用に 127.0.0.1 を入れる）。

## 手順（VPS の上で・root）

置き場所はどこでもよい（例: `/opt/carelink/agent/staging`。スクリプトは自分のフォルダを基準に動く）。

```bash
# 1. 立てる（既定は /opt/carelink/backups の最新 daily-*.sql.gz。ファイルを指定してもよい）
bash stg-up.sh
bash stg-up.sh /opt/carelink/backups/daily-20261011-1730.sql.gz

# 2. エージェント用の管理者ユーザーを作る（パスワードは 1 回だけ表示）
bash stg-create-agent-user.sh                       # 既定 rakusuke-agent@example.com

# 3. 試験（下の「rk から使う」）

# 4. 消す（必ず）
bash stg-down.sh
```

`stg-up.sh` がすること: 既に立っていないか（コンテナ・volume・網・ポート 18101）→ 空きメモリ・ディスク → バックアップの検査（`gzip -t`・1 DB の plain dump か）→ 本番で今動いている `carelink-backend` の image ID を使う → `.env.staging` を作る → `stg-postgres` 起動 → 流し込み（`psql -v ON_ERROR_STOP=1`）→ `stg-backend` 起動 → 安全確認（DB の向き先・網・鍵）→ バックアップの alembic の版が image より古いときだけ、**試験 DB にだけ** `alembic upgrade head` → `healthz` → メモリの使用量を表示。

## rk から使う

rk は環境変数でつなぎ先を変える（`agent/rk/config.py`）:

| 変数 | 試験環境の値 | 本番の既定 |
|---|---|---|
| `RK_API_PORT` | `18101` | `18001` |
| `RK_PG_CONTAINER` | `stg-postgres` | `carelink-postgres` |
| `RK_IDENTIFIER` / `RK_PASSWORD` | `stg-create-agent-user.sh` の出力 | `~/.rakusuke/credentials.json` |

```bash
# PC から（RK_MODE=pc・SSH でサーバーの中の 127.0.0.1:18101 を呼ぶ）
RK_API_PORT=18101 RK_PG_CONTAINER=stg-postgres RK_IDENTIFIER=rakusuke-agent@example.com RK_PASSWORD=... \
  PYTHONPATH=. python -m rk whoami
```

注意:
- 試験環境の資格情報を本番用の `~/.rakusuke/credentials.json` に上書きしない（環境変数で渡す）。トークンの置き場所も本番と分けたいときは別の `HOME` で動かす（I1 へ: 環境ごとにトークンのファイルを分けると安全）。
- rk を `RK_ENV=staging` で使う（ポート 18101・コンテナ stg-postgres・バックアップは `/opt/carelink/agent-staging/backups`・資格情報とトークンは `~/.rakusuke/*-staging.json`）。本番のバックアップ置き場には書かない。試験の後に `/opt/carelink/agent-staging` ごと消す。
- 試験環境のデータは本番の写し（利用者名・住所入り）。出力は本番と同じく `docs/reports/agent/` にだけ置き、外に出さない。

## 現状（2026-10-12 調べ・読むだけ）

- メモリ 7.9GB・スワップなし・空き（available）約 2.5GB。本番 backend 約 210MB・postgres 約 130MB・カイポケ RPA 約 500MB。ほかのアプリも同居しているので、立てている時間は短く。
- ディスク空き 36GB。バックアップは `daily-YYYYMMDD-1730.sql.gz`（UTC 17:30＝日本 02:30・約 4MB）。
- ポート 18101 は空き。backend の image は `carelink-backend:latest`（本番の commit 9111655）。
