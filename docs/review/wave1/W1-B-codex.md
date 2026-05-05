# W1-B スタッフマスタ — Codex Final Review

**Reviewer**: OpenAI Codex CLI v0.128.0 (gpt-5.5)
**Date**: 2026-05-05
**Scope**: Post-revise final QA review

---

**VERDICT**: Changes required. The critic items are mostly addressed, and backend RBAC prevents direct API escalation, but the revised UI still exposes actions that the backend will reject.

**Summary**: `staff_id` now flows from backend `UserOut.staff_id` into login response, NextAuth `User.staffId`, JWT `token.staffId`, and `session.user.staffId` (`frontend/lib/auth.ts:21,64,78,90`; `frontend/types/next-auth.d.ts:9,17,26`). Middleware consumes `role`, not `staffId` (`frontend/middleware.ts:8-29`), so per-staff ownership is not enforced there. Backend staff APIs remain authoritative: staff can list/read only own record, create/update are admin/manager, delete is admin only (`backend/app/api/v1/staff.py:53-57,72,93,107,130`).

**Residual issues**:
- `frontend/app/(app)/staff/[id]/page.tsx:120-123`: `canDelete = isPrivileged` gives managers the delete button, but backend `DELETE /staff/{id}` is admin-only. This is not a backend bypass, but it is a broken affordance and will produce avoidable 403s.
- `frontend/app/(app)/staff/[id]/page.tsx:121-122`: staff users get an edit button for their own record, but `PATCH /staff/{id}` is admin/manager only. Either remove own-staff edit UI or change backend policy deliberately.
- `frontend/app/(app)/staff/[id]/edit/page.tsx:63-123`: edit route has no session/role guard. Direct navigation renders the form for any user allowed to read that staff record; submit is blocked by backend for staff, but the client route gate is bypassable.
- `frontend/app/(app)/staff/page.tsx:125-129`: 500 warning fires at exactly 500 rows even though truncation is only proven when more rows exist. Needs total count or “may be truncated” wording.

**Security / RBAC**: No direct API privilege escalation found in the touched staff backend path. A staff user cannot read another staff record because backend returns 404, cannot POST/PATCH, and cannot DELETE. A manager cannot DELETE. The risky part is that frontend route/UI gates are advisory and inconsistent. Middleware allows `/staff/**` for all authenticated roles and does not validate `staffId`; that is acceptable only because backend ownership checks are load-bearing. Do not treat `session.user.role` or `staffId` as security boundaries, especially because NextAuth stores them from login and they can become stale after server-side role/staff assignment changes.

**Accessibility**: Dialog primitive replacement is a good improvement: focus trap, Escape close, labelled title/description are present (`DeleteConfirmModal.tsx:38-60`). Search and filter controls have `aria-label`s. Remaining issue: unauthorized edit/new pages return `null` during redirect in some paths, which can be disorienting; prefer an explicit loading/redirect state if this is user-visible.

**What’s still missing**: align UI permissions with backend policy, add edit-page route gating, and add tests for staff/manager/admin visibility of New/Edit/Delete plus direct navigation to `/staff/new` and `/staff/[id]/edit`. I did not run the test suite; this was a read-only review.
