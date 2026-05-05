/**
 * Patient zod schemas — mirrors backend `app/schemas/patient.py`
 * (Read / Create / Update).
 *
 * Notes
 * ─────
 * - `sex` / `insurance` / `sex_restriction` are stored as plain strings on
 *   the backend (`str | None`). We tighten them with zod enums on the FE so
 *   form selects can be driven from `*_OPTIONS` constants.
 * - `weekly_pattern` and `special_week` are NOT present in the current
 *   backend schema. They are kept here as forward-looking optional fields
 *   captured by the form but stripped before submit (see queries/patients.ts).
 *   TODO(Wave 2): wire to backend once columns land.
 */
import { z } from 'zod';

export const SEX_OPTIONS = ['男性', '女性'] as const;
export const INSURANCE_OPTIONS = ['医療保険', '介護保険'] as const;
export const SEX_RESTRICTION_OPTIONS = ['女性のみ', '男性のみ', 'なし'] as const;
export const STATUS_OPTIONS = ['active', 'inactive'] as const;

export const sexEnum = z.enum(SEX_OPTIONS);
export const insuranceEnum = z.enum(INSURANCE_OPTIONS);
export const sexRestrictionEnum = z.enum(SEX_RESTRICTION_OPTIONS);
export const statusEnum = z.enum(STATUS_OPTIONS);

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
  lat: z
    .union([z.coerce.number().min(-90).max(90), z.literal('')])
    .optional()
    .transform((v) => (v === '' || v === undefined ? undefined : (v as number))),
  lng: z
    .union([z.coerce.number().min(-180).max(180), z.literal('')])
    .optional()
    .transform((v) => (v === '' || v === undefined ? undefined : (v as number))),
  primary_office_id: z
    .union([z.string().uuid(), z.literal('')])
    .optional()
    .transform((v) => (v === '' || v === undefined ? undefined : v)),
  required_staff_count: z.coerce.number().int().min(1).max(10).default(1),
  sex_restriction: optionalEnum(SEX_RESTRICTION_OPTIONS),
  ng_time_start: optionalTime,
  ng_time_end: optionalTime,
  note: optionalNullableString,
  // Wave 2 — kept as form-only fields for now.
  // TODO(Wave 2): backend columns + structured editor.
  weekly_pattern: optionalNullableString,
  special_week: z.boolean().optional(),
});

export const patientCreateSchema = patientBaseSchema;

/** Update: all fields optional. */
export const patientUpdateSchema = patientBaseSchema.partial();

/** Read: server response. Includes server-generated fields. */
export const patientReadSchema = patientBaseSchema.extend({
  id: z.string().uuid(),
  created_at: z.string(),
  updated_at: z.string(),
  deleted_at: z.string().nullable().optional(),
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
  weekly_pattern: string;
  special_week: boolean;
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
  weekly_pattern: '',
  special_week: false,
};

/** Map a server `PatientRead` into form-friendly string values. */
export function patientReadToFormValues(p: PatientRead): PatientFormValues {
  return {
    code: p.code,
    name: p.name,
    kana: p.kana ?? '',
    sex: (p.sex as PatientFormValues['sex']) ?? '',
    age: p.age !== undefined && p.age !== null ? String(p.age) : '',
    status: p.status,
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
    weekly_pattern: p.weekly_pattern ?? '',
    special_week: p.special_week ?? false,
  };
}
