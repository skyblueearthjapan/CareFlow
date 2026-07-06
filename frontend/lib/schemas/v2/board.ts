/**
 * 週ボード read API (Phase2-3a) zod schemas — 現場ボード `/m` 用。
 *
 * Backend `backend/app/schemas/v2/board.py` の Pydantic schema と **完全一致**
 * させる。GET /api/v1/schedule/v2/board のレスポンス (office × weekday × course
 * → visits[]) を型安全に扱う。
 *
 * 実 Visit 由来の **実時刻** (start/end) を HH:MM 文字列でそのまま受け取る。
 * 空き枠の時間帯は本 API は扱わない (capacity.remaining と実訪問の実時刻のみ)。
 */
import { z } from 'zod';

/** 保険区分 (UI 表示用に短縮)。患者マスタ medical/care を med/care に正規化済み。 */
export const boardInsuranceSchema = z.enum(['med', 'care']);
export type BoardInsurance = z.infer<typeof boardInsuranceSchema>;

/** visit の配置種別 (Visit.type 由来)。 */
export const boardVisitModeSchema = z.enum(['normal', 'special']);
export type BoardVisitMode = z.infer<typeof boardVisitModeSchema>;

/** 曜日コード (患者マスタ weekly_pattern / propose-slots と同一表記)。 */
export const weekdayCodeSchema = z.enum(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']);
export type WeekdayCode = z.infer<typeof weekdayCodeSchema>;

/** ボード上の 1 訪問 (実 Visit 由来, 実時刻)。 */
export const boardVisitSchema = z.object({
  visit_id: z.string().uuid(),
  patient_id: z.string().uuid(),
  patient_name: z.string(),
  patient_kana: z.string().nullish(),
  insurance: boardInsuranceSchema.nullish(),
  service_minutes: z.number().int().nonnegative(),
  start_time: z.string(),
  end_time: z.string(),
  address: z.string().nullish(),
  lat: z.number().nullish(),
  lng: z.number().nullish(),
  same_address_group_id: z.string().nullish(),
  mode: boardVisitModeSchema.default('normal'),
  slot_index: z.number().int().nonnegative(),
  status: z.string().default('planned'),
});
export type BoardVisit = z.infer<typeof boardVisitSchema>;

/** コース当日の容量集計。 */
export const boardCapacitySchema = z.object({
  filled: z.number().int().nonnegative(),
  max: z.number().int(),
  total_minutes: z.number().int().nonnegative(),
  remaining: z.number().int(),
});
export type BoardCapacity = z.infer<typeof boardCapacitySchema>;

/** office × weekday の 1 コース。 */
export const boardCourseSchema = z.object({
  course_id: z.string().uuid().nullish(),
  course_code: z.string(),
  course_label: z.string(),
  staff_name: z.string().nullish(),
  visits: z.array(boardVisitSchema).default([]),
  capacity: boardCapacitySchema,
});
export type BoardCourse = z.infer<typeof boardCourseSchema>;

/** office × weekday の 1 マス (courses のコンテナ + 休講フラグ)。 */
export const boardCellSchema = z.object({
  office_id: z.string().uuid(),
  weekday: z.number().int().min(0).max(6),
  weekday_code: weekdayCodeSchema,
  closed: z.boolean().default(false),
  staff_count: z.number().int().nonnegative().default(0),
  manager_count: z.number().int().nonnegative().default(0),
  patient_count: z.number().int().nonnegative().default(0),
  courses: z.array(boardCourseSchema).default([]),
});
export type BoardCell = z.infer<typeof boardCellSchema>;

/** 対象拠点。 */
export const boardOfficeSchema = z.object({
  office_id: z.string().uuid(),
  office_name: z.string(),
});
export type BoardOffice = z.infer<typeof boardOfficeSchema>;

/** 曜日ヘッダー (日付 + 全拠点横断の集計)。 */
export const boardWeekdaySchema = z.object({
  weekday: z.number().int().min(0).max(6),
  weekday_code: weekdayCodeSchema,
  date: z.string(),
  patient_count: z.number().int().nonnegative().default(0),
});
export type BoardWeekday = z.infer<typeof boardWeekdaySchema>;

/** GET /v2/board レスポンス (週ボード描画構造)。 */
export const boardResponseSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  course_max: z.number().int().default(6),
  offices: z.array(boardOfficeSchema).default([]),
  weekdays: z.array(boardWeekdaySchema).default([]),
  board: z.array(boardCellSchema).default([]),
});
export type BoardResponse = z.infer<typeof boardResponseSchema>;
