/**
 * PatientFixedVisitV2 zod schemas (W9-FE1).
 *
 * 設計仕様書 `docs/plans/v2-allocation-redesign.md` §3.6.8 (固定枠ライフサイクル)
 * および `docs/plans/v2-api-contracts.md` §8 / §9 に基づく型定義。
 *
 * Backend `backend/app/schemas/v2/patient_fixed_visit.py` と完全一致させる。
 *
 * W22: course_template_id (UUID | null) を追加。
 *   BE Wave 22 (feat/v2-w22-pfv-course) で migration 0025 として追加された列。
 *   並列実装のため型契約を先行ミラー。BE 完成後に再同期する。
 */
import { z } from 'zod';

export const PATIENT_FIXED_VISIT_MODES = ['normal', 'special'] as const;
export type PatientFixedVisitMode = (typeof PATIENT_FIXED_VISIT_MODES)[number];

export const patientFixedVisitV2BaseSchema = z.object({
  weekday: z.number().int().min(0).max(6),
  start_time: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/, 'HH:MM 形式'),
  duration_min: z.number().int().min(1).max(480).default(30),
  /** W22: コーステンプレート ID (UUID | null). null = 未指定 (Layer 1 フォールバック). */
  course_template_id: z.string().uuid().nullable().optional(),
});

export const patientFixedVisitV2ReadSchema = patientFixedVisitV2BaseSchema.extend({
  id: z.string().uuid(),
  patient_id: z.string().uuid(),
  mode: z.enum(PATIENT_FIXED_VISIT_MODES),
  created_at: z.string(),
  updated_at: z.string(),
});

export const patientFixedVisitsBulkPutSchema = z.object({
  mode: z.enum(PATIENT_FIXED_VISIT_MODES),
  items: z
    .array(patientFixedVisitV2BaseSchema)
    .max(7)
    .superRefine((items, ctx) => {
      const seen = new Set<number>();
      items.forEach((it, i) => {
        if (seen.has(it.weekday)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: [i, 'weekday'],
            message: '同じ曜日が重複しています',
          });
        }
        seen.add(it.weekday);
      });
    }),
});

export type PatientFixedVisitV2Base = z.infer<typeof patientFixedVisitV2BaseSchema>;
export type PatientFixedVisitV2Read = z.infer<typeof patientFixedVisitV2ReadSchema>;
export type PatientFixedVisitsBulkPut = z.infer<typeof patientFixedVisitsBulkPutSchema>;
