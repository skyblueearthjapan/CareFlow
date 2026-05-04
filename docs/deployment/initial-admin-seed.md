# 初期管理者 user 投入手順

`backend/scripts/create_admin.py` で users テーブルに `role=admin` の最初の 1 人を作る。
NextAuth/JWT のフローはこの user で初回ログインしてから、UI 経由で他 user を発行する想定。

## 前提

- Phase E (Alembic) が完了し `users` テーブルが存在する
- backend コンテナが起動している (`docker compose ps backend` が healthy)

## 実行 (Phase I)

```bash
cd /opt/carelink
docker compose exec backend python scripts/create_admin.py
```

対話プロンプト:

```
admin email: ops@example.com
admin password (min 12 chars): ****************
confirm password: ****************
```

入力規約:

- email: RFC 5322 にラフ準拠 (アプリの auth ロジックは厳格に検証しないが、スクリプト側で `@` を最低限チェック)
- password: 12 文字以上必須 (スクリプト側で 12 文字未満を拒否)
- 既存 admin email が同一だった場合は **password / role / lockout フィールドを上書き更新**。
  実装は SQL `INSERT ... ON CONFLICT` ではなく **select-then-update upsert** (Python 側で
  `SELECT WHERE email=...` → 存在すれば UPDATE / 無ければ INSERT)。これは「忘れ secret を
  上書きする」rescue 用途も兼ねる。

## 成功確認

```bash
docker compose exec postgres psql -U carelink -d carelink -c \
  "SELECT email, role, locked_until, created_at FROM users WHERE role='admin';"
```

`locked_until` が NULL、`role='admin'` を確認できれば OK。

## ログインテスト

```bash
curl -fsS -X POST https://carelink.kaipoke-api.net/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"ops@example.com","password":"<the-password>"}'
```

`access_token` が JSON で返れば成功。401 が返る場合は password mismatch か `failed_login_count` lockout (現状の RBAC 実装上、5 回失敗で 15 分ロック想定)。

## トラブルシュート

- **`relation "users" does not exist`**: Alembic が走っていない。Phase E をやり直す。
- **`duplicate key value violates unique constraint "users_email_key"`**: select-then-update のセッションがまれにレースした可能性。再実行する。継続発生する場合は `scripts/create_admin.py` の最新版 (`upsert_admin` が select 後に UPDATE するパス) を確認。
- **bcrypt が遅い (>3 sec)**: コンテナ ARM/x86 ミスマッチ。`docker compose -f docs/deployment/docker-compose.production.yml build --no-cache backend` で再ビルド。
