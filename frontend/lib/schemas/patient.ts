/**
 * Patient zod schemas — **v2 schema 経由の re-export レイヤ** (W1-FE1).
 *
 * 設計仕様書 v0.9 §4.1 / 実装手順書 v0.2 §2 W1-FE1 に基づき、
 * v2 の `frontend/lib/schemas/v2/patient.ts` を上位の "Patient" 型として
 * 公開する。既存の import 箇所（`from '@/lib/schemas/patient'`）は変更不要に
 * できるよう、旧型名（`PatientRead` / `PatientCreate` / `patientCreateSchema`
 * など）と互換 alias を保つ。
 *
 * 削除済み (v2 schema には含めない / UI からも消える):
 *   age, ng_time_start, ng_time_end, required_staff_count, area,
 *   ng_staff_ids, preferred_staff_ids, specified_type, continuous_request,
 *   weekday_priority, ng_weekdays
 *
 * 追加 (§3.3 / §3.4):
 *   - weekly_pattern.entries[].staff_count (1 or 2)
 *   - special_weekly_pattern (JSONB, 同形)
 *   - special_week_active (適用週リスト [{iso_year, iso_week}, ...])
 */
import { z } from 'zod';

import {
  PATIENT_STATUS_V2_VALUES,
  SEX_RESTRICTION_V2_VALUES,
  SEX_V2_VALUES,
  TIME_TYPE_V2_VALUES,
  VISIT_FREQUENCY_V2_VALUES,
  WEEKDAY_V2_VALUES,
  patientV2BaseSchema,
  patientV2CreateSchema,
  patientV2ReadSchema,
  patientV2UpdateSchema,
  weeklyPatternEntryV2Schema,
  weeklyPatternV2Schema,
  specialWeekRefV2Schema,
  type PatientV2Base,
  type PatientV2Create,
  type PatientV2Read,
  type PatientV2Update,
  type SpecialWeekRefV2,
  type TimeTypeV2,
  type VisitFrequencyV2,
  type WeeklyPatternEntryV2,
  type WeeklyPatternV2,
  type WeekdayV2,
} from './v2/patient';

// ---------------------------------------------------------------------------
// v2 schema / 型の re-export (移行用 alias)
// ---------------------------------------------------------------------------

export {
  patientV2BaseSchema as patientBaseSchema,
  patientV2CreateSchema as patientCreateSchema,
  patientV2UpdateSchema as patientUpdateSchema,
  patientV2ReadSchema as patientReadSchema,
  weeklyPatternV2Schema,
  weeklyPatternEntryV2Schema,
  specialWeekRefV2Schema,
};

export type PatientBase = PatientV2Base;
export type PatientCreate = PatientV2Create;
export type PatientUpdate = PatientV2Update;
export type PatientRead = PatientV2Read;
export type SpecialWeekRef = SpecialWeekRefV2;
export type WeeklyPatternEntry = WeeklyPatternEntryV2;
export type { WeeklyPatternV2 };

// ---------------------------------------------------------------------------
// UI ラベル/オプション定数
// v2 のサーバ側 enum (英語小文字) ↔ UI 表示 (日本語) のマッピング。
// ---------------------------------------------------------------------------

export const SEX_VALUES = SEX_V2_VALUES;
export const SEX_LABELS_JA: Record<(typeof SEX_VALUES)[number], string> = {
  male: '男性',
  female: '女性',
  unknown: '不明',
};
/** 既存ページ互換: v1 では日本語ラベル配列だったが v2 は enum 値配列。 */
export const SEX_OPTIONS = SEX_VALUES;

export const INSURANCE_VALUES = ['medical', 'care'] as const;
export const INSURANCE_LABELS_JA: Record<(typeof INSURANCE_VALUES)[number], string> = {
  medical: '医療保険',
  care: '介護保険',
};
/** 既存ページ互換: v1 では `['医療保険','介護保険']` だった。
 *  v2 では enum (`medical|care`) を選び、表示は `INSURANCE_LABELS_JA` 経由。 */
export const INSURANCE_OPTIONS = INSURANCE_VALUES;

export const SEX_RESTRICTION_VALUES = SEX_RESTRICTION_V2_VALUES;
export const SEX_RESTRICTION_LABELS_JA: Record<(typeof SEX_RESTRICTION_VALUES)[number], string> = {
  female_only: '女性のみ',
  male_only: '男性のみ',
};
export const SEX_RESTRICTION_OPTIONS = SEX_RESTRICTION_VALUES;

export const STATUS_VALUES = PATIENT_STATUS_V2_VALUES;
export const STATUS_LABELS_JA: Record<(typeof STATUS_VALUES)[number], string> = {
  active: '稼働中',
  suspended: '一時休止',
  admitted: '入院中',
  pending: '開始前',
  cancelled: '解約済み',
};
export const STATUS_OPTIONS = STATUS_VALUES;

