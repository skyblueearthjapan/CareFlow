# G2 Codex Code Review — `59b542e`

**Reviewer**: OpenAI Codex CLI v0.128.0 (gpt-5.5)
**Branch**: `develop`
**Scope**: `backend/`
**Date**: 2026-05-05

> 注: pytest と SQLAlchemy 検証コマンドは OS のシェル policy で拒否されたため、本レビューは静的解析 + ソース読込のみ。

## VERDICT: REVISE

## Concise Summary

Good skeleton shape, but not merge-ready against `D1-backend-plan.md`. The highest-risk issue is that Alembic is configured to use `psycopg` while the backend only installs `asyncpg`, so clean DB migration is likely broken. The implementation also stops at auth/health despite the D1 plan listing domain routers, RBAC enforcement, schemas, audit/error/logging, and integration-forward contracts.

## Major Findings

### 1. Alembic uses psycopg but only asyncpg is installed

- 場所: `backend/alembic/env.py:33` + `backend/requirements.txt:6-11`
- Alembic rewrites `postgresql+asyncpg://` to `postgresql+psycopg://`, but `psycopg` is not installed. `make migrate`/container migration will **fail before DDL**.
- **Fix**: add `psycopg[binary]` to requirements/pyproject, or implement Alembic's async engine path using `asyncpg`.
- Severity: **HIGH** — blocks clean DB initialization

### 2. Only health and auth routers are mounted

- 場所: `backend/app/api/v1/__init__.py:5-9`
- Planned patients/staff/offices/cities/visits/allocate/geocode/ai/dashboard/integration APIs are absent.
- **Fix**: either narrow the commit claim to "infra/auth only" or add the D1 routers/contracts in the next commit.
- Severity: **MEDIUM** — scope mislabeling, blocks D2/D3 integration

### 3. `require_role()` exists but is not enforced

- 場所: `backend/app/core/deps.py:95-106`
- `require_role()` is defined but **no endpoint uses it**, and there is no staff-token-on-admin-route 403 test.
- **Fix**: apply RBAC dependencies to admin/staff routes and add admin/staff/anonymous matrix tests.
- Severity: **HIGH** — security feature implemented but inactive

### 4. Lockout instead of rate-limit creates DoS + email-enumeration risk

- 場所: `backend/app/api/v1/auth.py:30-34, 60-74`
- T17 asked for 5/15min rate limit with 6th request returning `429`. Implementation is **per-account lockout returning `423`**. This enables **account lockout DoS** (attacker locks out victims) and can **reveal valid emails** after repeated attempts (different status codes).
- **Fix**: IP+email rate limiting, preferably Redis-backed, atomic updates, generic responses, expected `429`.
- Severity: **HIGH** — security regression vs plan, exploitable

### 5. Index drift: `ix_offices_active` in model but not in migration

- 場所: `backend/app/models/office.py:35-38` vs `backend/alembic/versions/0001_initial.py:81`
- Model defines `ix_offices_active`, migration only creates `ix_offices_name`.
- **Fix**: add the index to migration or drop from model. Confirms the drift identified by critic M2.
- Severity: **MEDIUM** — silent divergence between ORM and migrations

## Minor Findings

- `backend/app/core/deps.py:20-22` uses `OAuth2PasswordBearer`, but `/auth/login` takes JSON, not OAuth2 password form. Use `HTTPBearer` or implement form login for OpenAPI compatibility.
- `backend/app/api/v1/health.py:17,23` are mounted under `/api/v1`; plan lists root `/healthz` and `/readyz`. Align paths before DevOps consumes them.
- Role/status/type values are plain strings (`backend/app/models/user.py:31`, etc.) despite plan enums. Add DB `CHECK` constraints or SQLAlchemy enums.

## Plan-vs-Implementation Drift

**Implemented**: FastAPI app, settings, async SQLAlchemy engine, 16-table initial migration, auth JWT/bcrypt, basic health, Docker files, minimal tests.

**Missing or materially incomplete**:
- `KaipokeSync` plan item
- entity CRUD schemas
- all domain APIs
- integration forwarder
- geocode/AI endpoints
- unified error format
- request_id/JSON logging
- audit-log writes
- OpenAPI artifact contract
- RBAC enforcement
- 80% coverage target

## Security Review

JWT is signed with `python-jose` (`backend/app/core/security.py:9`), but tests do not cover:
- expired
- tampered
- wrong-type
- missing-claim
- PyJWT/jose exception-style differences

`backend/app/core/config.py:39` ships a default secret and has no production guard; **fail startup when `APP_ENV=production` and the secret is default/short**.

CSRF risk is currently limited because auth is Bearer-token based, not cookie-auth based, but `allow_credentials=True` in CORS (`backend/app/main.py:45-48`) should be locked to explicit production origins and revisited before any cookie/session auth.

## Test Coverage Gaps

Tests cover only happy-path auth, basic lockout, `/me`, and health. They bypass Alembic with `Base.metadata.create_all` on SQLite (`backend/tests/conftest.py:46-48`), so:
- PostgreSQL DDL
- JSONB/UUID behavior
- partial indexes
- downgrade
- psycopg migration failures

are not covered. Add a Postgres migration smoke test and RBAC/security JWT tests.

## Production Deployment Risk

`/readyz` only runs `SELECT 1` (`backend/app/api/v1/health.py:27`), so an empty or unmigrated DB can report ready. Docker starts uvicorn directly (`backend/Dockerfile:51`) and **does not run migrations**. Add a migration job/entrypoint policy and make readiness verify Alembic head or required tables.
