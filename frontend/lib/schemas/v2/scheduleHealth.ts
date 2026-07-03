/**
 * Schedule Advisor — スケジュール健康診断 (Phase 1) の zod schema.
 *
 * BE 契約 (凍結済):
 *   GET /api/v1/schedule/v2/schedule-health?iso_year=&iso_week=&office_id=(任意)
 *   RBAC: admin / manager (read-only).
 *
 * 設計仕様書: ``docs/plans/schedule-advisor-design.md`` §3 Phase 1.
 *
 * 「移動や隙間の無駄」を数字で見せる健康診断パネル用. 数値系フィールドは
 * すべて z.number() (BE は分・km を数値で返す). 未知フィールドには寛容だが
 * strict 過ぎない方針 (既存 v2 スキーマの作法に合わせ passthrough は付けない).
 */
import { z } from 'zod';

// ---------------------------------------------------------------------------
// 集計メトリクス (6 フィールド). courses[].totals / weekdays / week_totals で共有.
// ---------------------------------------------------------------------------

export const scheduleHealthTotalsSchema = z.object({
  visit_count: z.number().default(0),
  service_minutes: z.number().default(0),
  travel_minutes: z.number().default(0),
  travel_km: z.number().default(0),
  buffer_minutes: z.number().default(0),
  gap_minutes: z.number().default(0),
});
export type ScheduleHealthTotals = z.infer<typeof scheduleHealthTotalsSchema>;

// ---------------------------------------------------------------------------
// 1 コース分のメトリクス (1 曜日内).
// ---------------------------------------------------------------------------

export const scheduleHealthCourseSchema = z.object({
  course_code: z.string(),
  staff_name: z.string().nullable().default(null),
  visit_count: z.number().default(0),
  patient_count: z.number().default(0),
  service_minutes: z.number().default(0),
  travel_minutes: z.number().default(0),
  travel_km: z.number().default(0),
  buffer_minutes: z.number().default(0),
  gap_minutes: z.number().default(0),
});
export type ScheduleHealthCourse = z.infer<typeof scheduleHealthCourseSchema>;

// ---------------------------------------------------------------------------
// 1 曜日分 (0=月..6=日) のコース群 + 曜日合計.
// ---------------------------------------------------------------------------

export const scheduleHealthWeekdaySchema = z.object({
  weekday: z.number().int().min(0).max(6),
  courses: z.array(scheduleHealthCourseSchema).default([]),
  totals: scheduleHealthTotalsSchema,
});
export type ScheduleHealthWeekday = z.infer<typeof scheduleHealthWeekdaySchema>;

// ---------------------------------------------------------------------------
// 1 拠点分 (曜日群 + 週合計).
// ---------------------------------------------------------------------------

export const scheduleHealthOfficeSchema = z.object({
  office_id: z.string(),
  office_name: z.string(),
  weekdays: z.array(scheduleHealthWeekdaySchema).default([]),
  week_totals: scheduleHealthTotalsSchema,
});
export type ScheduleHealthOffice = z.infer<typeof scheduleHealthOfficeSchema>;

// ---------------------------------------------------------------------------
// レスポンス全体.
// ---------------------------------------------------------------------------

export const scheduleHealthResponseSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  offices: z.array(scheduleHealthOfficeSchema).default([]),
});
export type ScheduleHealthResponse = z.infer<typeof scheduleHealthResponseSchema>;

// ---------------------------------------------------------------------------
// 見直しどきトレンド (Phase 3「見直しどき通知」).
//   GET /api/v1/schedule/v2/schedule-health/trend?iso_year=&iso_week=&weeks=&office_id=(任意)
//   各週の office 横断合計のみ (4 指標) を古→新順で返す. 劣化判定は FE 側.
// ---------------------------------------------------------------------------

export const scheduleHealthTrendTotalsSchema = z.object({
  visit_count: z.number().default(0),
  travel_minutes: z.number().default(0),
  travel_km: z.number().default(0),
  gap_minutes: z.number().default(0),
});
export type ScheduleHealthTrendTotals = z.infer<typeof scheduleHealthTrendTotalsSchema>;

export const scheduleHealthTrendWeekSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  totals: scheduleHealthTrendTotalsSchema,
});
export type ScheduleHealthTrendWeek = z.infer<typeof scheduleHealthTrendWeekSchema>;

export const scheduleHealthTrendResponseSchema = z.object({
  weeks: z.array(scheduleHealthTrendWeekSchema).default([]),
});
export type ScheduleHealthTrendResponse = z.infer<typeof scheduleHealthTrendResponseSchema>;

// ---------------------------------------------------------------------------
// H1: 原因ドリルダウン (GET /v2/schedule-health/course-detail)
// BE `backend/app/schemas/v2/schedule_health.py` の CourseDetail* と 1:1。
// ---------------------------------------------------------------------------

/** コース内の連続する 2 訪問間の移動 1 件 (同住所=0/座標欠損=0 は健康診断と同一規約). */
export const courseDetailTransitionSchema = z.object({
  from_patient_id: z.string().uuid(),
  from_patient_name: z.string(),
  from_end_time: z.string(),
  to_patient_id: z.string().uuid(),
  to_patient_name: z.string(),
  to_start_time: z.string(),
  travel_minutes: z.number().int().min(0),
  travel_km: z.number().min(0),
});
export type CourseDetailTransition = z.infer<typeof courseDetailTransitionSchema>;

/** 患者 1 名の配置コスト (= W3 厳密限界コスト。抜くと浮く travel+buffer). */
export const courseDetailPatientCostSchema = z.object({
  patient_id: z.string().uuid(),
  patient_name: z.string(),
  start_time: z.string(),
  marginal_minutes: z.number().int(),
  marginal_km: z.number(),
});
export type CourseDetailPatientCost = z.infer<typeof courseDetailPatientCostSchema>;

export const courseDetailWeekdaySchema = z.object({
  weekday: z.number().int().min(0).max(6),
  course_label: z.string(),
  staff_name: z.string().nullable().default(null),
  totals: z.object({
    travel_minutes: z.number().int().min(0),
    travel_km: z.number().min(0),
  }),
  transitions: z.array(courseDetailTransitionSchema).default([]),
  patient_costs: z.array(courseDetailPatientCostSchema).default([]),
});
export type CourseDetailWeekday = z.infer<typeof courseDetailWeekdaySchema>;

export const scheduleHealthCourseDetailResponseSchema = z.object({
  office_id: z.string().uuid(),
  course_code: z.string(),
  weekdays: z.array(courseDetailWeekdaySchema).default([]),
});
export type ScheduleHealthCourseDetailResponse = z.infer<
  typeof scheduleHealthCourseDetailResponseSchema
>;
