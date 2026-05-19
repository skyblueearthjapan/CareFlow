/**
 * PatientV2 / WeeklyPatternV2 zod schemas (Wave 0-C).
 *
 * 設計仕様書 v0.9 §4.1 に基づき、v2 で残す **16 項目** のみを保持する。
 * v1 で削除する 10 項目 (年齢 / NG時間 / エリア / 指定タイプ / NGスタッフ /
 * 同行希望スタッフ / 継続要望 / 必要スタッフ数 / 曜日優先度 / NG曜日) は
 * v2 schema には含めない。
 *
 * 追加項目 (§3.3 / §3.4):
 *   - weekly_pattern.staff_count (既定 1, 2 で 2 名体制)
 *   - special_weekly_pattern (JSONB)
 *   - special_week_active (適用週リスト [{iso_year, iso_week}, ...])
 *
 * Backend `backend/app/schemas/v2/patient.py` の Pydantic schema と
 * **完全一致** させる。
 */
import { z } from 'zod';

// ─────────────────────────────────────────────────────────────────────────
// Constants & primitive enums
// ─────────────────────────────────────────────────────────────────────────

/** 7 weekday short codes (Mon..Sun, English short form). */
export const WEEKDAY_V2_VALUES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const;
export const weekdayV2Enum = z.enum(WEEKDAY_V2_VALUES);
export type WeekdayV2 = z.infer<typeof weekdayV2Enum>;

/**
 * 時間タイプ (§4.1):
 *   固定 / 時間帯 (preferred_start / preferred_end 必須)
 *   午前 / 午後 / 終日 (時刻不要)
 */
export const TIME_TYPE_V2_VALUES = ['固定', '時間帯', '午前', '午後', '終日'] as const;
export const timeTypeV2Enum = z.enum(TIME_TYPE_V2_VALUES);
export type TimeTypeV2 = z.infer<typeof timeTypeV2Enum>;

/**
 * 訪問頻度 (§4.1 / §7.1 残置確定): 毎週 / 隔週 / 月次。
 * 具体的な使い方は別途検討 (TBD)。
 */
export const VISIT_FREQUENCY_V2_VALUES = ['every', 'biweekly', 'monthly'] as const;
export const visitFrequencyV2Enum = z.enum(VISIT_FREQUENCY_V2_VALUES);
export type VisitFrequencyV2 = z.infer<typeof visitFrequencyV2Enum>;

/** 性別 (§4.1). 保存値は英語小文字。UI 表示は ja-JP に変換する。 */
export const SEX_V2_VALUES = ['male', 'female', 'unknown'] as const;
export const sexV2Enum = z.enum(SEX_V2_VALUES);
export type SexV2 = z.infer<typeof sexV2Enum>;

/**
 * 性別制限 (§4.1, ハード制約):
 *   "female_only" = 女性スタッフのみ可
 *   "male_only" = 男性スタッフのみ可
 *   null = 制限なし
 */
export const SEX_RESTRICTION_V2_VALUES = ['female_only', 'male_only'] as const;
export const sexRestrictionV2Enum = z.enum(SEX_RESTRICTION_V2_VALUES);
export type SexRestrictionV2 = z.infer<typeof sexRestrictionV2Enum>;

/**
 * 患者状態 (§4.1):
 *   active = 稼働中 (割当対象)
 *   suspended = 一時休止
 *   admitted = 入院中
 *   pending = 開始前
 *   cancelled = 解約済み
 */
export const PATIENT_STATUS_V2_VALUES = [
  'active',
  'suspended',
  'admitted',
  'pending',
  'cancelled',
] as const;
export const patientStatusV2Enum = z.enum(PATIENT_STATUS_V2_VALUES);
export type PatientStatusV2 = z.infer<typeof patientStatusV2Enum>;

/** 保険区分 (§4.1). */
export const INSURANCE_V2_VALUES = ['medical', 'care'] as const;
export const insuranceV2Enum = z.enum(INSURANCE_V2_VALUES);
export type InsuranceV2 = z.infer<typeof insuranceV2Enum>;

// ─────────────────────────────────────────────────────────────────────────
// HH:MM validator
// ─────────────────────────────────────────────────────────────────────────
const hhmmRegex = /^([01]\d|2[0-3]):[0-5]\d$/;
const hhmmSchema = z.string().regex(hhmmRegex, '時刻は HH:MM 形式で入力してください');

// ─────────────────────────────────────────────────────────────────────────
// WeeklyPattern (§3.3 / §4.1)
// ─────────────────────────────────────────────────────────────────────────

/** 希望訪問パターンの 1 曜日エントリ (§3.3 / §4.1). */
export const weeklyPatternEntryV2Schema = z
  .object({
    weekday: weekdayV2Enum.nullable().optional(),
    time_type: timeTypeV2Enum.nullable().optional(),
    preferred_start: hhmmSchema.nullable().optional(),
    preferred_end: hhmmSchema.nullable().optional(),
    service_minutes: z.number().int().min(0).max(300).nullable().optional(),
    /** 必要スタッフ数 (§3.3). 既定 1, 2 で 2 名体制 */
    staff_count: z.number().int().min(1).max(2).default(1),
  })
  .passthrough();
export type WeeklyPatternEntryV2 = z.infer<typeof weeklyPatternEntryV2Schema>;

/**
 * 患者の希望訪問パターン (§4.1 / §3.3).
 *
 * JSONB として `patients.weekly_pattern` に保存される。
 * 通常パターンと特別週パターン (`special_weekly_pattern`) で同じ shape を共有。
 */
