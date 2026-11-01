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
  /** 不在 = no_show の打刻がある訪問の件数 (訪問件数には数えたまま)。 */
  no_show_count: z.number().default(0),
  /** 同行した日数・件数 (副担当・メンター・同行リンク。訪問件数・出勤日数には入れない)。 */
  accompany_days: z.number().default(0),
  accompany_visits: z.number().default(0),
  /** 1 日の距離が注意の線・多いの線 (distance_lines) を超えた日数。 */
  days_over_caution: z.number().default(0),
  days_over_heavy: z.number().default(0),
  /** その日いちばん遠いお宅 (拠点からの直線 km) の期間の平均と最大。 */
  farthest_km_avg: z.number().nullish(),
  farthest_km_max: z.number().nullish(),
  /** 利用者の主の拠点が職員の拠点と違う訪問の割合 (0〜1)。 */
  outside_ratio: z.number().nullish(),
  /** 訪問と訪問の間の区間のうち 1 km 未満の割合 (0〜1)。 */
  short_leg_ratio: z.number().nullish(),
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
  /** 1 日の距離 (直線・拠点の行き帰りを含む) の目安の線 (km)。値は backend が 1 か所で持つ。 */
  distance_lines: z.object({
    target_km: z.number(),
    caution_km: z.number(),
    heavy_km: z.number(),
  }),
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
export type DistanceLines = StaffPerformanceResponse['distance_lines'];
