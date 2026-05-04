# G3 Codex Code Review — `5ea86e4`

**Reviewer**: OpenAI Codex CLI v0.128.0 (gpt-5.5)
**Branch**: `develop`
**Scope**: `frontend/`
**Date**: 2026-05-05

> 注: typecheck/lint は frontend/node_modules と lockfile 不在のため実行できず、本レビューは静的解析 + ソース読込のみ。

## VERDICT: **BLOCK as D2 foundation**

This is a visual/auth skeleton, not the planned frontend foundation.

## Concise summary

Reviewed commit `5ea86e4` under `frontend/` against `docs/plans/D2-frontend-foundation-plan.md`. Core D2 acceptance items are missing: real auth, mobile routes, PWA, Query/Zustand providers, generated API client, tests, standalone output, and several planned shared components.

## Major Findings

### 1. Login does not actually sign in — middleware loop guaranteed

- 場所: `frontend/app/(auth)/login/page.tsx:19-21`
- Login never calls NextAuth `signIn`; it sleeps then `window.location.href = '/dashboard'`, so **no session cookie is created** and middleware redirects back to `/login`.
- **Fix**: call `signIn('credentials', { email, password, redirect: false, callbackUrl })`, then `router.replace`.
- Severity: **CRITICAL** — login is non-functional in deployed state

### 2. authorize() is fake auth that accepts any non-empty creds

- 場所: `frontend/lib/auth.ts:29-39`
- `authorize()` accepts any non-empty email/password and returns a placeholder bearer token. **This is not an auth integration.**
- **Fix**: POST to backend `/auth/login`, validate response with zod, return `null` on 401, and persist the real access token.
- Severity: **CRITICAL** — security illusion, must be fixed before any deployment

### 3. MobileShell points to /m/* but those routes don't exist

- 場所: `frontend/components/MobileShell.tsx:8-12`
- Mobile nav links to `/m/home`, `/m/today`, `/m/this-week`, `/m/mypage`, but no such routes exist. Plan expects `(mobile)/home|today|this-week|mypage`.
- **Fix**: create `app/(mobile)/layout.tsx` plus those pages, and align hrefs.
- Severity: **HIGH** — mobile experience completely broken

### 4. Tailwind class `bg-bg-window` is unregistered

- 場所: `frontend/components/AppShell.tsx:15` + `frontend/tailwind.config.ts:20-33`
- `bg-bg-window` is used but `bg-window` is not registered in Tailwind, so the outer window color will not apply.
- **Fix**: add `'bg-window': 'var(--bg-window)'`; also expose/use `shadow-outer-card`.
- Severity: **MEDIUM** — visual breakage

### 5. API client is untyped fetch, not `openapi-fetch`

- 場所: `frontend/lib/api-client.ts:23-34`
- API client is untyped fetch (not `openapi-fetch`), bearer injection is manual, and **spreading `HeadersInit` drops `Headers` instances** (silent header loss).
- **Fix**: generate OpenAPI types, use `new Headers(headers)`, and inject token from `auth()`/session provider.
- Severity: **HIGH** — type safety lost, header bug latent

## Minor Findings

- `frontend/package.json:5-11` lacks `packageManager`, lockfile, test script, `gen:api`, and pnpm alignment.
- `frontend/next.config.js:2-8` misses `output: 'standalone'`, required by the plan.
- `frontend/app/layout.tsx:2-5` loads Inter only; plan requires Noto Sans JP, Noto Serif JP, and JetBrains Mono via CSS variables.
- `frontend/.eslintrc.json:1-12` uses legacy config with ESLint 9; verify this actually runs under CI.
- Missing `not-found.tsx`, `error.tsx`, `loading.tsx`, `components.json`, toast, tooltip, skeleton, avatar, empty state, VisitChip.

## Plan-vs-implementation drift

- **mobile route group missing**: only unused `MobileShell.tsx` exists
- **PWA missing**: no `manifest.webmanifest`, service worker, icons, or offline fallback
- **TanStack Query missing**: no dependency, no `QueryClientProvider`, no devtools
- **Zustand missing**: no store for `sidebarCollapsed`, `density`, or `aiInputOpen`
- **Planned routes drift**: plan lists `weekly/master/integration`; implementation has `patients/staff/schedule`

## TypeScript strict violations

I could not run `typecheck` because `frontend/node_modules` and a lockfile are absent. Strict-risk items remain:
- ad-hoc casts in `frontend/lib/auth.ts:47-57`
- hand-written middleware request typing in `frontend/middleware.ts:8`
- unsafe `HeadersInit` handling in `frontend/lib/api-client.ts:28-31`

## NextAuth v5 type-merging issues

`frontend/types/next-auth.d.ts:3` defines `AppRole`, while `frontend/lib/auth.ts:5` defines another copy. The callbacks then cast `user`/`token` instead of trusting merged types.

**Fix**: centralize `AppRole`, augment `User`, `Session`, and `JWT` once, and let callback params infer.

## Accessibility risks

- Sidebar collapsed links rely on `title` only and lack `aria-current`
- Mobile nav uses color-only active state
- Login errors lack `role="alert"`/`aria-live`
- FAB has no visible focus styling beyond the global outline

## Production risk

**High**. The app can render shells, but:
- auth is fake
- protected navigation is unusable after login (middleware loop)
- planned mobile/PWA/state/query/API foundations are absent
- build/lint/test acceptance is unproven
