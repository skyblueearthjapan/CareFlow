# W2-D ダッシュボード — Codex Final Review

**Reviewer**: OpenAI Codex CLI v0.128.0 (gpt-5.5)
**Date**: 2026-05-05
**Scope**: Post-revise (Wave 2) final QA review

---

## VERDICT

**ACCEPT WITH RESERVATIONS.** The revise fixes the main runtime bugs: JST date anchoring, secondary/mentor staff scoping, cancelled exclusion for today/overlap, and zero-safe aggregates. I would not call it fully clean because the `VisitStatus` fix is mostly cosmetic and several staff/trend paths are still only indirectly covered.

## Summary

`zoneinfo` is safe for this repo: `backend/pyproject.toml` requires Python `>=3.12`, so `from zoneinfo import ZoneInfo` in `dashboard.py:22` does not create a supported-runtime break for Python `<3.9`.

The staff scope change uses SQLAlchemy expressions inside `or_()` at `dashboard.py:68-71`. This remains parameterized; `Visit.primary_staff_id == staff_id` and peers become bound parameters, not string interpolation.

Cancelled visits are excluded from today's KPI query at `dashboard.py:126-134`, so they no longer inflate `today_visits` or `today_overlapping`.

## Residual Issues

`VisitStatus` is **declared, not meaningfully used**. `models/visit.py:32` defines the `Literal`, but `Visit.status` remains `Mapped[str]` at `models/visit.py:76`, and dashboard comparisons still use raw strings at `dashboard.py:134`, `150`, `159`, and `202`. The import in `dashboard.py:28` is suppressed with `# noqa: F401` and referenced only by comments. This does not enforce allowed values or reduce hard-code drift.

Potential semantic bug: `_staff_scope()` now includes secondary/mentor visits, but `_count_overlaps()` still groups only by `primary_staff_id` at `dashboard.py:83-107`, and `get_kpi()` passes only primary staff into it at `dashboard.py:152-153`. A staff user who is secondary/mentor on overlapping visits with different primary staff may see `today_overlapping = 0`. If overlap is intentionally "primary staff conflict only," that is fine; if it means "my assigned schedule conflicts," this is still wrong.

Week and trend aggregates still include cancelled visits in totals. Only today's query filters `status != "cancelled"`. If cancelled visits should be excluded from operational visit counts consistently, `week_stmt` and `/trend` need the same rule.

## Performance

Admin paths are acceptable: date-window filters and grouped trend queries can use `ix_visits_date`.

Staff paths are weaker after the OR expansion. `models/visit.py:85-88` has indexes for `visit_date`, `patient/date`, `primary/date`, and `status`, but no `(secondary_staff_id, visit_date)` or `(mentor_staff_id, visit_date)`. On larger datasets, staff dashboards involving secondary/mentor scope may degrade to broader scans. Add secondary/mentor date indexes, or consider a normalized assignment table if these roles keep expanding.

The Python overlap pass is acceptable for small daily cardinality, but it is O(n²) per primary staff bucket. Probably fine under the stated `<200 rows/day` assumption.

## What Is Still Missing

Tests cover KPI admin, KPI staff across primary/secondary/mentor, staff-without-staff-id for KPI, adjacent overlap, cancelled overlap, empty completion rate, trend backfill, days bounds, and KPI JST.

Missing coverage: `/trend` staff scope for secondary/mentor, `/trend` staff-without-staff-id, `/trend` JST anchoring, non-empty weekly completion math, cancelled behavior for week/trend, and the secondary/mentor overlap semantic above.

Also note frontend/backend comments still say staff sees "primary-staff visits" in places, which is now stale documentation.

I could not run pytest: this shell policy rejected `python`/`pytest` execution, so this review is static.
