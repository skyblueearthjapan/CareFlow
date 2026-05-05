# W2-A 週ビュー — Codex Final Review

**Reviewer**: OpenAI Codex CLI v0.128.0 (gpt-5.5)
**Date**: 2026-05-05
**Scope**: Post-revise (Wave 2) final QA review

---

**VERDICT**

Conditional pass for W2-A, not production-ready. The critic's main defects are addressed at the data/security boundary: backend role enforcement is not just UI-only, week range filtering is pushed into `GET /visits`, unassigned fetch no longer sends `staff_id`, and secondary/mentor fan-out does not appear to inflate displayed summaries.

**Summary**

Role gates mostly hold. `list/get` allow admin/manager/staff but staff is scoped by `_staff_visibility_filter`; create/update require admin/manager; delete requires admin. Allocate also requires admin/manager server-side. The schedule UI now prevents staff chip clicks, but backend remains the real guard.

Week range is correctly ANDed onto the list query after deleted/staff predicates in [visits.py](<C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/backend/app/api/v1/visits.py:94>). No alternate `WHERE` branch bypasses it.

Unassigned now uses a separate call without `staff_id` in [visits.ts](<C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/frontend/lib/queries/visits.ts:161>), then filters `!primary_staff_id`. That satisfies the staff-filter skip requirement for admin/manager drilldown.

**Residual Issues**

1. [UnassignedList.tsx](<C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/frontend/components/schedule/UnassignedList.tsx:30>) always renders rows as buttons even when `onSelect` is undefined. For staff, [page.tsx](<C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/frontend/app/(app)/schedule/page.tsx:268>) passes no handler, producing focusable no-op controls. Not a security bypass, but it is inconsistent with the fixed read-only chip behavior.

2. `useUnassignedVisits` query key includes `role` but not `sessionStaffId` in [visits.ts](<C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/frontend/lib/queries/visits.ts:154>). Same-browser staff account switches can reuse another staff user's unassigned cache. Backend does not leak on fetch, but the client cache boundary is too coarse.

3. Warning state in [WeekGrid.tsx](<C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/frontend/components/schedule/WeekGrid.tsx:135>) is keyed only by `visit.id`. If a co-visit overlaps in one staff row, all appearances of that visit can render warning in other rows too. Scope warning keys by `staffId|date|visitId|role`.

**Bug-Class Detection**

Authorization: backend-enforced, acceptable. Client-only affordances are still incomplete around open dialogs/mutation hooks, but PATCH/DELETE/allocate return 403 when invoked improperly.

Date filtering: acceptable for list. Missing tests for `week_start`, `week_end`, staff filter interaction, and inverted ranges.

Unassigned split: functionally fixed, but cap/truncation remains risky. The unassigned query fetches the first 500 total visits in the week, then filters client-side, so late unassigned rows can disappear silently.

Double-counting: no evidence that secondary/mentor fan-out inflates allocate summaries or unassigned counts. Grid workload caps count per staff row, which is reasonable.

**Accessibility**

Table semantics improved with caption plus `scope="col"`/`scope="row"`. Remaining a11y issue is the no-op unassigned buttons. Static read-only rows should not be buttons.

**Still Missing For Production**

Server-side `patient_id` and unassigned-only filters, pagination beyond 500, tests for RBAC plus week/staff filters, cache keys that include auth identity, dialog-level read-only guards, and UI coverage for secondary/mentor fan-out edge cases.
