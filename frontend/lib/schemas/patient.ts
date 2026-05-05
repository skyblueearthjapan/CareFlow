/**
 * Patient zod schemas — mirrors backend `app/schemas/patient.py`
 * (Read / Create / Update).
 *
 * Notes
 * ─────
 * - `sex` / `insurance` / `sex_restriction` are stored as plain strings on
 *   the backend (`str | None`). We tighten them with zod enums on the FE so
 *   form selects can be driven from `*_OPTIONS` constants.
 * - `weekly_pattern` / `special_week` map to JSONB columns on the backend
 *   (`dict | None`). The form captures `weekly_pattern` as a structured dict
 *   via `WeeklyPatternEditor` (W3-C); `special_week` is also a structured dict.
 * - W3-A additions (`area`, `ng_staff_ids`, `preferred_staff_ids`,
 *   `specified_type`, `continuous_request`) are present on the SQLAlchemy
 *   model but the pydantic schemas may not yet surface them — keep all five
 *   nullable/optional on the FE until the backend pass exposes them.
 */
import { z } from 'zod';

export const SEX_OPTIONS = ['男性', '女性'] as const;
export const INSURANCE_OPTIONS = ['医療保険', '介護保険'] as const;
export const SEX_RESTRICTION_OPTIONS = ['女性のみ', '男性のみ', 'なし'] as const;
export const STATUS_OPTIONS = ['active', 'inactive'] as const;
export const SPECIFIED_TYPE_OPTIONS = ['必須', '同じ人希望', '最初は希望'] as const;

export const sexEnum = z.enum(SEX_OPTIONS);
export const insuranceEnum = z.enum(INSURANCE_OPTIONS);
export const sexRestrictionEnum = z.enum(SEX_RESTRICTION_OPTIONS);
export const statusEnum = z.enum(STATUS_OPTIONS);
export const specifiedTypeEnum = z.enum(SPECIFIED_TYPE_OPTIONS);

/** HH:MM (24h) — backend stores `time` (Python). */
const timeStringSchema = z
  .string()
  .regex(/^([01]\d|2[0-3]):[0-5]\d$/, '時刻は HH:MM 形式で入力してください');

const optionalNullableString = z
  .string()
  .trim()
  .optional()
  .transform((v) => (v === '' ? undefined : v));

const optionalTime = z
  .union([timeStringSchema, z.literal('')])
  .optional()
  .transform((v) => (v === '' || v === undefined ? undefined : v));

const optionalEnum = <T extends readonly [string, ...string[]]>(values: T) =>
  z
    .union([z.enum(values as unknown as [string, ...string[]]), z.literal('')])
    .optional()
    .transform((v) => (v === '' || v === undefined ? undefined : (v as T[number])));

// ---------------------------------------------------------------------------
// Structured weekly_pattern (W3-C)
// ---------------------------------------------------------------------------

export const WEEKDAY_KEYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const;
export type WeekdayKey = (typeof WEEKDAY_KEYS)[number];

export const WEEKDAY_LABELS_JA: Record<WeekdayKey, string> = {
  Mon: '月',
  Tue: '火',
  Wed: '水',
  Thu: '木',
  Fri: '金',
  Sat: '土',
  Sun: '日',
};

export const VISIT_FREQUENCY_OPTIONS = ['every', 'biweekly', 'monthly'] as const;
export const VISIT_FREQUENCY_LABELS: Record<
  (typeof VISIT_FREQUENCY_OPTIONS)[number],
  string
> = {
  every: '毎週',
  biweekly: '隔週',
  monthly: '月次',
};

export const WEEKDAY_PRIORITY_OPTIONS = ['高', '中', '低'] as const;
export const TIME_TYPE_OPTIONS = ['固定', '午前', '午後', '終日', '時間帯'] as const;

