# W4-F. AuditLog middleware + /admin/users CRUD

**実装 commit**: `0f67139` (2026-05-05)
**ドメイン**: D1 (Backend) / Phase 3

## 概要

監査要件 (誰が・いつ・どの API に対し・何を変更したか) を満たすため、
ASGI middleware で全ミューテーション系リクエストを 1 行ずつ
audit_logs テーブルに書き込むフックを追加する。併せて、これまで
スタブだった `/api/v1/admin/users` を完全な CRUD として実装し、admin が
UI から user 追加 / 役割変更 / soft-delete を完結できる状態にする。

## 実装範囲

- **middleware `app/middleware/audit.py` (AuditLogMiddleware)**:
  - 対象: `settings.api_v1_prefix` 配下の POST/PUT/PATCH/DELETE のみ
  - 除外: GET/HEAD/OPTIONS, `/healthz`, `/auth/login` (login 成功は
    `auth.py` 内で明示的に書込)
  - 取得情報: `actor_user_id`, `method`, `path`, `query_string`,
    `status_code`, `latency_ms`, `request_body` (上限 8KB、PII redact 後)
- **`/admin/users` CRUD**:
  - `POST` (新規作成、temp password 自動発行 + must_change_password=true)
  - `GET` (一覧 + `role=` / `q=` filter + paginate)
  - `GET /{id}`, `PATCH /{id}` (role 変更 / メール変更 / lock 解除)
  - `DELETE /{id}` (soft-delete: deleted_at 設定)
- **`User` model 追加列**: `deleted_at`, `must_change_password`
- **`/api/v1/audit-logs` 読み取り API**: admin only、時間範囲 / actor /
  role / path / method / status_code フィルタ + standard pagination

## 関連 commit

- `0f67139` feat(W4-F): 本体
- `d526b59` fix(alembic): W4-D / W4-F の並列 head を 0008 merge revision
  で統合
- `0a6db00` feat(W4-G): audit middleware に
  `sqlalchemy.exc.ProgrammingError` 専用ハンドリング (alembic 未適用時に
  login が 500 を巻き込んだ件への学習)

## テスト被覆

- `test_admin_users.py`: 18 ケース (CRUD + RBAC + duplicate + filter +
  patch role + soft delete)
- `test_audit_log_middleware.py`: 全 method の書込 + redact + skip
- `test_audit_middleware_resilience.py`: DB 障害 / large body / 例外耐性

## 残課題 / 次 Wave 移譲

- 閲覧監査 (GET も記録) は医療情報要件次第で Wave 6 検討
- audit_logs の TTL / archive は運用 1 年経過後に判断 (現状無制限)
- `/audit-logs` UI は frontend 側で admin 画面追加 (本タスク現状は API
  のみ、UI は別 sprint)
