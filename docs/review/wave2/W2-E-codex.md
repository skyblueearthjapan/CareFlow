# W2-E PWA — Codex Final Review

**Reviewer**: OpenAI Codex CLI v0.128.0 (gpt-5.5)
**Date**: 2026-05-05
**Scope**: Post-revise (Wave 2) final QA review

---

## VERDICT

**Conditional pass, not a clean production pass.** The critic items are mostly addressed, but the BUILD_ID/GIT_SHA story is not actually wired. This is acceptable only if every release manually bumps `CACHE_VERSION`.

## Summary

`/api/*` is now network-only (`frontend/public/sw.js:64`), so `/api/v1/auth/*` and other backend JSON are no longer runtime-cached. `offline.html` is really precached during `install` via `cache.addAll([OFFLINE_URL])` (`sw.js:19-24`), and navigation fallback uses it only after network/cache miss (`sw.js:111-117`). Non-GET requests return before `respondWith` (`sw.js:56`), so POST/PATCH form/API flows are not broken by the navigation catch path.

The icon generator centers the maskable glyph and shrinks it to 40% of canvas (`frontend/scripts/gen-icons.py:35-48`), which should keep it inside the Android maskable safe zone. `theme_color` and viewport theme color are lowercase, and I only found one SW registration path (`frontend/app/layout.tsx:26,34`).

## Residual Issues

1. **Build/version automation is still not solved.** `sw.js` still hard-codes `const CACHE_VERSION = 'careflow-v2'` and explicitly says to bump it manually (`frontend/public/sw.js:11-13`). `generateBuildId` exists (`frontend/next.config.js:8-10`), but public `sw.js` is not templated or injected from that value. Also, `GIT_SHA` is not set in `frontend-ci` (`.github/workflows/frontend-ci.yml:73-74`), not passed as a Docker build arg (`frontend/Dockerfile:17-18`), and not present in production compose env (`docs/deployment/docker-compose.production.yml:82-101`). Result: SW updates correctly **when `CACHE_VERSION` changes**, but not reliably per deploy.

2. **Runtime cache cap is best-effort, not strict under concurrency.** `cache.put(req, res.clone())` is not awaited before `trimCache(...)` starts (`frontend/public/sw.js:104-108`). Under concurrent successful GETs, trims can inspect stale key sets and the cache can exceed 50 until later traffic. Also, this is FIFO, not LRU, which is fine if intended but should not be described as true LRU behavior.

3. **No automated SW coverage found.** I found no test asserting `/api/*` bypass, offline fallback, cache trim, or cache-version activation behavior. This remains manual-review logic.

## Security: Cache Pollution

The `/api/*` fix removes the largest PII leak, but cache pollution is not fully closed. The default branch caches **any same-origin successful GET** (`frontend/public/sw.js:99-108`). That can include authenticated app HTML, App Router/RSC payloads, prefetch/data responses, or other cookie-scoped pages outside `/api/*`. If those contain patient/staff data, they can persist in Cache Storage after logout.

For healthcare-style data, the safer pattern is an allowlist: cache `/_next/static/*`, icons, manifest, and offline shell only; do not runtime-cache arbitrary same-origin HTML/RSC unless the response is explicitly public and cacheable. At minimum, bypass requests with credentials/auth-sensitive headers and respect `Cache-Control: no-store/private`.

## What Is Still Missing

Automated cache version injection from commit SHA; Docker/CI passing `GIT_SHA`; a strict or serialized trim if the 50-entry limit matters; SW unit/browser tests; explicit handling for App Router data/RSC requests; `worker-src` CSP; and navigation preload/scope decisions. I would not call this PWA layer production-hardened until those are closed.
