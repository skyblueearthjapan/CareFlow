# W2-B 連携センター — Codex Final Review

**Reviewer**: OpenAI Codex CLI v0.128.0 (gpt-5.5)
**Date**: 2026-05-05
**Scope**: Post-revise (Wave 2) final QA review

---

**VERDICT**

ACCEPT WITH RESERVATIONS. The revise fixes the original blocking shape issues, but it is not a clean "done" for environments where `0003` may already have run, and `address_hash` is still only a helper, not an enforced write-path invariant.

**Summary**

Pagination is now consistently applied on the three list endpoints: jobs, geocoding cache, and AI logs all return `Paginated[...]` with `items/total/limit/offset` in [integrations.py:59](/C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/backend/app/api/v1/integrations.py:59), `:212`, `:248`; frontend hooks consume `Paginated<T>` and components read `data.items`.

The tab dedup/Suspense revise looks materially fixed: shared list components replace page imports, admin-only tabs are conditionally mounted, and `useSearchParams` is inside `Suspense`.

**Residual Issues**

`with_for_update` was not implemented. The code uses a conditional single-row `UPDATE` at [integrations.py:168](/C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/backend/app/api/v1/integrations.py:168). That is acceptable and lower-deadlock than SELECT-lock-update for this endpoint, since it takes one row lock in one statement. Caveat: future workers must also use conditional state transitions. A blind worker update can still overwrite `cancelled -> completed`.

`address_hash` is standalone. [hash.py:19](/C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/backend/app/services/geocoding/hash.py:19) has NFKC + whitespace normalization and SHA-256, with tests, but `rg` only finds it in tests/module exports/model/schema. No `GeocodingCache` producer calls it yet.

Frontend zod schemas are mostly type documentation here. `KaipokeJobCreateSchema.week_start` has the regex, but `useCreateKaipokeJob` posts the payload without parsing it.

**Migration Safety**

Editing `0003` in place is only safe if `0003_kaipoke_jobs_geocoding_ai` has never been applied anywhere shared. If any DB already ran the initial migration, Alembic will consider `0003` complete and will not replace the old non-unique index with `uq_kaipoke_job_items_job_seq`. In that case, ship a `0004` that drops the old index and creates the unique constraint.

The revised migration itself is coherent: unique constraint is created at [0003:117](/C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/backend/alembic/versions/0003_kaipoke_jobs_geocoding_ai.py:117) and dropped at `:201`.

**Security/RBAC**

Backend RBAC is the real protection and looks correct: jobs list/detail are admin/manager, create/cancel are admin, geocoding and AI logs are admin only.

Frontend RBAC is UX-only. Sidebar hides integrations only from `staff`, not managers, which matches manager access to Kaipoke list/detail. Direct `/integrations/geocoding` and `/integrations/ai` routes still render for non-admins and rely on backend 403.

**What Is Still Missing**

No duplicate job prevention per `job_type + week_start`, no Monday validation for `week_start`, no audit event for create/cancel, no PII redaction policy for AI prompts/logs, no API tests for pagination/RBAC/cancel races, and no actual geocoding writer using `address_hash`.

I could not run the narrow pytest check; the shell policy rejected the pytest command, so this is static review plus source verification.