// ---------------------------------------------------------------------------
// WeeklyPattern (UI 層用)
// ---------------------------------------------------------------------------

export const WEEKDAY_KEYS = WEEKDAY_V2_VALUES;
export type WeekdayKey = WeekdayV2;

export const WEEKDAY_LABELS_JA: Record<WeekdayKey, string> = {
  Mon: '月',
  Tue: '火',
  Wed: '水',
  Thu: '木',
  Fri: '金',
  Sat: '土',
  Sun: '日',
};

export const VISIT_FREQUENCY_OPTIONS = VISIT_FREQUENCY_V2_VALUES;
export const VISIT_FREQUENCY_LABELS: Record<(typeof VISIT_FREQUENCY_OPTIONS)[number], string> = {
  every: '毎週',
  biweekly: '隔週',
  monthly: '月次',
};

export const TIME_TYPE_OPTIONS = TIME_TYPE_V2_VALUES;

export type TimeType = TimeTypeV2;
export type VisitFrequency = VisitFrequencyV2;

/**
 * 曜日ごとの 1 エントリ (UI 編集用). サーバ送信時は
 * `weeklyPatternV2Schema.entries[]` に入る。
 *
 * §3.3: staff_count = 2 で「2 名体制」を表現。
 */
export interface WeeklyPatternEntryUI {
  weekday: WeekdayKey;
  /** 当該曜日に訪問するか (false 時は `entries` から除外して保存) */
  enabled: boolean;
  staff_count: 1 | 2;
  time_type: TimeType;
  preferred_start: string | null;
  preferred_end: string | null;
  service_minutes: number | null;
}

/** UI 上の WeeklyPattern. v1 から weekday_priority / ng_weekdays を取り除き、
 *  entries[] (曜日ごと staff_count) を追加。 */
export interface WeeklyPattern {
  frequency_per_week: number;
  visit_frequency: VisitFrequency | null;
  visit_weeks: string | null;
  preferred_weekdays: WeekdayKey[];
  service_minutes: number;
  time_type: TimeType;
  preferred_start: string | null;
  preferred_end: string | null;
  /** 曜日ごとの staff_count 切替 (§3.3). enabled=true の曜日のみ送信時に flatten。 */
  entries: WeeklyPatternEntryUI[];
}

/** 全曜日の初期 entry (enabled=false / staff_count=1). */
export function makeDefaultEntries(): WeeklyPatternEntryUI[] {
  return WEEKDAY_KEYS.map((d) => ({
    weekday: d,
    enabled: false,
    staff_count: 1,
    time_type: '終日',
    preferred_start: null,
    preferred_end: null,
    service_minutes: null,
  }));
}

export const emptyWeeklyPattern: WeeklyPattern = {
  frequency_per_week: 1,
  visit_frequency: null,
  visit_weeks: null,
  preferred_weekdays: [],
  service_minutes: 30,
  time_type: '終日',
  preferred_start: null,
  preferred_end: null,
  entries: makeDefaultEntries(),
};

// ---------------------------------------------------------------------------
// react-hook-form 用 form schema / values
// ---------------------------------------------------------------------------

const optionalNullableString = z
  .string()
  .trim()
  .optional()
  .transform((v) => (v === '' ? undefined : v));

const optionalEnum = <T extends readonly [string, ...string[]]>(values: T) =>
  z
    .union([z.enum(values as unknown as [string, ...string[]]), z.literal('')])
    .optional()
    .transform((v) => (v === '' || v === undefined ? undefined : (v as T[number])));

/**
 * patientFormSchema — react-hook-form が直接 bind する shape.
 *
 * `weekly_pattern` / `special_weekly_pattern` は `WeeklyPatternEditor` が
 * 構造化 dict を出力するため record として受ける（zod transform は通さず、
 * 送信前に `weeklyPatternToWire` で v2 dict に直す）。
 */
export const patientFormSchema = z.object({
  code: z.string().min(1, 'コードは必須です').max(64),
  name: z.string().min(1, '氏名は必須です').max(120),
  kana: optionalNullableString,
  sex: optionalEnum(SEX_VALUES),
  status: z.enum(STATUS_VALUES as unknown as [string, ...string[]]).default('active'),
  insurance: optionalEnum(INSURANCE_VALUES),
  address: optionalNullableString,
  lat: z.preprocess(
    (v) => (v === '' || v === null || v === undefined ? undefined : v),
    z.coerce.number().min(-90).max(90).optional(),
  ),
  lng: z.preprocess(
    (v) => (v === '' || v === null || v === undefined ? undefined : v),
    z.coerce.number().min(-180).max(180).optional(),
  ),
  primary_office_id: z
    .union([z.string().uuid(), z.literal('')])
    .optional()
    .transform((v) => (v === '' || v === undefined ? undefined : v)),
  sex_restriction: optionalEnum(SEX_RESTRICTION_VALUES),
  note: optionalNullableString,
  weekly_pattern: z.record(z.unknown()).optional(),
  special_weekly_pattern: z.record(z.unknown()).optional(),
  special_week_enabled: z.boolean().optional().default(false),
  special_week_active_input: z.string().optional().default(''),
});