export interface WeeklyPattern {
  frequency_per_week: number;
  visit_frequency: (typeof VISIT_FREQUENCY_OPTIONS)[number] | null;
  visit_weeks: string | null;
  preferred_weekdays: WeekdayKey[];
  weekday_priority: (typeof WEEKDAY_PRIORITY_OPTIONS)[number];
  service_minutes: number;
  time_type: (typeof TIME_TYPE_OPTIONS)[number];
  preferred_start: string | null;
  preferred_end: string | null;
  ng_weekdays: WeekdayKey[] | null;
}

export const emptyWeeklyPattern: WeeklyPattern = {
  frequency_per_week: 1,
  visit_frequency: null,
  visit_weeks: null,
  preferred_weekdays: [],
  weekday_priority: '中',
  service_minutes: 30,
  time_type: '終日',
  preferred_start: null,
  preferred_end: null,
  ng_weekdays: null,
};

/** Shared base — fields common to Create/Read/Update. */
export const patientBaseSchema = z.object({
  code: z.string().min(1, 'コードは必須です').max(64),
  name: z.string().min(1, '氏名は必須です').max(120),
  kana: optionalNullableString,
  sex: optionalEnum(SEX_OPTIONS),
  age: z
    .union([z.coerce.number().int().min(0).max(150), z.literal('')])
    .optional()
    .transform((v) => (v === '' || v === undefined ? undefined : (v as number))),
  status: statusEnum.default('active'),
  insurance: optionalEnum(INSURANCE_OPTIONS),
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
  required_staff_count: z.coerce.number().int().min(1).max(10).default(1),
  sex_restriction: optionalEnum(SEX_RESTRICTION_OPTIONS),
  ng_time_start: optionalTime,
  ng_time_end: optionalTime,
  note: optionalNullableString,
  weekly_pattern: z.record(z.unknown()).nullish(),
  special_week: z.record(z.unknown()).nullish(),
  // TODO: W3-A backend pydantic 拡張完了後に nullable() を必須化検討
  area: z.string().nullable().optional(),
  ng_staff_ids: z.array(z.string().uuid()).nullable().optional().default([]),
  preferred_staff_ids: z.array(z.string().uuid()).nullable().optional().default([]),
  specified_type: z
    .union([specifiedTypeEnum, z.literal('')])
    .nullable()
    .optional()
    .transform((v) =>
      v === '' || v === undefined || v === null
        ? undefined
        : (v as (typeof SPECIFIED_TYPE_OPTIONS)[number]),
    ),
  continuous_request: z.boolean().default(false),
});

export const patientCreateSchema = patientBaseSchema;

/**
 * Update: all fields optional. Hand-written (not `.partial()`) so that the
 * `status` / `required_staff_count` defaults on the base schema do NOT leak
 * into PATCH payloads (which would silently overwrite server values).
 */
export const patientUpdateSchema = z.object({
  code: z.string().min(1).max(64).optional(),
  name: z.string().min(1).max(120).optional(),
  kana: optionalNullableString,
  sex: optionalEnum(SEX_OPTIONS),
  age: z
    .union([z.coerce.number().int().min(0).max(150), z.literal('')])
    .optional()
    .transform((v) => (v === '' || v === undefined ? undefined : (v as number))),
  status: z
    .union([statusEnum, z.literal('')])
    .optional()
    .transform((v) =>
      v === '' || v === undefined ? undefined : (v as (typeof STATUS_OPTIONS)[number]),
    ),
  insurance: optionalEnum(INSURANCE_OPTIONS),
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
  required_staff_count: z
    .union([z.coerce.number().int().min(1).max(10), z.literal('')])
    .optional()
    .transform((v) => (v === '' || v === undefined ? undefined : (v as number))),
  sex_restriction: optionalEnum(SEX_RESTRICTION_OPTIONS),
  ng_time_start: optionalTime,
  ng_time_end: optionalTime,
  note: optionalNullableString,
  weekly_pattern: z.record(z.unknown()).nullish(),
  special_week: z.record(z.unknown()).nullish(),
  // TODO: W3-A backend pydantic 拡張完了後に挙動再確認
  area: z.string().nullable().optional(),
  ng_staff_ids: z.array(z.string().uuid()).nullable().optional(),
  preferred_staff_ids: z.array(z.string().uuid()).nullable().optional(),
  specified_type: z
    .union([specifiedTypeEnum, z.literal('')])
    .nullable()
    .optional()
    .transform((v) =>
      v === '' || v === undefined || v === null
        ? undefined
        : (v as (typeof SPECIFIED_TYPE_OPTIONS)[number]),
    ),
  continuous_request: z.boolean().optional(),
});

