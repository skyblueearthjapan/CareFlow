# W1-F shadcn/ui プリミティブ — Codex Final Review

**Reviewer**: OpenAI Codex CLI v0.128.0 (gpt-5.5)
**Date**: 2026-05-05
**Scope**: Post-revise final QA review

---

**VERDICT**: Inconclusive (session truncated).

**Note on this review**: The Codex CLI session was cut off before producing a final VERDICT/Summary block. The shell sandbox rejected `pnpm exec tsc`, `.\node_modules\.bin\tsc`, and `node node_modules/typescript/bin/tsc` calls, so no typecheck was executed, and the run was terminated mid-investigation while still inspecting `frontend/lib/forms.ts`, `frontend/components/ui/dialog.tsx`, `frontend/components/ui/form.tsx`, the Radix `react-dialog` `aria-describedby` warning behavior, and the zod `coerce.number/string` typings. The fragments below are the only Codex narrative captured; treat as preliminary observations rather than a signed-off review.

**Preliminary observations (Codex partial output)**

- Initial framing: review intended as a final PR pass — diff `f2841cb..8ab163d` for `frontend/lib/forms.ts`, the shadcn primitives, and `frontend/package.json`, plus a check on whether the `useFormWithSchema` helper’s generics preserve input/output typing for W1-A `z.coerce.number` schemas.
- W1-A integration check: "The W1-A patient form itself still uses `useForm<PatientFormValues>` plus a resolver cast, so the new helper is not exercised there directly." Codex flagged this and intended to "separate ‘compiles now’ from ‘sound for the intended W1-A pattern,’" but never reached that conclusion.
- Static evidence collected: `forms.ts` exports `useFormWithSchema`; `dialog.tsx` exposes typed `aria-describedby` and `displayName` on `DialogOverlay`/`DialogContent`/`DialogTitle`/`DialogDescription`; `form.tsx` provides `FormField`/`FormItem`/`FormLabel`/`FormControl`/`FormDescription`/`FormMessage` with `forwardRef` + `displayName`; `@hookform/resolvers@3.10.0` is locked against `react-hook-form@7.75.0` and `react@19.0.0-rc-69d4b800-20241021` in `pnpm-lock.yaml`.
- Sandbox blockers (no verdict possible): `tsc --noEmit` could not be executed under three different invocations; all `pnpm`/`.bin`/`node` paths were rejected by policy. No lint, no type-check, no test run was completed.

**What’s still missing**

A re-run of the Codex review under a sandbox profile that allows `pnpm -C frontend typecheck` and `pnpm -C frontend lint`, focused on: (1) `useFormWithSchema` generic soundness against `z.coerce.number` (W1-A patient schema), (2) Radix `DialogContent` `aria-describedby` runtime warning suppression, (3) `displayName`/`forwardRef` parity with shadcn upstream, and (4) any UI primitives still missing relative to W2 needs.

**Cross-reference**: `W1-F-critic.md` already issued **VERDICT: ACCEPT-WITH-RESERVATIONS** with concrete W2-blocker follow-ups; treat that critic verdict as authoritative until a complete Codex pass is available.