/**
 * react-hook-form 入力型. HTML inputs は文字列で bind するため数値・boolean は
 * 文字列で表現し、送信時に coerce する。
 */
export type PatientFormValues = {
  code: string;
  name: string;
  kana: string;
  sex: '' | (typeof SEX_VALUES)[number];
  status: (typeof STATUS_VALUES)[number];
  insurance: '' | (typeof INSURANCE_VALUES)[number];
  address: string;
  lat: string;
  lng: string;
  primary_office_id: string;
  sex_restriction: '' | (typeof SEX_RESTRICTION_VALUES)[number];
  note: string;
  weekly_pattern: WeeklyPattern;
  special_weekly_pattern: WeeklyPattern;
  /** 特別訪問週間 ON/OFF (§3.4 簡素実装) */
  special_week_enabled: boolean;
  /** 適用週入力 (例 "2026-W18, 2026-W19"). 送信時に正規化。 */
  special_week_active_input: string;
};

export const emptyPatientFormValues: PatientFormValues = {
  code: '',
  name: '',
  kana: '',
  sex: '',
  status: 'active',
  insurance: '',
  address: '',
  lat: '',
  lng: '',
  primary_office_id: '',
  sex_restriction: '',
  note: '',
  weekly_pattern: emptyWeeklyPattern,
  special_weekly_pattern: emptyWeeklyPattern,
  special_week_enabled: false,
  special_week_active_input: '',
};

// ---------------------------------------------------------------------------
// JSONB <-> UI 変換ヘルパ
// ---------------------------------------------------------------------------

const isWeekday = (v: unknown): v is WeekdayKey =>
  typeof v === 'string' && (WEEKDAY_KEYS as readonly string[]).includes(v);

const filterWeekdays = (arr: unknown): WeekdayKey[] =>
  Array.isArray(arr) ? arr.filter(isWeekday) : [];

const isTimeType = (v: unknown): v is TimeType =>
  typeof v === 'string' && (TIME_TYPE_OPTIONS as readonly string[]).includes(v);

const isVisitFrequency = (v: unknown): v is VisitFrequency =>
  typeof v === 'string' && (VISIT_FREQUENCY_OPTIONS as readonly string[]).includes(v);

/**
 * Coerce a raw `weekly_pattern` JSONB blob into a structured `WeeklyPattern`.
 * Unknown / missing keys fall back to `emptyWeeklyPattern` defaults so the
 * editor always has a valid shape to bind to.
 */
export function coerceWeeklyPattern(raw: unknown): WeeklyPattern {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return {
      ...emptyWeeklyPattern,
      entries: makeDefaultEntries(),
    };
  }
  const r = raw as Record<string, unknown>;

  const freq = Number(r.frequency_per_week);
  const minutes = Number(r.service_minutes);

  const visitFreq = isVisitFrequency(r.visit_frequency) ? r.visit_frequency : null;
  const timeType = isTimeType(r.time_type) ? r.time_type : '終日';

  const entries = makeDefaultEntries();
  if (Array.isArray(r.entries)) {
    for (const e of r.entries) {
      if (!e || typeof e !== 'object') continue;
      const o = e as Record<string, unknown>;
      const day = isWeekday(o.weekday) ? o.weekday : null;
      if (!day) continue;
      const idx = WEEKDAY_KEYS.indexOf(day);
      const sc = Number(o.staff_count);
      const sm = Number(o.service_minutes);
      entries[idx] = {
        weekday: day,
        enabled: true,
        staff_count: sc === 2 ? 2 : 1,
        time_type: isTimeType(o.time_type) ? o.time_type : timeType,
        preferred_start:
          typeof o.preferred_start === 'string' && o.preferred_start ? o.preferred_start : null,
        preferred_end:
          typeof o.preferred_end === 'string' && o.preferred_end ? o.preferred_end : null,
        service_minutes: Number.isFinite(sm) ? sm : null,
      };
    }
  }

  return {
    frequency_per_week: Number.isFinite(freq) ? Math.min(7, Math.max(1, freq)) : 1,
    visit_frequency: visitFreq,
    visit_weeks: typeof r.visit_weeks === 'string' ? r.visit_weeks : null,
    preferred_weekdays: filterWeekdays(r.preferred_weekdays),
    service_minutes: Number.isFinite(minutes) ? Math.min(180, Math.max(1, minutes)) : 30,
    time_type: timeType,
    preferred_start:
      typeof r.preferred_start === 'string' && r.preferred_start ? r.preferred_start : null,
    preferred_end: typeof r.preferred_end === 'string' && r.preferred_end ? r.preferred_end : null,
    entries,
  };
}