/** Read: server response. Includes server-generated fields. */
export const patientReadSchema = patientBaseSchema.extend({
  id: z.string().uuid(),
  created_at: z.string(),
  updated_at: z.string(),
  deleted_at: z.string().nullable().optional(),
});

/**
 * Form-input schema — matches the actual shape that react-hook-form binds
 * to (`weekly_pattern` is a structured dict via WeeklyPatternEditor,
 * `special_week` is a checkbox boolean). The submit handler converts these
 * into the `dict | null` payload expected by `patientCreateSchema` /
 * `patientUpdateSchema`.
 *
 * Defined separately from `patientCreateSchema` so that resolver validation
 * does not reject the structured/checkbox values before our custom parsing
 * runs.
 */
export const patientFormSchema = patientBaseSchema
  .omit({ weekly_pattern: true, special_week: true })
  .extend({
    weekly_pattern: z.record(z.unknown()).optional(),
    special_week: z.boolean().optional().default(false),
  });

export type PatientCreate = z.infer<typeof patientCreateSchema>;
export type PatientUpdate = z.infer<typeof patientUpdateSchema>;
export type PatientRead = z.infer<typeof patientReadSchema>;

/**
 * react-hook-form input type — fields can be empty strings before zod
 * coercion runs. Used as `useForm<PatientFormValues>` so HTML inputs bind
 * cleanly without `as any`.
 */
export type PatientFormValues = {
  code: string;
  name: string;
  kana: string;
  sex: '' | (typeof SEX_OPTIONS)[number];
  age: string;
  status: (typeof STATUS_OPTIONS)[number];
  insurance: '' | (typeof INSURANCE_OPTIONS)[number];
  address: string;
  lat: string;
  lng: string;
  primary_office_id: string;
  required_staff_count: string;
  sex_restriction: '' | (typeof SEX_RESTRICTION_OPTIONS)[number];
  ng_time_start: string;
  ng_time_end: string;
  note: string;
  weekly_pattern: WeeklyPattern;
  special_week: boolean;
  // W3-A additions
  area: string;
  ng_staff_ids: string[];
  preferred_staff_ids: string[];
  specified_type: '' | (typeof SPECIFIED_TYPE_OPTIONS)[number];
  continuous_request: boolean;
};

export const emptyPatientFormValues: PatientFormValues = {
  code: '',
  name: '',
  kana: '',
  sex: '',
  age: '',
  status: 'active',
  insurance: '',
  address: '',
  lat: '',
  lng: '',
  primary_office_id: '',
  required_staff_count: '1',
  sex_restriction: '',
  ng_time_start: '',
  ng_time_end: '',
  note: '',
  weekly_pattern: emptyWeeklyPattern,
  special_week: false,
  area: '',
  ng_staff_ids: [],
  preferred_staff_ids: [],
  specified_type: '',
  continuous_request: false,
};

/**
 * Coerce an arbitrary JSONB blob (from server) into a structured
 * `WeeklyPattern`. Unknown / missing keys fall back to `emptyWeeklyPattern`
 * defaults so the editor always has a valid shape to bind to.
 */
