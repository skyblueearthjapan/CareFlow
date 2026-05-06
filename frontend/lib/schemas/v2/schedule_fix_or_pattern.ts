/**
 * Zod schemas for POST /api/v1/schedule/fix-or-pattern (W9-FE2 Phase 4).
 *
 * 設計仕様書 `docs/plans/v2-api-contracts.md` §8.6 (fix-or-pattern) に準拠。
 *
 * mode:
 *   - this_week_only : 当該週の visit のみ変更。固定枠 (PatientFixedVisit) は変更なし。
 *   - pattern_change : 固定枠を変更。翌週以降にも適用される。
 */
import { z } from 'zod';

export const SCHEDULE_FIX_MODES = ['this_week_only', 'pattern_change'] as const;
export type ScheduleFixMode = (typeof SCHEDULE_FIX_MODES)[number];

export const fixOrPatternRequestSchema = z.object({
  mode: z.enum(SCHEDULE_FIX_MODES),
  visit_id: z.string().uuid(),
  new_weekday: z.number().int().min(0).max(6),
  new_start_time: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/),
  new_duration_min: z.number().int().min(1).max(480),
  iso_year: z.number().int(),
  iso_week: z.number().int().min(1).max(53),
});

export const fixOrPatternResponseSchema = z.object({
  mode: z.enum(SCHEDULE_FIX_MODES),
  updated_visit: z.unknown().nullable().optional(),
  updated_fixed_visit: z.unknown().nullable().optional(),
});

export type FixOrPatternRequest = z.infer<typeof fixOrPatternRequestSchema>;
export type FixOrPatternResponse = z.infer<typeof fixOrPatternResponseSchema>;
