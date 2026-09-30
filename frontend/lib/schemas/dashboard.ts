/**
 * スタッフ別の実績 (GET /api/v1/dashboard/staff-performance) の zod スキーマ。
 *
 * 設計: docs/plans/dashboard-staff-performance-design-2026-09-30.md §3 / §7。
 * 訪問の無い期間 (days = 0) は割り算の数字が null。
 */
import { z } from 'zod';

export const performanceMetricsSchema = z.object({
  days: z.number(),
  visits: z.number(),
  patients: z.number(),
  per_day: z.number().nullable(),
  plan_min: z.number().nullable(),
  /** 到着・退出が揃った訪問が min_actual_samples 件以上のときだけ。 */
  actual_min: z.number().nullable(),
  actual_samples: z.number(),
  arrival_only: z.number(),
  qr_ratio: z.number().nullable(),
  km_total: z.number(),
  km_per_day: z.number().nullable(),
  km_per_visit: z.number().nullable(),
  skipped_legs: z.number(),
  visit_min_per_day: z.number().nullable(),
  travel_min_per_day: z.number().nullable(),
  meeting_min_per_day: z.number().nullable(),
  idle_min_per_day: z.number().nullable(),
  meeting_min_total: z.number(),
  /** 未訪問 (no_show) の記録がある訪問の件数 (件数には数えたまま)。 */
  no_show_count: z.number().default(0),
  /** チーム平均のときだけ: 訪問のあった人数。 */
  staff_count: z.number().nullish(),
});

export const staffPerformanceRowSchema = z.object({
  staff_id: z.string().uuid(),
  name: z.string(),
  office_id: z.string().uuid().nullable(),
  office_short: z.string().nullable(),
  is_manager: z.boolean(),
  is_trainee: z.boolean(),
  qualification: z.string().nullable(),
  period: performanceMetricsSchema,
  weeks: z.array(performanceMetricsSchema),
});

export const staffPerformanceResponseSchema = z.object({
  date_from: z.string(),
  date_to: z.string(),
  office_id: z.string().uuid().nullable(),
  travel_speed_kmh: z.number(),
  min_actual_samples: z.number(),
  weeks: z.array(z.object({ start: z.string(), end: z.string() })),
  offices: z.array(z.object({ id: z.string().uuid(), name: z.string(), short_label: z.string() })),
  team: z.object({
    period: performanceMetricsSchema,
    weeks: z.array(performanceMetricsSchema),
  }),
  staff: z.array(staffPerformanceRowSchema),
});

export type PerformanceMetrics = z.infer<typeof performanceMetricsSchema>;
export type StaffPerformanceRow = z.infer<typeof staffPerformanceRowSchema>;
export type StaffPerformanceResponse = z.infer<typeof staffPerformanceResponseSchema>;
