/**
 * Patient zod schemas — mirrors backend `app/schemas/v2/patient.py`
 * (PatientV2Base / PatientV2Create / PatientV2Update / PatientV2Read).
 *
 * v2 (W1-BE1 / §4.1): backend は §4.1 の 16 項目のみ受け付ける。
 * 削除済み (v2 schema に含めない):
 *   age / ng_time_start / ng_time_end / required_staff_count / area /
 *   ng_staff_ids / preferred_staff_ids / specified_type / continuous_request /
 *   weekday_priority。
 *
 * 値域:
 *   sex             ∈ male | female | unknown
 *   status          ∈ active | suspended | admitted | pending | cancelled
 *   insurance       ∈ medical | care
 *   sex_restriction ∈ female_only | male_only | null
 *
 * 追加 (§3.4):
 *   - special_weekly_pattern (JSONB)
 *   - special_week_active (適用週リスト [{iso_year, iso_week}, ...])
 *
 * Notes
 * ─────
 * - backend は ``extra="ignore"`` なので未知フィールドは黙殺されるが、
 *   送信側 (frontend) でも一致させて送信ペイロードを軽くする。
 * - 旧 v1 値 (例: ``男性`` / ``女性`` / ``医療保険``) が DB に残っている前提で
 *   ``patientReadToFormValues`` で v2 値に正規化する。
 * - `weekly_pattern` / `special_weekly_pattern` は JSONB (dict)。フォームは
 *   `WeeklyPatternEditor` (W3-C) で構造化。
 */
import { z } from 'zod';

// ---------------------------------------------------------------------------
// Enum 定義 (backend `app/schemas/v2/patient.py` と完全一致)
// ---------------------------------------------------------------------------

export const SEX_OPTIONS = ['male', 'female', 'unknown'] as const;
export const INSURANCE_OPTIONS = ['medical', 'care'] as const;
export const SEX_RESTRICTION_OPTIONS = ['female_only', 'male_only'] as const;
export const STATUS_OPTIONS = ['active', 'suspended', 'admitted', 'pending', 'cancelled'] as const;

export const sexEnum = z.enum(SEX_OPTIONS);
export const insuranceEnum = z.enum(INSURANCE_OPTIONS);
export const sexRestrictionEnum = z.enum(SEX_RESTRICTION_OPTIONS);
export const statusEnum = z.enum(STATUS_OPTIONS);

// ---------------------------------------------------------------------------
// 表示用ラベル
// ---------------------------------------------------------------------------

export const SEX_LABEL: Record<(typeof SEX_OPTIONS)[number], string> = {
  male: '男性',
  female: '女性',
  unknown: '不明',
};

export const INSURANCE_LABEL: Record<(typeof INSURANCE_OPTIONS)[number], string> = {
  medical: '医療保険',
  care: '介護保険',
};

export const SEX_RESTRICTION_LABEL: Record<(typeof SEX_RESTRICTION_OPTIONS)[number], string> = {
  female_only: '女性のみ',
  male_only: '男性のみ',
};

export const STATUS_LABEL: Record<(typeof STATUS_OPTIONS)[number], string> = {
  active: '稼働中',
  suspended: '一時休止',
  admitted: '入院中',
  pending: '開始前',
  cancelled: '解約済み',
};

/** HH:MM (24h) — backend stores `time` (Python). */
const timeStringSchema = z
  .string()
  .regex(/^([01]\d|2[0-3]):[0-5]\d$/, '時刻は HH:MM 形式で入力してください');
void timeStringSchema; // 互換のため一時的に保持 (未使用警告回避)

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
export const VISIT_FREQUENCY_LABELS: Record<(typeof VISIT_FREQUENCY_OPTIONS)[number], string> = {
  every: '毎週',
  biweekly: '隔週',
  monthly: '月次',
};

export const TIME_TYPE_OPTIONS = ['固定', '午前', '午後', '終日', '時間帯'] as const;

