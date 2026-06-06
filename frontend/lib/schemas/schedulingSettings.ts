/**
 * Scheduling settings (自動最適化ルール) zod schemas — Phase G-88 Step4.
 *
 * Mirrors the backend contract (`/api/v1/scheduling-settings`, Phase A 実装済):
 *   GET → { values: {...8項目}, is_default: {...8項目 bool} }
 *   PUT → 部分更新 (omit=不変, 明示 null=既定リセット), 範囲外/時刻順→422, admin/manager.
 *         レスポンスは GET と同形.
 *
 * 8 項目 (すべて事業所共通・1 セット):
 *   ① visit_buffer_min        int   既定 8  / 0..30
 *   ② travel_speed_kmh        float 既定 20 / 10..40
 *   ③a lunch_duration_min     int   既定 60 / 30..90
 *   ③b lunch_window_start     "HH:MM:SS" 既定 11:30:00
 *      lunch_window_end       "HH:MM:SS" 既定 13:30:00
 *   ④ business_start          "HH:MM:SS" 既定 09:30:00
 *      business_end           "HH:MM:SS" 既定 18:00:00
 *   ⑤ max_patients_per_course int   既定 6  / 1..10
 *
 * 時刻は "HH:MM:SS" 文字列 (秒は常に 00)。範囲チェックは UI 側でも行うが、
 * 最終的な 422 は BE が担保するため zod は型(形)の検証のみに留める。
 */
import { z } from 'zod';

/** "HH:MM:SS" 形式の時刻文字列 (24h, 秒含む)。 */
export const timeStringSchema = z
  .string()
  .regex(/^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$/, 'HH:MM:SS 形式で指定してください');

/** GET / PUT レスポンスの `values`。BE が常に全 8 項目を返す。 */
export const schedulingSettingsValuesSchema = z.object({
  visit_buffer_min: z.number().int(),
  travel_speed_kmh: z.number(),
  lunch_duration_min: z.number().int(),
  lunch_window_start: timeStringSchema,
  lunch_window_end: timeStringSchema,
  business_start: timeStringSchema,
  business_end: timeStringSchema,
  max_patients_per_course: z.number().int(),
});

/** 各項目が既定値由来か (= ユーザー未設定) を示すフラグ群。 */
export const schedulingSettingsIsDefaultSchema = z.object({
  visit_buffer_min: z.boolean(),
  travel_speed_kmh: z.boolean(),
  lunch_duration_min: z.boolean(),
  lunch_window_start: z.boolean(),
  lunch_window_end: z.boolean(),
  business_start: z.boolean(),
  business_end: z.boolean(),
  max_patients_per_course: z.boolean(),
});

/** GET / PUT のレスポンス全体。 */
export const schedulingSettingsResponseSchema = z.object({
  values: schedulingSettingsValuesSchema,
  is_default: schedulingSettingsIsDefaultSchema,
});

/**
 * PUT のリクエストボディ。
 *   - 省略 (undefined) = 不変
 *   - 明示 null        = 既定値にリセット
 *   - 値               = 更新
 */
export const schedulingSettingsUpdateSchema = z.object({
  visit_buffer_min: z.number().int().nullable().optional(),
  travel_speed_kmh: z.number().nullable().optional(),
  lunch_duration_min: z.number().int().nullable().optional(),
  lunch_window_start: timeStringSchema.nullable().optional(),
  lunch_window_end: timeStringSchema.nullable().optional(),
  business_start: timeStringSchema.nullable().optional(),
  business_end: timeStringSchema.nullable().optional(),
  max_patients_per_course: z.number().int().nullable().optional(),
});

export type SchedulingSettingsValues = z.infer<typeof schedulingSettingsValuesSchema>;
export type SchedulingSettingsIsDefault = z.infer<typeof schedulingSettingsIsDefaultSchema>;
export type SchedulingSettingsResponse = z.infer<typeof schedulingSettingsResponseSchema>;
export type SchedulingSettingsUpdate = z.infer<typeof schedulingSettingsUpdateSchema>;

/** PUT で「全項目を既定にリセット」する payload (各項目 null)。 */
export const SCHEDULING_SETTINGS_RESET_ALL: SchedulingSettingsUpdate = {
  visit_buffer_min: null,
  travel_speed_kmh: null,
  lunch_duration_min: null,
  lunch_window_start: null,
  lunch_window_end: null,
  business_start: null,
  business_end: null,
  max_patients_per_course: null,
};

/** UI の範囲制約 (スライダー / ステッパー / バリデーション用)。BE 契約と一致。 */
export const SCHEDULING_RANGES = {
  visit_buffer_min: { min: 0, max: 30, step: 1 },
  travel_speed_kmh: { min: 10, max: 40, step: 1 },
  lunch_duration_min: { min: 30, max: 90, step: 5 },
  max_patients_per_course: { min: 1, max: 10, step: 1 },
} as const;