/**
 * UI の `WeeklyPattern` を v2 サーバ送信用 dict に直す。
 * `entries[]` は enabled=true のエントリのみ送る。
 */
export function weeklyPatternToWire(wp: WeeklyPattern): Record<string, unknown> {
  return {
    frequency_per_week: wp.frequency_per_week,
    visit_frequency: wp.visit_frequency ?? null,
    visit_weeks: wp.visit_weeks ?? null,
    preferred_weekdays: wp.preferred_weekdays.length > 0 ? wp.preferred_weekdays : null,
    service_minutes: wp.service_minutes,
    time_type: wp.time_type,
    preferred_start: wp.preferred_start ?? null,
    preferred_end: wp.preferred_end ?? null,
    entries: wp.entries
      .filter((e) => e.enabled)
      .map((e) => ({
        weekday: e.weekday,
        staff_count: e.staff_count,
        time_type: e.time_type,
        preferred_start: e.preferred_start ?? null,
        preferred_end: e.preferred_end ?? null,
        service_minutes: e.service_minutes != null ? e.service_minutes : wp.service_minutes,
      })),
  };
}

/**
 * special_week_active_input ("2026-W18, 2026-W19") を
 * `[{iso_year, iso_week}, ...]` に正規化する。
 *
 * 受け付ける形式:
 *   - "2026-W18, 2026-W19" (ISO week date 風)
 *   - "2026-18 2026-19"
 *   - "2026/18,2026/19"
 *
 * 不正トークンは黙って落とす。
 */
const ISO_WEEK_TOKEN_RE = /(\d{4})[-\/W ]+W?(\d{1,2})/g;

export function parseSpecialWeekInput(input: string): SpecialWeekRefV2[] {
  const out: SpecialWeekRefV2[] = [];
  if (!input) return out;
  const seen = new Set<string>();
  for (const m of input.matchAll(ISO_WEEK_TOKEN_RE)) {
    const y = Number(m[1]);
    const w = Number(m[2]);
    if (!Number.isInteger(y) || !Number.isInteger(w)) continue;
    if (y < 2000 || y > 2100) continue;
    if (w < 1 || w > 53) continue;
    const key = `${y}-${w}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ iso_year: y, iso_week: w });
  }
  return out;
}

/** SpecialWeekRef[] → 表示・編集用テキスト ("2026-W18, 2026-W19"). */
export function formatSpecialWeekInput(
  refs: ReadonlyArray<SpecialWeekRef> | null | undefined,
): string {
  if (!refs || refs.length === 0) return '';
  return refs.map((r) => `${r.iso_year}-W${String(r.iso_week).padStart(2, '0')}`).join(', ');
}

/** Map a server `PatientRead` into form-friendly string values. */
export function patientReadToFormValues(p: PatientRead): PatientFormValues {
  const refs: SpecialWeekRef[] = (p.special_week_active ?? [])
    .map((r) => {
      const o = r as Record<string, unknown>;
      const y = Number(o.iso_year);
      const w = Number(o.iso_week);
      if (!Number.isInteger(y) || !Number.isInteger(w)) return null;
      return { iso_year: y, iso_week: w } as SpecialWeekRef;
    })
    .filter((x): x is SpecialWeekRef => x !== null);

  return {
    code: p.code,
    name: p.name,
    kana: p.kana ?? '',
    sex: (p.sex as PatientFormValues['sex']) ?? '',
    status: (p.status as PatientFormValues['status']) ?? 'active',
    insurance: (p.insurance as PatientFormValues['insurance']) ?? '',
    address: p.address ?? '',
    lat: p.lat !== undefined && p.lat !== null ? String(p.lat) : '',
    lng: p.lng !== undefined && p.lng !== null ? String(p.lng) : '',
    primary_office_id: p.primary_office_id ?? '',
    sex_restriction: (p.sex_restriction as PatientFormValues['sex_restriction']) ?? '',
    note: p.note ?? '',
    weekly_pattern: coerceWeeklyPattern(p.weekly_pattern),
    special_weekly_pattern: coerceWeeklyPattern(p.special_weekly_pattern),
    special_week_enabled: refs.length > 0,
    special_week_active_input: formatSpecialWeekInput(refs),
  };
}
