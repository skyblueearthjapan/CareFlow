# Phase 2 Codex Code Review — `e1c886a` + `f3eb9e0`

**Reviewer**: OpenAI Codex CLI v0.128.0 (gpt-5.5)
**Branch**: `develop`
**Date**: 2026-05-05
**Scope**: frontend Phase 2 (TanStack Query + Zustand + API client + 4 screens) + backend Phase 2 (5 domain routers + schemas + tests + RBAC)

> 注: pytest / typecheck は OS のシェル policy で拒否されたため、本レビューは静的解析 + ソース読込のみ。

## VERDICT: REVISE

## Concise Summary

Backend router shape is mostly coherent: routers are registered, `from_attributes=True` is present, and GET paths apply soft-delete filters. The blockers are around **auth/session integration and test depth**. Frontend protected queries can run without a token, cache keys are not scoped by user/role, `401` refresh is not implemented, and the existing NextAuth login parser appears **incompatible with the backend login response**.

## Major Findings — Frontend

### 1. Login/session contract is broken

- 場所: `backend/app/api/v1/auth.py:89` vs `frontend/lib/auth.ts:12`
- Backend returns `{ user: { id, email, role, ... }, tokens: { access_token, refresh_token } }`
- Frontend expects flat `{ id, email, role, accessToken }`
- **影響**: Phase 2 screens depend on `session.accessToken`, so protected API calls may always be anonymous (login appears to succeed but no token is forwarded to API client)
- Severity: **HIGH** — first-pass deployment blocker

### 2. No 401 refresh-token path

- 場所: `frontend/lib/api/fetcher.ts:35` + `frontend/lib/api/client.ts:41`
- Both clients only attach bearer tokens and throw on error. NextAuth stores no refresh token in `frontend/lib/auth.ts:62`, so access-token expiry becomes a hard logout/error state.
- **Fix**: persist `refresh_token` in JWT callback, intercept 401 in API client, call `/api/v1/auth/refresh`, retry once
- Severity: **HIGH** — session brittleness in production

### 3. Protected useQuery not gated, unscoped cache keys

- 場所: `frontend/app/(app)/patients/page.tsx:30`, `staff/page.tsx:30`, `schedule/page.tsx:32`
- `useQuery({ queryKey: ['patients'] })` runs even when session is loading or unauthenticated
- Cache keys don't include `user.id` or `role` → role switch (logout/login as different role) shows stale data until manual refetch
- **Fix**: `enabled: status === 'authenticated'`, key as `['patients', user.id]` (or invalidate on `signOut`)
- Severity: **HIGH** — security implication: role transition shows previous user's data

### 4. Schedule UI shape mismatch with backend

- 場所: `frontend/app/(app)/schedule/page.tsx:11` vs `backend/app/schemas/visit.py:11`
- UI expects `{ date, patientName, staffName }`, backend returns `{ visit_date, patient_id, staff_id }`
- **Fix**: either denormalize on backend (join to get names) or rename UI fields to match backend
- Severity: **MEDIUM** — schedule screen will render empty/undefined cells

## Major Findings — Backend

### 5. Delete is admin-only across all routers

- 場所: `backend/app/api/v1/patients.py:95`, `staff.py:103`, `visits.py:115`, `offices.py`, `cities.py`
- All `DELETE` requires admin role. If "admin/manager can mutate" includes delete, this is too restrictive.
- **Fix**: clarify plan intent. If admin-only is correct, document as a plan exception in code/comments.
- Severity: **MEDIUM** — RBAC mismatch with stated plan

### 6. Create/update endpoints don't validate FK or catch IntegrityError

- 場所: All `POST` and `PATCH` handlers
- Invalid `patient_id`, staff IDs, duplicate patient codes, etc. become DB errors instead of stable `404/409/422` API responses
- **Fix**: explicit FK existence checks before insert; wrap commit in try/except IntegrityError → 409 Conflict / 422 Unprocessable
- Severity: **MEDIUM** — bad UX, exposes DB internals

### 7. `require_role()` uses `CurrentUser`, not `CurrentActiveUser`

- 場所: `backend/app/core/deps.py:116`
- Today `User` has no `deleted_at`, but once it lands, many role-protected endpoints will bypass the active-user dependency unless this is refactored.
- **Fix**: chain `require_role` after `get_current_active_user`
- Severity: **LOW** — future-proof concern

## Minor Findings

- Zustand persistence is fine: `lib/stores/ui.ts:25` persists only UI preferences, not role-sensitive data.
- `Content-Type: application/json` is set on every GET in both clients, which can cause unnecessary CORS preflights.
- `frontend/lib/api/types.ts` is `Record<string, unknown>`, so the "typed" OpenAPI client currently gives little contract protection.

## Security/RBAC Review

Staff scoping is mostly correct:
- Staff list/detail restrict staff users to `user.staff_id` in `staff.py:33, 53`
- Visit list/detail scope staff users to primary/secondary/mentor assignments in `visits.py:41, 66`
- Admin bypass uses **DB-loaded `User.role`**, not JWT role claims (good — JWT role can't be forged for new privileges)
- Detail visit authorization fetches the row **before** applying staff visibility → response is still `404`, but SQL-level scoping would reduce timing/existence side channels

## N+1 Query Risks

Current schemas serialize scalar fields only, so no immediate relationship N+1. The frontend already wants visit patient/staff names, and adding those naively would create N+1 risk; use explicit joins or `selectinload` when expanding `VisitRead`.

## TypeScript Strict Violations

Likely strict errors in `frontend/components/ui/alert.tsx:42`:
- `AlertTitle` forwards `HTMLParagraphElement` ref to an `h5` → wrong type
- `AlertDescription` forwards `HTMLParagraphElement` ref to a `div` at line 54 → wrong type
- **Fix**: Use `HTMLHeadingElement` and `HTMLDivElement` respectively

## Missing Test Cases

- Staff can only list/get own staff record, including non-owner `404`
- Staff can only list/get assigned visits across primary/secondary/mentor
- Soft-deleted rows disappear from list/detail for every router
- Manager create/patch/delete expectations
- FK/unique constraint failures
- Frontend auth response parsing, `401` refresh, query gating/cache clearing

## Production Deployment Risk

**High** until auth is fixed. A production user may log in successfully at the backend but fail frontend session parsing, then all Phase 2 screens call protected APIs without a bearer token. Missing refresh handling also makes sessions brittle under normal token expiry.

## Open Questions

- Is soft delete intentionally admin-only, or should managers delete per "mutate"?
- Should staff see all patients/offices/cities, or only office/assignment-scoped records?
- Should `/visits` return denormalized display names for the schedule screen, or should the frontend join client-side?
