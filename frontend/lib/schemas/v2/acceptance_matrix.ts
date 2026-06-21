/**
 * 受け入れ枠マトリックス v2 zod schemas (P2 / FE).
 *
 * Backend `backend/app/schemas/v2/acceptance_matrix.py` の Pydantic schema と
 * **完全一致** させる。snake_case をそのまま維持する。
 *
 * GET /api/v1/acceptance-matrix
 *   ?iso_year=...&iso_week=...&office_id=...&service_minutes=...
 */
import { z } from 'zod';

import { acceptanceStatusEnum } from './acceptance';

export const matrixCellSourceEnum = z.enum(['auto', 'manual_standing']);
export type MatrixCellSource = z.infer<typeof matrixCellSourceEnum>;

export const matrixCellMetricsSchema = z.object({
  remaining_patients_total: z.number().int().nonnegative(),
  remaining_minutes_total: z.number().int().nonnegative(),
  available_course_count: z.number().int().nonnegative(),
  consult_course_count: z.number().int().nonnegative(),
  max_free_gap_minutes: z.number().int().nonnegative(),
  reasons: z.array(z.string()).default([]),
});
export type MatrixCellMetrics = z.infer<typeof matrixCellMetricsSchema>;

export const matrixCellSchema = z.object({
  time_slot: z.string(), // "HH:MM:SS"
  auto_status: acceptanceStatusEnum,
  manual_status: acceptanceStatusEnum.nullable(),
  effective_status: acceptanceStatusEnum,
  source: matrixCellSourceEnum,
  metrics: matrixCellMetricsSchema,
});
export type MatrixCell = z.infer<typeof matrixCellSchema>;

export const dayMatrixSchema = z.object({
  weekday: z.number().int().min(0).max(6),
  date: z.string(), // "YYYY-MM-DD"
  office_closed: z.boolean().default(false),
  cells: z.array(matrixCellSchema).default([]),
});
export type DayMatrix = z.infer<typeof dayMatrixSchema>;

export const officeMatrixSchema = z.object({
  office_id: z.string().uuid(),
  office_name: z.string(),
  city_names: z.array(z.string()).default([]),
  operating_weekdays: z.array(z.number().int().min(0).max(6)).default([]),
  week_generated: z.boolean().default(false),
  days: z.array(dayMatrixSchema).default([]),
});
export type OfficeMatrix = z.infer<typeof officeMatrixSchema>;

export const acceptanceMatrixResponseSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  service_minutes: z.number().int().positive(),
  slots: z.array(z.string()).default([]),
  week_generated_any: z.boolean().default(false),
  offices: z.array(officeMatrixSchema).default([]),
});
export type AcceptanceMatrixResponse = z.infer<typeof acceptanceMatrixResponseSchema>;
