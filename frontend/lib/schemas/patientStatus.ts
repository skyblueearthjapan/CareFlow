/**
 * 患者ステータス連動 (docs/plans/patient-status-schedule-design-2026-09-09.md §7-3)。
 * BE `backend/app/schemas/patient_status.py` と 1:1 (キー名を変えない)。
 *
 * - GET  /api/v1/patients/{id}/status-impact?to=&from_date=  → statusImpactSchema
 * - POST /api/v1/patients/{id}/status-change                 → statusChangeRequestSchema / statusChangeResultSchema
 */
import { z } from 'zod';

import { patientReadSchema, statusEnum } from './patient';

export const statusDirectionEnum = z.enum(['deactivate', 'reactivate', 'none']);
export const specialPeriodActionEnum = z.enum(['keep', 'end']);

export const weekCountSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  count: z.number().int().nonnegative(),
  label: z.string(),
});

export const impactVisitsSchema = z.object({
  total: z.number().int().nonnegative(),
  by_week: z.array(weekCountSchema).default([]),
  by_source: z.record(z.number().int()).default({}),
  pair_groups: z.number().int().nonnegative().default(0),
  excluded: z.record(z.number().int()).default({}),
});

export const impactSpecialPeriodSchema = z.object({
  id: z.string().uuid(),
  start_date: z.string(),
  end_date: z.string(),
  pool_marks: z.number().int().nonnegative(),
  placed_marks: z.number().int().nonnegative(),
  placed_future_visits: z.number().int().nonnegative(),
});

export const impactRegenerateSchema = z.object({
  weeks: z.array(weekCountSchema).default([]),
  total: z.number().int().nonnegative().default(0),
});

export const statusImpactSchema = z.object({
  patient_id: z.string().uuid(),
  current_status: z.string(),
  to_status: statusEnum,
  from_date: z.string(),
  direction: statusDirectionEnum,
  visits: impactVisitsSchema,
  special_period: impactSpecialPeriodSchema.nullish(),
  fixed_visit_rows: z.number().int().nonnegative().default(0),
  pending_requests: z.number().int().nonnegative().default(0),
  kaipoke_weeks: z.number().int().nonnegative().default(0),
  regenerate: impactRegenerateSchema.nullish(),
});
export type StatusImpact = z.infer<typeof statusImpactSchema>;

export const statusChangeRequestSchema = z.object({
  status: statusEnum,
  from_date: z.string().optional(),
  special_period_action: specialPeriodActionEnum.default('keep'),
  regenerate: z.boolean().default(true),
  note: z.string().max(200).optional(),
});
export type StatusChangeRequest = z.input<typeof statusChangeRequestSchema>;

export const opGroupRefSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  op_group_id: z.string().uuid(),
});

export const statusChangeResultSchema = z.object({
  patient: patientReadSchema,
  direction: statusDirectionEnum,
  cancelled_visit_ids: z.array(z.string().uuid()).default([]),
  cancelled_count: z.number().int().nonnegative().default(0),
  special_period: z
    .object({
      id: z.string().uuid(),
      action: specialPeriodActionEnum,
      cancelled_pool_marks: z.number().int().nonnegative().default(0),
    })
    .nullish(),
  rejected_requests: z.number().int().nonnegative().default(0),
  op_groups: z.array(opGroupRefSchema).default([]),
  regenerated: z
    .object({ created: z.number().int().nonnegative(), weeks: z.array(weekCountSchema).default([]) })
    .nullish(),
  notification_count: z.number().int().nonnegative().default(0),
});
export type StatusChangeResult = z.infer<typeof statusChangeResultSchema>;

/**
 * 422 `{"detail": {"code": "patient_not_active", ...}}` (Phase 2 入口ガード・§7-3 (d))。
 * `can_override` が true なら「稼働中にして続ける」導線を出す。
 */
export const patientNotActiveDetailSchema = z.object({
  code: z.literal('patient_not_active'),
  patient_id: z.string().uuid(),
  status: z.string(),
  status_label: z.string(),
  can_override: z.boolean().default(false),
  message: z.string(),
});
export type PatientNotActiveDetail = z.infer<typeof patientNotActiveDetailSchema>;