export const weeklyPatternV2Schema = z
  .object({
    /** 週あたり訪問回数 (§4.1 核項目) */
    frequency_per_week: z.number().int().min(1).max(7).nullable().optional(),
    /** 訪問頻度 (§7.1 残置確定, 具体仕様 TBD) */
    visit_frequency: visitFrequencyV2Enum.nullable().optional(),
    /** 訪問週 (例 "1,3", §7.1 残置確定, 具体仕様 TBD) */
    visit_weeks: z.string().nullable().optional(),
    preferred_weekdays: z.array(weekdayV2Enum).nullable().optional(),
    service_minutes: z.number().int().min(0).max(300).nullable().optional(),
    time_type: timeTypeV2Enum.nullable().optional(),
    preferred_start: hhmmSchema.nullable().optional(),
    preferred_end: hhmmSchema.nullable().optional(),
    /** 曜日ごとの細粒度設定 (§3.3 staff_count 等). リスト形式運用時に使用 */
    entries: z.array(weeklyPatternEntryV2Schema).nullable().optional(),
  })
  .passthrough();
export type WeeklyPatternV2 = z.infer<typeof weeklyPatternV2Schema>;

/** special_week_active の 1 要素 (§3.4 / §4.1). */
export const specialWeekRefV2Schema = z.object({
  iso_year: z.number().int().min(2000).max(2100),
  iso_week: z.number().int().min(1).max(53),
});
export type SpecialWeekRefV2 = z.infer<typeof specialWeekRefV2Schema>;

// ─────────────────────────────────────────────────────────────────────────
// PatientV2 base / Create / Update / Read
// ─────────────────────────────────────────────────────────────────────────

/**
 * 患者マスタ v2 (§4.1 残す 16 項目).
 *
 * 削除済み (v2 schema に含めない): age, ng_time_start, ng_time_end,
 * required_staff_count, area, ng_staff_ids, preferred_staff_ids,
 * specified_type, continuous_request, weekday_priority。
 */
export const patientV2BaseSchema = z.object({
  // 基本情報
  code: z.string().min(1, 'コードは必須です').max(64),
  name: z.string().min(1, '氏名は必須です').max(120),
  kana: z.string().max(120).nullable().optional(),
  sex: sexV2Enum.nullable().optional(),
  status: patientStatusV2Enum.default('active'),

  // 連絡先
  address: z.string().nullable().optional(),
  lat: z.number().min(-90).max(90).nullable().optional(),
  lng: z.number().min(-180).max(180).nullable().optional(),

  // 保険・拠点
  insurance: insuranceV2Enum.nullable().optional(),
  primary_office_id: z.string().uuid().nullable().optional(),

  // 訪問条件 (ハード制約)
  sex_restriction: sexRestrictionV2Enum.nullable().optional(),

  // 希望訪問パターン (§4.1 核)
  weekly_pattern: weeklyPatternV2Schema.nullable().optional(),
  special_weekly_pattern: weeklyPatternV2Schema.nullable().optional(),
  special_week_active: z.array(specialWeekRefV2Schema).default([]),

  // 備考
  note: z.string().nullable().optional(),
});
export type PatientV2Base = z.infer<typeof patientV2BaseSchema>;

/** POST /api/v1/patients リクエスト (W1-BE1). */
export const patientV2CreateSchema = patientV2BaseSchema;
export type PatientV2Create = z.infer<typeof patientV2CreateSchema>;

/**
 * PATCH /api/v1/patients/{id} リクエスト (W1-BE1).
 *
 * 全フィールド optional. 手書き列挙 (`.partial()` 不使用)。
 * デフォルト値が PATCH ペイロードに混入してサーバ値を黙って上書きするのを防ぐため。
 */
export const patientV2UpdateSchema = z.object({
  code: z.string().min(1).max(64).optional(),
  name: z.string().min(1).max(120).optional(),
  kana: z.string().max(120).nullable().optional(),
  sex: sexV2Enum.nullable().optional(),
  status: patientStatusV2Enum.optional(),
  address: z.string().nullable().optional(),
  lat: z.number().min(-90).max(90).nullable().optional(),
  lng: z.number().min(-180).max(180).nullable().optional(),
  insurance: insuranceV2Enum.nullable().optional(),
  primary_office_id: z.string().uuid().nullable().optional(),
  sex_restriction: sexRestrictionV2Enum.nullable().optional(),
  weekly_pattern: weeklyPatternV2Schema.nullable().optional(),
  special_weekly_pattern: weeklyPatternV2Schema.nullable().optional(),
  special_week_active: z.array(specialWeekRefV2Schema).optional(),
  note: z.string().nullable().optional(),
});
export type PatientV2Update = z.infer<typeof patientV2UpdateSchema>;

/**
 * GET /api/v1/patients/{id} レスポンス (W1-BE1).
 *
 * Read 経路は JSONB を緩く受ける (asymmetric 戦略: 書込みは構造化、読込は dict)。
 * weekly_pattern / special_weekly_pattern は dict のまま受け、
 * `coerceWeeklyPattern` 等で UI 用に変換する。
 */
export const patientV2ReadSchema = patientV2BaseSchema.extend({
  id: z.string().uuid(),
  created_at: z.string(),
  updated_at: z.string(),
  deleted_at: z.string().nullable().optional(),
  // asymmetric: 読込は dict のまま受ける
  weekly_pattern: z.record(z.unknown()).nullable().optional(),
  special_weekly_pattern: z.record(z.unknown()).nullable().optional(),
  special_week_active: z.array(z.record(z.unknown())).default([]),
});
export type PatientV2Read = z.infer<typeof patientV2ReadSchema>;
