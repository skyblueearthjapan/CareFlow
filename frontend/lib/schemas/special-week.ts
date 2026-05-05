/**
 * SpecialWeek zod schemas — mirrors backend `app/schemas/special_week.py`.
 *
 * Header (`SpecialWeek`) + child items (`SpecialWeekItem`). Read shape
 * includes the items array. Items are submitted together on create / update
 * (full replacement on update).
 */
import { z } from 'zod';

export const APPLY_MODE_OPTIONS = ['ADD', 'REPLACE'] as const;
export const STATUS_OPTIONS = ['draft', 'applied', 'cancelled'] as const;
export const TIME_TYPE_OPTIONS = ['時間帯', '時刻指定', '希望幅'] as const;
export const INDIVIDUAL_CHANGE_OPTIONS = ['merge', 'override', 'ignore'] as const;

export const applyModeEnum = z.enum(APPLY_MODE_OPTIONS);
export const statusEnum = z.enum(STATUS_OPTIONS);

const isoDateSchema = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, '日付は YYYY-MM-DD 形式で入力してください');

const timeStringSchema = z
  .string()
  .regex(/^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/, '時刻は HH:MM 形式で入力してください');

const optionalTime = z
  .union([timeStringSchema, z.literal('')])
  .optional()
  .transform((v) => (v === '' || v === undefined ? undefined : v));

const optionalString = z
  .string()
  .optional()
  .transform((v) => (v === '' || v === undefined ? undefined : v));

// ---------- Item ----------

export const specialWeekItemBaseSchema = z.object({
  patient_id: z.string().uuid(),
  visit_date: isoDateSchema,
  weekday: z.coerce.number().int().min(0).max(6),
  row_label: optionalString,
  time_type: z.string().default('時間帯'),
  start_time: optionalTime,
  end_time: optionalTime,
  preferred_earliest: optionalTime,
  preferred_latest: optionalTime,
  service_minutes: z.coerce.number().int().min(1).max(600).default(30),
  required_staff_count: z.coerce.number().int().min(1).max(10).default(1),
  individual_change_handling: optionalString,
  note: optionalString,
});

export const specialWeekItemCreateSchema = specialWeekItemBaseSchema;

export const specialWeekItemReadSchema = specialWeekItemBaseSchema.extend({
  id: z.string().uuid(),
  special_week_id: z.string().uuid(),
  created_at: z.string(),
  updated_at: z.string(),
});

// ---------- Header ----------

export const specialWeekBaseSchema = z.object({
  patient_id: z.string().uuid(),
  week_start: isoDateSchema,
  week_end: isoDateSchema,
  apply_mode: applyModeEnum.default('ADD'),
  reason: optionalString,
  status: statusEnum.default('draft'),
});

export const specialWeekCreateSchema = specialWeekBaseSchema.extend({
  items: z.array(specialWeekItemCreateSchema).default([]),
});

export const specialWeekUpdateSchema = z.object({
  week_start: isoDateSchema.optional(),
  week_end: isoDateSchema.optional(),
  apply_mode: applyModeEnum.optional(),
  reason: optionalString,
  status: statusEnum.optional(),
  items: z.array(specialWeekItemCreateSchema).optional(),
});

export const specialWeekReadSchema = specialWeekBaseSchema.extend({
  id: z.string().uuid(),
  registered_at: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  items: z.array(specialWeekItemReadSchema).default([]),
});

export type SpecialWeekItemCreate = z.infer<typeof specialWeekItemCreateSchema>;
export type SpecialWeekItemRead = z.infer<typeof specialWeekItemReadSchema>;
export type SpecialWeekCreate = z.infer<typeof specialWeekCreateSchema>;
export type SpecialWeekUpdate = z.infer<typeof specialWeekUpdateSchema>;
export type SpecialWeekRead = z.infer<typeof specialWeekReadSchema>;

/**
 * Form-input shape for react-hook-form. Numeric/date/time fields are
 * captured as strings (HTML inputs) and coerced by the create/update zod
 * schemas on submit.
 */
export interface SpecialWeekItemFormValues {
  patient_id: string;
  visit_date: string;
  weekday: string; // 0..6
  row_label: string;
  time_type: string;
  start_time: string;
  end_time: string;
  preferred_earliest: string;
  preferred_latest: string;
  service_minutes: string;
  required_staff_count: string;
  individual_change_handling: string;
  note: string;
}

export interface SpecialWeekFormValues {
  patient_id: string;
  week_start: string;
  week_end: string;
  apply_mode: (typeof APPLY_MODE_OPTIONS)[number];
  reason: string;
  status: (typeof STATUS_OPTIONS)[number];
  items: SpecialWeekItemFormValues[];
}

export const emptySpecialWeekItemFormValues: SpecialWeekItemFormValues = {
  patient_id: '',
  visit_date: '',
  weekday: '0',
  row_label: '',
  time_type: '時間帯',
  start_time: '',
  end_time: '',
  preferred_earliest: '',
  preferred_latest: '',
  service_minutes: '30',
  required_staff_count: '1',
  individual_change_handling: '',
  note: '',
};

export const emptySpecialWeekFormValues: SpecialWeekFormValues = {
  patient_id: '',
  week_start: '',
  week_end: '',
  apply_mode: 'ADD',
  reason: '',
  status: 'draft',
  items: [],
};

/** Map a server `SpecialWeekRead` into form-friendly string values. */
export function specialWeekReadToFormValues(
  sw: SpecialWeekRead,
): SpecialWeekFormValues {
  return {
    patient_id: sw.patient_id,
    week_start: sw.week_start,
    week_end: sw.week_end,
    apply_mode: sw.apply_mode as SpecialWeekFormValues['apply_mode'],
    reason: sw.reason ?? '',
    status: sw.status as SpecialWeekFormValues['status'],
    items: (sw.items ?? []).map((it) => ({
      patient_id: it.patient_id,
      visit_date: it.visit_date,
      weekday: String(it.weekday),
      row_label: it.row_label ?? '',
      time_type: it.time_type ?? '時間帯',
      start_time: it.start_time ?? '',
      end_time: it.end_time ?? '',
      preferred_earliest: it.preferred_earliest ?? '',
      preferred_latest: it.preferred_latest ?? '',
      service_minutes: String(it.service_minutes ?? 30),
      required_staff_count: String(it.required_staff_count ?? 1),
      individual_change_handling: it.individual_change_handling ?? '',
      note: it.note ?? '',
    })),
  };
}
