# Deployment + CI Codex Review — `9cc9791..ea410c1`

**Reviewer**: OpenAI Codex CLI v0.128.0 (gpt-5.5)
**Branch**: `develop`
**Date**: 2026-05-05
**Scope**: docs/deployment/* + frontend/Dockerfile + frontend/next.config.js + .github/workflows/*

## VERDICT: REVISE

## Concise Summary

Do not deploy these commits as-is. The docs are directionally useful, and `create_admin.py` uses the existing bcrypt path correctly, but the production Compose/runbook path is internally inconsistent and likely fails before the app starts. Cloudflared merge guidance is also unsafe enough to risk existing `kaipoke-api` ingress.

## Major Findings

### 1. Compose env vars not loaded — POSTGRES_PASSWORD / NEXTAUTH_SECRET unset

- 場所: `docs/deployment/docker-compose.production.yml:23,48,77-79` + `docs/deployment/runbook.md:38-45`
- Compose interpolates from the **shell/root `.env`**, not `backend/.env` or `frontend/.env`. `POSTGRES_PASSWORD`, `NEXTAUTH_URL`, and `NEXTAUTH_SECRET` will be unset despite the runbook creating only service-local env files.
- **Fix**: add `/opt/carelink/.env` to the runbook or use `env_file` plus explicit non-interpolated service env. Prefer one root production `.env` consumed by Compose.
- Severity: **HIGH** — first deploy will fail with "variable is not set" warnings + empty secrets

### 2. Build context paths inconsistent

- 場所: `docs/deployment/docker-compose.production.yml:39,68` + `docs/deployment/runbook.md:51,73`
- No invocation makes both build contexts correct. After copying the file to repo root, `../../frontend` is wrong; when using `-f docs/deployment/...`, `./backend` is wrong.
- **Fix**: choose one mode. If copying to root, set frontend context to `./frontend` and remove the later `-f docs/deployment/...` commands.
- Severity: **HIGH** — `docker compose build` fails

### 3. Cloudflared fragment is a full ingress block, not list items

- 場所: `docs/deployment/cloudflared-config-fragment.yml:17,29-37`
- The fragment includes a full top-level `ingress:` block, an active `api.carelink...` rule despite "only enable" wording, and a placeholder existing `kaipoke-api.net -> localhost:80` rule. Pasting this can **replace or corrupt existing routes**.
- **Fix**: provide only list items to insert, keep API rule commented out, and document "preserve all existing ingress entries and catch-all." Add post-change validation for both `https://kaipoke-api.net` and `https://carelink...`.
- Severity: **HIGH** — could break existing kaipoke-api production traffic

### 4. healthz routes to frontend, no /api rewrite to backend

- 場所: `docs/deployment/runbook.md:103` + `cloudflared-config-fragment.yml:20-21`
- Public health check calls `https://carelink.../api/v1/healthz`, but that hostname routes to **frontend**, and `frontend/next.config.js:4` only sets standalone output, **no API rewrite**.
- **Fix**: add a Cloudflared path rule or Next rewrite for `/api/v1/*` to backend, or change validation to `api.carelink...` only after explicitly enabling that hostname.
- Severity: **HIGH** — Phase H verification cannot succeed

### 5. Postgres 127.0.0.1:5432 likely collides with kaipoke-api

- 場所: `docs/deployment/docker-compose.production.yml:29`
- Binding Postgres to `127.0.0.1:5432` is a likely collision with existing `kaipoke-api` Postgres on the same VPS.
- **Fix**: remove the host port unless host-side psql is required, or use a nonstandard loopback port like `15432:5432`.
- Severity: **MEDIUM** — `docker compose up -d postgres` may fail with EADDRINUSE

## Minor Findings

- `docs/deployment/preflight-check.sh:18`: `df -BG` rounds up, so slightly under 5 GiB can pass. Use KB/MB arithmetic.
- `preflight-check.sh:36-38`: Docker version is printed but not checked against the documented `24.x` minimum.
- `preflight-check.sh:67-72`: UFW active is checked, but "only 22/tcp public" is not verified.
- `preflight-check.sh:89-94`: existing `/opt/carelink` always fails, blocking redeploys.
- `backend/scripts/create_admin.py:55-77`: implementation is select-then-update, not the documented `ON CONFLICT`; acceptable for manual bootstrap, but docs should not claim atomic upsert.
- `backend/scripts/create_admin.py:31` vs `initial-admin-seed.md:22,29`: script minimum is 8 chars while docs/prompt say 12.

## Deployment Risk

**High**. Phase ordering is mostly right: DB before migration, migration before app/frontend. But the runbook switches Compose file locations midstream, env loading is wrong, and frontend/API routing validation does not match the tunnel config. These are first-deploy blockers, not edge cases.

## CI/Security Risk

CI does not build either production Docker image or run Compose validation, so the current blockers would pass. Path filters also skip deployment docs for backend/frontend CI. `backend-ci.yml:92,157` should **not use a secret for test JWTs**; PR code can exfiltrate same-repo secrets. Use a hard-coded CI-only dummy value. Security workflow may also fail SARIF upload on fork PRs depending token permissions.

## Open Questions

- Does the existing Cloudflared config contain wildcard `*.kaipoke-api.net` routes?
- Is existing `kaipoke-api` already binding host `5432`, `18000`, or `18001`?
- Should CareLink expose backend through same-host `/api/v1/*` or a separate `api.carelink...` hostname?
