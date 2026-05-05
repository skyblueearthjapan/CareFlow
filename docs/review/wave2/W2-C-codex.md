# W2-C モバイル4画面 — Codex Final Review

**Reviewer**: OpenAI Codex CLI v0.128.0 (gpt-5.5)
**Date**: 2026-05-05
**Scope**: Post-revise (Wave 2) final QA review

---

**VERDICT**

Not ship-ready. The revise fixes several critic items, but two functional/privacy gaps remain: check-in localStorage is not cleared on sign-out or user/role switch, and mobile home/week counting still has a timezone/capping bug.

**Summary**

Server-side `staff_id` forwarding is now correct: `useMyVisits` always sends `staff_id` from `session.user.staffId` (`frontend/lib/queries/me.ts:96-105`), and the backend honors it for admin/manager while forcing staff users to their own staff id (`backend/app/api/v1/visits.py:90-99`). `addDays` is now local-date based and should handle month/year rollover via `Date#setDate` (`me.ts:78-85`). `MobileShell` strict prefix matching handles `/m/today/` and `/m/today/123` without matching `/m/todayspecial` (`MobileShell.tsx:38-39`).

**Residual Issues**

1. `localStorage` cleanup is missing. Check-in records are stored under `checkin:${visitId}` (`today/[visitId]/page.tsx:109,129`) and removed only for that visit on server success (`:138`). The sign-out button only calls `signOut()` (`mypage/page.tsx:149`). I found no clear-on-signout, clear-on-401 signout, or staff/role-change purge.

2. Home week count is still TZ unsafe. `home/page.tsx:55-58` computes `weekEnd` with `toISOString()`. In Asia/Tokyo, local Monday midnight serializes as the previous UTC date, so the exclusive end can be one day early and exclude Sunday.

3. Visit list paging is fragile. `useMyVisits` requests only `limit=100` (`me.ts:103-105`) and then filters date/week client-side. Backend supports `week_start`/`week_end`, but they are not forwarded. A staff member with more than 100 rows can get wrong today/week/month counts or empty lists.

4. Error handling is broader than stated. 404/405/501 are covered, but every non-`ApiError` is treated as "backend not implemented" (`today/[visitId]/page.tsx:165-173`). That catches network failures, but also masks unexpected client/runtime errors as successful offline saves.

**Security**

The local record contains visit id, timestamp, and optional precise lat/lng (`today/[visitId]/page.tsx:102-108`). That is PII/PHI-adjacent in a care app, persists across logout, and is readable by any script running on the origin. At minimum: namespace by user/staff id, clear all `checkin:*` keys on signOut and auth refresh failure, purge on staffId/role change, and add TTL. Prefer not storing precise coordinates until server sync exists.

**Accessibility**

Active bottom nav is visual only; no `aria-current="page"` is emitted. The notification button uses `aria-disabled="true"` but remains clickable and keyboard-activatable (`home/page.tsx:86-93`), which is semantically mixed. Either make it a normal button that opens a "coming soon" toast, or actually disable it and expose the message elsewhere.

**What Is Still Missing**

Check-in/out are still offline-only fallbacks until backend endpoints exist. Photo upload remains a toast/TODO. Mypage now fetches staff name, but the row is still labeled as office/affiliation while showing a staff person's name. I could not run `pnpm -C frontend typecheck`; the sandbox policy rejected the command.
