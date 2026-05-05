# `.env` テンプレート (本番)

VPS 上の **`/opt/carelink/.env` (= リポジトリルート直下、1 ファイルのみ)** に backend / frontend
両方の値を統合して書き込む。runbook の compose コマンドはすべて
`--env-file .env` 付きで呼ぶため、`${VAR}` 補間は repo root の `.env` から行われる。
かつ各 service には `env_file: - ../../.env` (compose ファイル基準で repo root を指す)
を明示参照させているため、shell interpolation と container 内環境変数の双方が同じ値になる。

**実 secret 値は Git に絶対に commit しない**。本ドキュメントはプレースホルダのみ。

> 旧構成 (`backend/.env` + `frontend/.env` を別々に置く) は廃止した。
> Compose は **shell / プロジェクトルートの `.env`** だけを interpolation 用に使うため、
> service-local な `.env` ファイルを置いても `${POSTGRES_PASSWORD}` 等は展開されない。

## 値生成コマンド (VPS 上で実行)

```bash
# 32 byte ランダム base64 (JWT_SECRET / NEXTAUTH_SECRET 用)
openssl rand -base64 32

# 強パスワード (Postgres 用、24 chars)
openssl rand -base64 18 | tr -d '/+=' | cut -c1-24

# UUID (アプリ内識別子用、必要なら)
python3 -c "import uuid; print(uuid.uuid4())"
```

生成した値はパスワードマネージャに保管し、Slack/メール/チケットに貼り付けない。

## 統合 `.env` (`/opt/carelink/.env`)

`backend/.env.example` と `frontend/.env.example` の内容を 1 ファイルに統合し、
本番値で置き換える。下記をそのまま雛形として使用する。

```ini
# =====================================================================
# CareLink production .env (root, /opt/carelink/.env)
# - backend と frontend の両方で参照される
# - docker compose は実行ディレクトリの .env を interpolation 用に自動ロード
# - 各 service の env_file: - .env で container 内にも同じ値が注入される
# - chmod 600 必須
# =====================================================================

# --- App (backend) ---
APP_ENV=production
APP_NAME=CareLink Backend
LOG_LEVEL=INFO
API_V1_PREFIX=/api/v1
CORS_ORIGINS=https://carelink.kaipoke-api.net

# --- Database (compose 内: postgres service name / host から: localhost:5432 だが host port は非公開) ---
DATABASE_URL=postgresql+asyncpg://carelink:__REPLACE_WITH_STRONG_PASSWORD__@postgres:5432/carelink
DATABASE_ECHO=false
POSTGRES_USER=carelink
POSTGRES_PASSWORD=__REPLACE_WITH_STRONG_PASSWORD__
POSTGRES_DB=carelink

# --- JWT (32 文字以上必須。production で config.py の field_validator が発火) ---
JWT_SECRET=__REPLACE_WITH_OPENSSL_RAND_BASE64_32__
JWT_ALGORITHM=HS256
JWT_ACCESS_TTL_SECONDS=3600
JWT_REFRESH_TTL_SECONDS=2592000

# --- External APIs ---
GOOGLE_MAPS_API_KEY=__SET_IF_USED__
GEMINI_API_KEY=__SET_IF_USED__
# Gemini model id (Google が定期的に旧モデルを 404 にするため pin しておく)
GEMINI_MODEL=gemini-2.0-flash

# --- Integrations forward target (D4 worker, Phase 2) ---
INTEGRATION_BASE_URL=http://integrations:8001

# --- Kaipoke API relay (Wave 4-A) ---
# 既存 Flask + Playwright service。本番では同一 VPS 上の kaipoke-api コンテナと
# external network `playwrighttest1_default` 経由で通信する。
KAIPOKE_API_BASE_URL=https://kaipoke-api.net
KAIPOKE_API_TOKEN=__SET_IF_USED__
KAIPOKE_EXPORT_DIR=/tmp/carelink/exports
KAIPOKE_EXPORT_TTL_SECONDS=1800

# --- Visit photo storage (Wave 4-D) ---
VISIT_PHOTOS_DIR=/opt/carelink/data/visit_photos

# --- NextAuth (frontend) ---
NEXTAUTH_URL=https://carelink.kaipoke-api.net
NEXTAUTH_SECRET=__REPLACE_WITH_OPENSSL_RAND_BASE64_32__

# --- Backend API base URL (frontend → backend) ---
# container 間通信: http://backend:8000 (compose service name)
# Tunnel/外部経由: https://carelink.kaipoke-api.net (path rewrite を経由)
BACKEND_API_BASE_URL=http://backend:8000
```

## 危険な値の警告

- `JWT_SECRET=please-change-me-...` (dev デフォルト) を本番で使うと `app/core/config.py` の `_validate_jwt_secret` が `ValueError` を投げ、**起動時に backend が落ちる**。これは意図的な fail-fast。
- `POSTGRES_PASSWORD` を `carelink` (dev 同値) のまま使うと、誤って 5432 が外部に露出した場合に即侵害される。loopback bind があってもパスワードは強くする (本構成では host port を非公開にしているが多重防御)。
- `APP_ENV=production` を忘れると JWT バリデーションが効かず、弱秘密のまま運用されてしまう。
- `CORS_ORIGINS` に `*` や `http://...` (非 HTTPS) を入れない。
- `.env` のパーミッションは `chmod 600` 必須。`ls -l /opt/carelink/.env` で `-rw-------` を確認。

## `backend/.env.example` との整合 (Wave 5-A)

本テンプレートは `backend/.env.example` + `frontend/.env.example` を統合した
スーパーセット。新規 env 変数を増やす際は **3 ファイル全てを同時に更新する**:

1. `backend/.env.example` — backend dev 環境用デフォルト
2. `frontend/.env.example` — frontend dev 環境用デフォルト (存在する場合)
3. `docs/deployment/env-template.md` (本ファイル) — 本番統合 `.env`

整合チェック (CI 化未実装、手動コマンド):

```bash
diff <(grep -E '^[A-Z_]+=' backend/.env.example | cut -d= -f1 | sort) \
     <(grep -E '^[A-Z_]+=' docs/deployment/env-template.md | cut -d= -f1 | sort) \
  | grep -E '^[<>]' || echo "OK: keys match"
```

`<` で出るキーは template 側が漏れている (本番 `.env` で参照されないので backend が起動失敗の可能性)、
`>` で出るキーは backend dev 側に存在しない frontend / 運用専用キー (POSTGRES_*, NEXTAUTH_* 等) で許容。
