/**
 * place-and-fix v2 zod schemas (Wave 15 Phase 3 / W15-FE).
 *
 * BE 側 POST /api/v1/schedule/place-and-fix のリクエスト/レスポンス契約を
 * Zod でミラー。BE 実装仕様 (フラット単一オブジェクト) に整合済み。
 */
import { z } from 'zod';

import { visitV2ReadSchema } from './visit';
import { patientFixedVisitV2ReadSchema } from './patient_fixed_visit';

// ---------------------------------------------------------------------------
// Request schema — フラット単一オブジェクト (BE 仕様)
// ---------------------------------------------------------------------------

export const placeAndFixRequestSchema = z.object({
  patient_id: z.string().uuid(),
  // W15-codex-fix (1): ドロップ先の course_template_id (BE 必須).
  // BE は (course_template_id, iso_year, iso_week, weekday) で週次 Course を
  // find/create し、Visit.course_id に紐付けて画面表示を維持する。
  course_template_id: z.string().uuid(),
  iso_year: z.number().int(),
  iso_week: z.number().int().min(1).max(53),
  weekday: z.number().int().min(0).max(6),
  start_time: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$/),
  duration_min: z.number().int().min(1).max(480),
  staff_count: z.union([z.literal(1), z.literal(2)]).default(1),
  fix_pattern: z.boolean().default(true),
});

export type PlaceAndFixRequest = z.infer<typeof placeAndFixRequestSchema>;

// ---------------------------------------------------------------------------
// Response schema — {visit, fixed_visit} (BE 仕様)
// ---------------------------------------------------------------------------

export const placeAndFixResponseSchema = z.object({
  visit: visitV2ReadSchema,
  fixed_visit: patientFixedVisitV2ReadSchema.nullable(),
});

export type PlaceAndFixResponse = z.infer<typeof placeAndFixResponseSchema>;
