# W1-A 患者マスタ — Codex Final Review

**Reviewer**: OpenAI Codex CLI v0.128.0 (gpt-5.5)
**Date**: 2026-05-05
**Scope**: Post-revise final QA review

---

**VERDICT**

Request changes. The revision fixed the schema/model direction, lat/lng blank handling, dialog primitive, warning alert, and toast work, but I would not sign off yet.

**Summary**

`weekly_pattern` parsing in [patients.ts](C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/frontend/lib/queries/patients.ts:102) is guarded with `try/catch` and rejects non-object JSON, so malformed input is handled safely once that code is reached. The problem is that [PatientForm.tsx](<C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/frontend/app/(app)/patients/_components/PatientForm.tsx:61>) still uses `zodResolver(patientCreateSchema)`, while the schema expects object records at [patient.ts](C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/frontend/lib/schemas/patient.ts:78) and the form actually registers string/boolean fields. That can block submit before the custom parser runs.

Backend `dict | None` at [patient.py](C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/backend/app/schemas/patient.py:28) is compatible with consumers that omit these fields or tolerate additive response fields. It is breaking for any client that previously sent `weekly_pattern` as a JSON string or `special_week` as boolean, because those are now recognized and validated as dicts instead of ignored.

**Residual Issues**

1. High: create/edit form validation is likely broken for default values. `PatientFormValues` defines `weekly_pattern: string` and `special_week: boolean` at [patient.ts](C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/frontend/lib/schemas/patient.ts:164), with defaults `''` and `false`, but the resolver schema expects records. Since `handleSubmit` runs resolver validation before `submitHandler`, the safer JSON parsing and `setError` path at [PatientForm.tsx](<C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/frontend/app/(app)/patients/_components/PatientForm.tsx:73>) may never execute. Fix with a dedicated form schema accepting textarea/checkbox values, or move the preprocessing into the resolver schema.

2. Medium: existing JSON fields cannot be cleared through edit. Empty `weekly_pattern` and unchecked `special_week` become `undefined` at [patients.ts](C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/frontend/lib/queries/patients.ts:116), `dropUndefined` removes them, and the backend applies only `exclude_unset` fields at [patients.py](C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/backend/app/api/v1/patients.py:110). Send explicit `null` when the user clears an existing JSON value.

3. Medium: API contract changed. Internal frontend can be adjusted, but external/generated clients need schema regeneration and a note that string/boolean legacy payloads now return 422.

**Security Concerns**

No direct XSS issue from `JSON.stringify`; React renders it as text. Backend RBAC remains enforced for create/update/delete. The main security/privacy concern is production scale: list fetch still pulls up to 500 patient records client-side, which increases PHI exposure and should move to server-side search/pagination.

**Accessibility**

The Radix dialog is a clear improvement: title/description/focus handling are now covered. Inline form errors exist, but the weekly-pattern textarea error is not clearly wired with `aria-invalid`/`aria-describedby`. Toasts are acceptable for secondary feedback, but the inline error must remain the primary failure path.

**Still Missing For Production**

Add tests for blank lat/lng, malformed weekly JSON, object-only JSON, clearing JSON fields, and PATCH default leakage. Add server-side pagination/search/total counts, a structured weekly-pattern editor, bounded JSON shape/size validation, and a documented migration/API compatibility note.

I could not run `pnpm -C frontend typecheck` or `lint`; the local command policy rejected those commands.
