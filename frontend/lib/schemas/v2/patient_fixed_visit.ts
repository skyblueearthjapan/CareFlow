/**
 * PatientFixedVisitV2 zod schemas (W9-FE1).
 *
 * 設計仕様書 `docs/plans/v2-allocation-redesign.md` §3.6.8 (固定枠ライフサイクル)
 * および `docs/plans/v2-api-contracts.md` §8 / §9 に基づく型定義。
 *
 * Backend `backend/app/schemas/v2/patient_fixed_visit.py` と完全一致させる。
 */
import { z } from 'zod';

export const PATIENT_FIXED_VISIT_MODES = ['normal', 'special'] as const;
export type PatientFixedVisitMode = (typeof PATIENT_FIXED_VISIT_MODES)[number];

export const patientFixedVisitV2BaseSchema = z.object({
  weekday: z.number().int().min(0).max(6),
  start_time: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/, 'HH:MM 形式'),
  duration_min: z.number().int().min(1).max(480).default(30),
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
