/**
 * 提案 API (Phase2-2) zod schemas — 現場ボード `/m` の自動提案用。
 *
 * Backend `backend/app/schemas/v2/propose_slots.py` の Pydantic schema と
 * **完全一致** させる。POST /api/v1/schedule/v2/propose-slots のリクエスト /
 * レスポンスを型安全に扱う。実現可能な空き枠のみが返る (距離 / バッファー /
 * 昼休み / 18:00 / 容量 / time_type / 同住所を判定済)。
 */
import { z } from 'zod';

import { weekdayCodeSchema, type WeekdayCode } from './board';

/** 候補患者の希望時刻種別 (患者マスタ TimeTypeV2 に準拠)。 */
export const proposeTimeTypeSchema = z.enum(['固定', '時間帯', '午前', '午後', '終日']);
export type ProposeTimeType = z.infer<typeof proposeTimeTypeSchema>;

/** 訪問頻度 (患者マスタ VisitFrequencyV2 に準拠)。 */
export const proposeVisitFrequencySchema = z.enum(['every', 'biweekly', 'monthly']);
export type ProposeVisitFrequency = z.infer<typeof proposeVisitFrequencySchema>;

const hhmmSchema = z
  .string()
  .regex(/^([01]?\d|2[0-3]):[0-5]\d$/, '時刻は HH:MM 形式で入力してください');

/** POST /v2/propose-slots リクエスト (候補患者の希望)。 */
export const proposeSlotsRequestSchema = z.object({
  // 候補の座標確定 (address 優先 → lat/lng)。
  address: z.string().nullish(),
  lat: z.number().min(-90).max(90).nullish(),
  lng: z.number().min(-180).max(180).nullish(),

  // 候補の希望 (患者マスタ項目に準拠)。
  service_minutes: z.number().int().min(1).max(480),
  time_type: proposeTimeTypeSchema.nullish(),
  preferred_start: hhmmSchema.nullish(),
  preferred_end: hhmmSchema.nullish(),
  preferred_weekdays: z.array(weekdayCodeSchema).default([]),
  visit_frequency: proposeVisitFrequencySchema.nullish(),
  frequency_per_week: z.number().int().min(1).max(7).nullish(),
  requires_multiple_staff: z.boolean().default(false),
  sex_restriction: z.string().nullish(),

  // 対象週 / 拠点。
  iso_year: z.number().int().min(2020).max(2100),
  iso_week: z.number().int().min(1).max(53),
  office_ids: z.array(z.string().uuid()).default([]),

  // 既存患者からの提案用。
  existing_patient_id: z.string().uuid().nullish(),

  limit: z.number().int().min(1).max(50).default(10),
});
export type ProposeSlotsRequest = z.input<typeof proposeSlotsRequestSchema>;

/** ミニスケジュール 1 行 (そのコース当日の既存訪問 + 提案枠)。 */
export const proposeMiniScheduleEntrySchema = z.object({
  time: z.string(),
  name: z.string(),
  ins: z.string().nullish(),
  is_here: z.boolean().default(false),
  is_pair: z.boolean().default(false),
});
export type ProposeMiniScheduleEntry = z.infer<typeof proposeMiniScheduleEntrySchema>;

/** ランキング済み提案スロット 1 件。 */
export const proposeSlotItemSchema = z.object({
  office_id: z.string().uuid(),
  office_name: z.string().nullish(),
  weekday: z.number().int().min(0).max(6),
  weekday_code: weekdayCodeSchema,
  course_code: z.string(),
  course_label: z.string(),
  staff_name: z.string().nullish(),
  start_time: z.string(),
  end_time: z.string(),
  score: z.number(),
  reasons: z.array(z.string()).default([]),
  warnings: z.array(z.string()).default([]),
  is_pair: z.boolean().default(false),
  pair_partner: z.string().nullish(),
  mini_schedule: z.array(proposeMiniScheduleEntrySchema).default([]),
});
export type ProposeSlotItem = z.infer<typeof proposeSlotItemSchema>;

/** POST /v2/propose-slots レスポンス (ランキング済み候補リスト)。 */
export const proposeSlotsResponseSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  candidate_lat: z.number().nullish(),
  candidate_lng: z.number().nullish(),
  resolved_office_id: z.string().uuid().nullish(),
  slots: z.array(proposeSlotItemSchema).default([]),
  message: z.string().nullish(),
});
export type ProposeSlotsResponse = z.infer<typeof proposeSlotsResponseSchema>;

export type { WeekdayCode };