export function coerceWeeklyPattern(raw: unknown): WeeklyPattern {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ...emptyWeeklyPattern };
  }
  const r = raw as Record<string, unknown>;
  const isWeekday = (v: unknown): v is WeekdayKey =>
    typeof v === 'string' && (WEEKDAY_KEYS as readonly string[]).includes(v);
  const filterWeekdays = (arr: unknown): WeekdayKey[] =>
    Array.isArray(arr) ? arr.filter(isWeekday) : [];

  const freq = Number(r.frequency_per_week);
  const minutes = Number(r.service_minutes);

  const visitFreq = VISIT_FREQUENCY_OPTIONS.includes(
    r.visit_frequency as (typeof VISIT_FREQUENCY_OPTIONS)[number],
  )
    ? (r.visit_frequency as (typeof VISIT_FREQUENCY_OPTIONS)[number])
    : null;

  const priority = WEEKDAY_PRIORITY_OPTIONS.includes(
    r.weekday_priority as (typeof WEEKDAY_PRIORITY_OPTIONS)[number],
  )
    ? (r.weekday_priority as (typeof WEEKDAY_PRIORITY_OPTIONS)[number])
    : '中';

  const timeType = TIME_TYPE_OPTIONS.includes(
    r.time_type as (typeof TIME_TYPE_OPTIONS)[number],
  )
    ? (r.time_type as (typeof TIME_TYPE_OPTIONS)[number])
    : '終日';

  const ngWeekdaysRaw = r.ng_weekdays;
  const ngWeekdays =
    ngWeekdaysRaw === null || ngWeekdaysRaw === undefined
      ? null
      : filterWeekdays(ngWeekdaysRaw);

  return {
    frequency_per_week: Number.isFinite(freq) ? Math.min(7, Math.max(1, freq)) : 1,
    visit_frequency: visitFreq,
    visit_weeks: typeof r.visit_weeks === 'string' ? r.visit_weeks : null,
    preferred_weekdays: filterWeekdays(r.preferred_weekdays),
    weekday_priority: priority,
    service_minutes: Number.isFinite(minutes)
      ? Math.min(180, Math.max(1, minutes))
      : 30,
    time_type: timeType,
    preferred_start:
      typeof r.preferred_start === 'string' && r.preferred_start ? r.preferred_start : null,
    preferred_end:
      typeof r.preferred_end === 'string' && r.preferred_end ? r.preferred_end : null,
    ng_weekdays: ngWeekdays,
  };
}

/** Map a server `PatientRead` into form-friendly string values. */
export function patientReadToFormValues(p: PatientRead): PatientFormValues {
  return {
    code: p.code,
    name: p.name,
    kana: p.kana ?? '',
    sex: (p.sex as PatientFormValues['sex']) ?? '',
    age: p.age !== undefined && p.age !== null ? String(p.age) : '',
    status: p.status ?? 'active',
    insurance: (p.insurance as PatientFormValues['insurance']) ?? '',
    address: p.address ?? '',
    lat: p.lat !== undefined && p.lat !== null ? String(p.lat) : '',
    lng: p.lng !== undefined && p.lng !== null ? String(p.lng) : '',
    primary_office_id: p.primary_office_id ?? '',
    required_staff_count: String(p.required_staff_count ?? 1),
    sex_restriction:
      (p.sex_restriction as PatientFormValues['sex_restriction']) ?? '',
    ng_time_start: p.ng_time_start ?? '',
    ng_time_end: p.ng_time_end ?? '',
    note: p.note ?? '',
    weekly_pattern: coerceWeeklyPattern(p.weekly_pattern),
    special_week: !!p.special_week,
    area: p.area ?? '',
    ng_staff_ids: p.ng_staff_ids ?? [],
    preferred_staff_ids: p.preferred_staff_ids ?? [],
    specified_type:
      (p.specified_type as PatientFormValues['specified_type']) ?? '',
    continuous_request: p.continuous_request ?? false,
  };
}
