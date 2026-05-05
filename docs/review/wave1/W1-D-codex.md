# W1-D Allocation Engine + API — Codex Final Review

**Reviewer**: OpenAI Codex CLI v0.128.0 (gpt-5.5)
**Date**: 2026-05-05
**Scope**: Post-revise (commits d1e7ba9 + 3d169ce + c1d73be) final QA review

---

## VERDICT

ACCEPT-WITH-RESERVATIONS for W1-D merge. The c1d73be revisions close the prior API safety gaps well enough for an internal slice, but this is still not production-rollout ready.

## Summary

`@limiter.limit("3/minute")` is now on `run_allocate`, and `main.py` wires the slowapi limiter/429 handler. This prevents straightforward per-IP compute DoS; it is not a distributed/global concurrency guard.

The 5,000 visit cap is enforced before `_build_inputs`, engine construction, and `engine.allocate`, so it protects the allocation engine from oversized runs. It still loads ORM rows before rejecting.

`run_in_executor(None, engine.allocate, requests)` wrapped by `asyncio.wait_for(..., 30.0)` correctly offloads CPU work from the asyncio event loop. The 30s timeout is reasonable for a synchronous admin endpoint, but it does not kill the underlying thread after timeout.

`mapping_phase="minimal"` is returned on empty and non-empty paths, and nullable visit times now fail loudly instead of silently defaulting.

## Residual Issues

- API mapping remains intentionally lossy: staff shifts, areas, capacity, coordinates, real work days, NG staff, weekly patterns, and patient area are not fully mapped. `VisitRequest.need_staff=1` also ignores patient required staff count.
- API tests cover only 200/403/401/422. No tests cover 429 rate limit, 413 visit cap, 504 timeout, executor offload, `mapping_phase`, or NULL-time corruption.
- Cap is after DB materialization. Use `COUNT(*)`, `LIMIT cap+1`, or streaming to avoid DB/memory pressure before 413.
- Timeout is a response guard, not hard compute cancellation.

## Bug Coverage

- **Bug A** is covered: required-staff constraints are persisted and checked through Level1/relaxed paths.
- **Bug B** is covered: same patient + same day + same staff is rejected in reinsertion/relaxed/full-pipeline tests.
- **Bug C** is not named literally, but if it refers to the C-series regressions, C1-C4 are covered.
- **Bug D** is not identifiable in code/tests/docs; no direct coverage found.

## Performance + Memory: 300x50

For 300 visits x 50 staff, memory should be acceptable and 30s is a reasonable HTTP ceiling. For 300 patients with daily visits, the run is still under the 5,000 cap but can move into seconds-to-tens due to multi-trial scans, gap packing, ejection, and route optimization. No benchmark test proves this yet. Response size is bounded by the cap but still returns every assignment.

## Security

Authz is admin/manager only. Rate limiting reduces abuse. SQLAlchemy query construction is safe from injection. Remaining production gaps are global concurrency limits, audit logging, request correlation, and operational metrics.

## Still Missing For Production Rollout

- Full W1-G mapping or feature gating
- Cap-before-load (count + limit before materialization)
- API safety tests (429/413/504/mapping_phase/NULL-time)
- Benchmark/load tests
- Job + polling model for long runs
- Idempotency/week lock
- Audit log integration
- Latency/trial metrics
- Apache-2.0 LICENSE/NOTICE verification
