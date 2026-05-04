# `.env` テンプレート (本番)

VPS 上の `/opt/carelink/backend/.env` と `/opt/carelink/frontend/.env` の中身。
**実 secret 値は Git に絶対に commit しない**。本ドキュメントはプレースホルダのみ。

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

## `/opt/carelink/backend/.env`

```ini
# --- App ---
APP_ENV=production
APP_NAME=CareLink Backend
LOG_LEVEL=INFO
API_V1_PREFIX=/api/v1
CORS_ORIGINS=https://carelink.kaipoke-api.net

# --- Database ---
# compose では postgres service name を指す。host から実行する場合は localhost:5432。
DATABASE_URL=postgresql+asyncpg://carelink:__REPLACE_WITH_STRONG_PASSWORD__@postgres:5432/carelink
DATABASE_ECHO=false
POSTGRES_USER=carelink
POSTGRES_PASSWORD=__REPLACE_WITH_STRONG_PASSWORD__
POSTGRES_DB=carelink

# --- JWT ---
# 32 文字以上必須 (config.py の field_validator が production で発火)
JWT_SECRET=__REPLACE_WITH_OPENSSL_RAND_BASE64_32__
JWT_ALGORITHM=HS256
JWT_ACCESS_TTL_SECONDS=3600
JWT_REFRESH_TTL_SECONDS=2592000

# --- External APIs ---
GOOGLE_MAPS_API_KEY=__SET_IF_USED__
GEMINI_API_KEY=__SET_IF_USED__

# --- Integrations forward target (D4 worker, Phase 2) ---
INTEGRATION_BASE_URL=http://integrations:8001
```

## `/opt/carelink/frontend/.env` (Phase 2)

```ini
# NextAuth
NEXTAUTH_URL=https://carelink.kaipoke-api.net
NEXTAUTH_SECRET=__REPLACE_WITH_OPENSSL_RAND_BASE64_32__

# Backend API (loopback 経由で同 VPS 上の backend へ)
BACKEND_API_BASE_URL=http://localhost:18001/api/v1
```

## 危険な値の警告

- `JWT_SECRET=please-change-me-...` (dev デフォルト) を本番で使うと `app/core/config.py` の `_validate_jwt_secret` が `ValueError` を投げ、**起動時に backend が落ちる**。これは意図的な fail-fast。
- `POSTGRES_PASSWORD` を `carelink` (dev 同値) のまま使うと、誤って 5432 が外部に露出した場合に即侵害される。loopback bind があってもパスワードは強くする。
- `APP_ENV=production` を忘れると JWT バリデーションが効かず、弱秘密のまま運用されてしまう。
- `CORS_ORIGINS` に `*` や `http://...` (非 HTTPS) を入れない。
- `.env` のパーミッションは `chmod 600` 必須。`ls -l backend/.env` で `-rw-------` を確認。