/**
 * サービス時間 `service_minutes` の共有プルダウン候補。
 *
 * 5 分刻み・範囲 15〜180 分 (Excel 取込ドロップダウン慣習に一致)。
 * 本機 (患者マスタ `WeeklyPatternEditor`) と子機 (現場ボード `SuggestSheet`) の
 * 両方から import して**完全に同一の選択肢**にし、定義のドリフトを防ぐ。
 *
 * value は number で統一する (onChange 側で `Number(...)` に揃える)。
 */
export const SERVICE_MINUTES_OPTIONS: number[] = (() => {
  const out: number[] = [];
  for (let m = 15; m <= 180; m += 5) out.push(m);
  return out;
})();

/** サービス時間の既定値 (基本 35 分)。新規作成時のデフォルトに使う。 */
export const DEFAULT_SERVICE_MINUTES = 35;

export interface WeeklyPattern {
  frequency_per_week: number;
  visit_frequency: (typeof VISIT_FREQUENCY_OPTIONS)[number] | null;
  visit_weeks: string | null;
  preferred_weekdays: WeekdayKey[];
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
  service_minutes: DEFAULT_SERVICE_MINUTES,
  time_type: '終日',
  preferred_start: null,
  preferred_end: null,
  ng_weekdays: null,
};

// ---------------------------------------------------------------------------
// v1 → v2 後方互換 (DB hot-fix 漏れ救済)
// ---------------------------------------------------------------------------

export function normalizePatientSex(
  v: string | null | undefined,
): (typeof SEX_OPTIONS)[number] | null {
  if (v === null || v === undefined || v === '') return null;
  if (v === 'male' || v === 'female' || v === 'unknown') return v;
  if (['M', 'm', '男', '男性', 'Male'].includes(v)) return 'male';
  if (['F', 'f', '女', '女性', 'Female'].includes(v)) return 'female';
  if (['X', 'x', 'その他', 'Other', 'other', '不明'].includes(v)) return 'unknown';
  return null;
}

export function normalizePatientInsurance(
  v: string | null | undefined,
): (typeof INSURANCE_OPTIONS)[number] | null {
  if (v === null || v === undefined || v === '') return null;
  if (v === 'medical' || v === 'care') return v;
  if (v === '医療保険') return 'medical';
  if (v === '介護保険') return 'care';
  return null;
}

export function normalizePatientStatus(
  v: string | null | undefined,
): (typeof STATUS_OPTIONS)[number] {
  if (
    v === 'active' ||
    v === 'suspended' ||
    v === 'admitted' ||
    v === 'pending' ||
    v === 'cancelled'
  ) {
    return v;
  }
  // v1 の ``inactive`` は v2 に対応する厳密な値がないので ``suspended`` に寄せる
  if (v === 'inactive') return 'suspended';
  return 'active';
}

export function normalizePatientSexRestriction(
  v: string | null | undefined,
): (typeof SEX_RESTRICTION_OPTIONS)[number] | null {
  if (v === null || v === undefined || v === '') return null;
  if (v === 'female_only' || v === 'male_only') return v;
  if (v === '女性のみ') return 'female_only';
  if (v === '男性のみ') return 'male_only';
  // v1 の ``なし`` は v2 では null
  return null;
}

// ---------------------------------------------------------------------------
// CRUD payloads (1:1 with backend pydantic models)
// ---------------------------------------------------------------------------

/** Shared base — fields common to Create/Read/Update. */
export const patientBaseSchema = z.object({
  code: z.string().min(1, 'コードは必須です').max(64),
  name: z.string().min(1, '氏名は必須です').max(120),
  kana: optionalNullableString,
  sex: optionalEnum(SEX_OPTIONS),
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
  sex_restriction: optionalEnum(SEX_RESTRICTION_OPTIONS),
  /**
   * 2 名体制 (複数スタッフ) での訪問が必要な患者かどうか (Wave 18 Phase 0+A).
   *
   * BE migration 0024 で追加される `patients.requires_multiple_staff` を
   * フロント側で先行ミラー。FE は schema/UI を先に出し、BE 完成後の整合確認で
   * 値の往復を保証する。未対応 BE では空 (undefined) として扱い、PATCH 時にも
   * `dropUndefined` で除外されるため安全。
   */
  requires_multiple_staff: z
    .union([z.boolean(), z.literal('true'), z.literal('false'), z.literal('')])
    .optional()
    .transform((v) => {
      if (v === undefined || v === '') return undefined;
      if (v === true || v === 'true') return true;
      if (v === false || v === 'false') return false;
      return undefined;
    }),
  note: optionalNullableString,
  weekly_pattern: z.record(z.unknown()).nullish(),
  special_weekly_pattern: z.record(z.unknown()).nullish(),
  special_week_active: z
    .array(
      z.object({
        iso_year: z.number().int(),
        iso_week: z.number().int(),
      }),
    )
    .optional()
    .default([]),
});

