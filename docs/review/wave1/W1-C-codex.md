# W1-C 拠点マスタ + Cities seed — Codex Final Review

**Reviewer**: OpenAI Codex CLI v0.128.0 (gpt-5.5)
**Date**: 2026-05-05
**Scope**: Post-revise final QA review

---

**VERDICT**

Pass with follow-ups. The critic’s main blockers are addressed: `prefecture`, `code`, and `allowed_cities` are now explicit API fields, `extra="forbid"` prevents silent payload drops, M2M writes go through `OfficeCity`, reads use `selectinload`, and `q` is server-side.

**Summary**

The revised backend contract is coherent: `OfficeBase` now includes `code` and `prefecture`, `OfficeCreate/OfficeUpdate` carry `allowed_cities`, and `OfficeRead` injects city UUIDs from the relationship. The frontend form initializes and submits all three new fields, and selected cities remain visible outside the active city filter.

**Residual Issues**

- Medium: clearing an existing `code` from the edit form does not round-trip. [OfficeForm.tsx](C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/frontend/app/(app)/offices/_components/OfficeForm.tsx:75) sends `code: code || undefined`; `JSON.stringify` omits that, so PATCH treats it as “unchanged”. Backend supports clearing via `code: null` in [office.py](C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/backend/app/schemas/office.py:31). Use `code: code || null` if empty should clear.
- Low/Medium: migration can fail on existing data with duplicate active office names because `uq_offices_name_active` is created as a unique partial index in [0002_add_office_prefecture_code.py](C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/backend/alembic/versions/0002_add_office_prefecture_code.py:38). This is not a data-loss bug, but it needs a preflight cleanup query or documented rollout check.
- Low: selected city labels depend on `useCities({ limit: 2000 })` in [OfficeForm.tsx](C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/frontend/app/(app)/offices/_components/OfficeForm.tsx:45). The UUIDs still submit, but selected labels may disappear once city count exceeds that page.

**Migration Safety**

The migration is reversible on PostgreSQL: downgrade drops the active-name partial index, drops `uq_offices_code`, then drops `code` and `prefecture`. Existing office rows are preserved on upgrade. Downgrade necessarily discards the newly added column data; `office_cities` is untouched. SQLite alembic reversibility is weaker because `drop_constraint` is not batch-wrapped, but this project’s migrations are clearly PostgreSQL-oriented.

**API Contract Integrity**

Good. Backend and frontend now agree on `code`, `prefecture`, and `allowed_cities`. `extra="forbid"` closes the original silent-drop path. List/detail/create/update all return `OfficeRead`, so the form can round-trip `allowed_cities`. `q` search moved server-side for offices and cities.

**What’s Still Missing**

Add focused tests for create/update/read with `prefecture`, `code`, and `allowed_cities`; PATCH clearing `code`; duplicate active-name migration preflight; and office `q` search by `name`/`code`. Existing office tests still only cover basic list/get/RBAC behavior.