/**
 * Phase G-86: 患者コードを任意化する Create/Form 用の上書きフィールド。
 * 空文字は `undefined` に畳んで wire から落とし、backend に自動採番させる
 * (`dropUndefined` が undefined キーを除外)。値が入っていれば従来どおり送る。
 * base (Read 共有) の必須 `code` には触れず、Create/Form 変種のみ緩める。
 */
const optionalPatientCode = z
  .string()
  .trim()
  .max(64)
  .optional()
  .transform((v) => (v === '' || v === undefined ? undefined : v));

/** Create: `code` は任意 (空欄で backend 自動採番)。他フィールドは base のまま。 */
export const patientCreateSchema = patientBaseSchema.extend({
  code: optionalPatientCode,
});

/**
 * Update: all fields optional. Hand-written (not `.partial()`) so that the
 * `status` defaults on the base schema do NOT leak into PATCH payloads
 * (which would silently overwrite server values).
 */
export const patientUpdateSchema = z.object({
  code: z.string().min(1).max(64).optional(),
  name: z.string().min(1).max(120).optional(),
  kana: optionalNullableString,
  sex: optionalEnum(SEX_OPTIONS),
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
  sex_restriction: optionalEnum(SEX_RESTRICTION_OPTIONS),
  /** Wave 18: 2 名体制での訪問が必要かどうか. PATCH 時のみ送信. */
  requires_multiple_staff: z
    .union([z.boolean(), z.literal('true'), z.literal('false'), z.literal('')])
    .optional()
    .transform((v) => {
      if (v === undefined || v === '') return undefined;
      if (v === true || v === 'true') return true;
      if (v === false || v === 'false') return false;
      return undefined;
    }),
  note: optionalNullableString,
  weekly_pattern: z.record(z.unknown()).nullish(),
  special_weekly_pattern: z.record(z.unknown()).nullish(),
  special_week_active: z
    .array(
      z.object({
        iso_year: z.number().int(),
        iso_week: z.number().int(),
      }),
    )
    .optional(),
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
  .omit({
    weekly_pattern: true,
    special_weekly_pattern: true,
    special_week_active: true,
    requires_multiple_staff: true,
  })
  .extend({
    // Phase G-86: フォームでも患者コードは任意 (空欄で backend 自動採番)。
    code: optionalPatientCode,
    weekly_pattern: z.record(z.unknown()).optional(),
    special_week: z.boolean().optional().default(false),
    /** Wave 18: フォームでは plain boolean でバインド。送信時に PATCH 経由で BE に渡す。 */
    requires_multiple_staff: z.boolean().optional().default(false),
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
  status: (typeof STATUS_OPTIONS)[number];
  insurance: '' | (typeof INSURANCE_OPTIONS)[number];
  address: string;
  lat: string;
  lng: string;
  primary_office_id: string;
  sex_restriction: '' | (typeof SEX_RESTRICTION_OPTIONS)[number];
  /** Wave 18: 2 名体制必須フラグ (checkbox)。boolean に正規化。 */
  requires_multiple_staff: boolean;
  note: string;
  weekly_pattern: WeeklyPattern;
  special_week: boolean;
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
  requires_multiple_staff: false,
  note: '',
  weekly_pattern: emptyWeeklyPattern,
  special_week: false,
};

/**
 * Wave 18 Phase B-4: WeeklyPattern を 「プールカード表示用の希望時間ラベル」に
 * 整形する。
 *   - time_type='固定' で preferred_start のみ → "09:30 (固定)"
 *   - time_type='時間帯' で 両端あり → "09:30〜10:00 (時間帯)"
 *   - time_type='午前' / '午後' / '終日' → そのままラベル化 (時刻なし → 'なし' を返さない)
 *   - 何も無い → null
 */
export function formatPreferredTimeLabel(wp: WeeklyPattern | null | undefined): string | null {
  if (!wp) return null;
  const start = wp.preferred_start ?? null;
  const end = wp.preferred_end ?? null;
  const tt = wp.time_type;
  if (tt === '固定') {
    if (start) return `${start} (固定)`;
    // 時刻未入力の固定: 中途半端なラベル '固定' を表示しないため null を返す。
    // PatientCard 側で null の場合は時間表示を省略する。
    return null;
  }
  if (tt === '時間帯') {
    if (start && end) return `${start}〜${end} (時間帯)`;
    if (start) return `${start}〜 (時間帯)`;
    return '時間帯';
  }
  if (tt === '午前' || tt === '午後' || tt === '終日') {
    return tt;
  }
  return null;
}

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

  const timeType = TIME_TYPE_OPTIONS.includes(r.time_type as (typeof TIME_TYPE_OPTIONS)[number])
    ? (r.time_type as (typeof TIME_TYPE_OPTIONS)[number])
    : '終日';

  const ngWeekdaysRaw = r.ng_weekdays;
  const ngWeekdays =
    ngWeekdaysRaw === null || ngWeekdaysRaw === undefined ? null : filterWeekdays(ngWeekdaysRaw);

  return {
    frequency_per_week: Number.isFinite(freq) ? Math.min(7, Math.max(1, freq)) : 1,
    visit_frequency: visitFreq,
    visit_weeks: typeof r.visit_weeks === 'string' ? r.visit_weeks : null,
    preferred_weekdays: filterWeekdays(r.preferred_weekdays),
    service_minutes: Number.isFinite(minutes)
      ? Math.min(180, Math.max(1, minutes))
      : DEFAULT_SERVICE_MINUTES,
    time_type: timeType,
    preferred_start:
      typeof r.preferred_start === 'string' && r.preferred_start ? r.preferred_start : null,
    preferred_end: typeof r.preferred_end === 'string' && r.preferred_end ? r.preferred_end : null,
    ng_weekdays: ngWeekdays,
  };
}

/**
 * Map a server `PatientRead` into form-friendly string values.
 *
 * 旧 v1 値 (例: ``男性`` / ``医療保険`` / ``inactive``) が DB に残っていても
 * v2 値に正規化してフォームに渡す。 ``patientReadSchema`` は ``passthrough``
 * 寄り (asymmetric) で、未知キーは握りつぶす想定。
 */
export function patientReadToFormValues(p: PatientRead): PatientFormValues {
  return {
    code: p.code,
    name: p.name,
    kana: p.kana ?? '',
    sex:
      (normalizePatientSex(p.sex as string | null | undefined) as PatientFormValues['sex']) ?? '',
    status: normalizePatientStatus(p.status as string | null | undefined),
    insurance:
      (normalizePatientInsurance(
        p.insurance as string | null | undefined,
      ) as PatientFormValues['insurance']) ?? '',
    address: p.address ?? '',
    lat: p.lat !== undefined && p.lat !== null ? String(p.lat) : '',
    lng: p.lng !== undefined && p.lng !== null ? String(p.lng) : '',
    primary_office_id: p.primary_office_id ?? '',
    sex_restriction:
      (normalizePatientSexRestriction(
        p.sex_restriction as string | null | undefined,
      ) as PatientFormValues['sex_restriction']) ?? '',
    requires_multiple_staff:
      (p as { requires_multiple_staff?: boolean | null }).requires_multiple_staff === true,
    note: p.note ?? '',
    weekly_pattern: coerceWeeklyPattern(p.weekly_pattern),
    special_week: !!p.special_weekly_pattern,
  };
}
