/**
 * Visit zod schemas — mirrors backend `app/schemas/visit.py`
 * (VisitBase / VisitCreate / VisitUpdate / VisitRead).
 *
 * Backend stores `start_time` / `end_time` as Python `time` objects, which
 * FastAPI serializes as `HH:MM:SS`. We narrow the regex to accept either
 * `HH:MM` or `HH:MM:SS` so server payloads round-trip without a transform
 * but the form-side `<input type="time">` (which emits `HH:MM`) also passes.
 */
import { z } from 'zod';

/** HH:MM or HH:MM:SS (24h) — backend stores `time`. */
const timeStringSchema = z
  .string()
  .regex(/^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$/, '時刻は HH:MM 形式で入力してください');

/** ISO date string (yyyy-MM-dd). */
const dateStringSchema = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, '日付は yyyy-MM-dd 形式で入力してください');

export const VISIT_TYPE_VALUES = ['regular', 'spot', 'event'] as const;
export const VISIT_STATUS_VALUES = ['planned', 'confirmed', 'done', 'cancelled'] as const;
export const VISIT_SOURCE_VALUES = ['manual', 'allocate', 'kaipoke'] as const;

export const visitBaseSchema = z.object({
  patient_id: z.string().uuid(),
  primary_staff_id: z.string().uuid().nullable().optional(),
  secondary_staff_id: z.string().uuid().nullable().optional(),
  mentor_staff_id: z.string().uuid().nullable().optional(),
  visit_date: dateStringSchema,
  start_time: timeStringSchema,
  end_time: timeStringSchema,
  type: z.string().default('regular'),
  status: z.string().default('planned'),
  source: z.string().default('manual'),
  note: z.string().nullable().optional(),
  kaipoke_id: z.string().nullable().optional(),
});

export const visitCreateSchema = visitBaseSchema;

export const visitUpdateSchema = z.object({
  patient_id: z.string().uuid().optional(),
  primary_staff_id: z.string().uuid().nullable().optional(),
  secondary_staff_id: z.string().uuid().nullable().optional(),
  mentor_staff_id: z.string().uuid().nullable().optional(),
  visit_date: dateStringSchema.optional(),
  start_time: timeStringSchema.optional(),
  end_time: timeStringSchema.optional(),
  type: z.string().optional(),
  status: z.string().optional(),
  source: z.string().optional(),
  note: z.string().nullable().optional(),
  kaipoke_id: z.string().nullable().optional(),
});

export const visitReadSchema = visitBaseSchema.extend({
  id: z.string().uuid(),
  created_at: z.string(),
  updated_at: z.string(),
  deleted_at: z.string().nullable().optional(),
  /** Server-side denormalized join (see backend `_serialize_visit`). */
  patient_name: z.string().nullable().optional(),
  staff_name: z.string().nullable().optional(),
  /** v2 Layer 2: コース紐付け (Wave 4 以降, BE `visits.course_id`). */
  course_id: z.string().uuid().nullable().optional(),
  /**
   * v2 §3.3 / §4.5: 必要スタッフ数 (1=通常, 2=2 名体制).
   * BE `_serialize_visit` が常に返す (default 1)。FE では「複数」列の判定に使う。
   */
  required_staff_count: z
    .union([z.literal(1), z.literal(2)])
    .default(1)
    .optional(),
  /** v2 §3.3: 2 名体制の visit グルーピングキー. 通常は null. */
  visit_group_id: z.string().uuid().nullable().optional(),
  /**
   * Phase G-21 T4 reviewer C2: BE が将来 visits API に `fixed_visit_id` / `is_pinned`
   * を expose した場合に備えて optional で受け取れるようにする. 現状 BE 未 expose で
   * あり、FE は別途 PFV API を join して値を導出する (CourseDayTablePanel 参照).
   */
  fixed_visit_id: z.string().uuid().nullable().optional(),
  is_pinned: z.boolean().nullable().optional(),
});

export type VisitCreate = z.infer<typeof visitCreateSchema>;
export type VisitUpdate = z.infer<typeof visitUpdateSchema>;
export type VisitRead = z.infer<typeof visitReadSchema>;

/** Form-input shape used by `VisitEditDialog` (HTML inputs use string values). */
export type VisitFormValues = {
  visit_date: string;
  start_time: string;
  end_time: string;
  primary_staff_id: string;
  note: string;
};

export const emptyVisitFormValues: VisitFormValues = {
  visit_date: '',
  start_time: '',
  end_time: '',
  primary_staff_id: '',
  note: '',
};

/** Strip seconds when present so `<input type="time">` accepts the value. */
export function trimSeconds(t: string | null | undefined): string {
  if (!t) return '';
  return t.length >= 5 ? t.slice(0, 5) : t;
}

export function visitReadToFormValues(v: VisitRead): VisitFormValues {
  return {
    visit_date: v.visit_date,
    start_time: trimSeconds(v.start_time),
    end_time: trimSeconds(v.end_time),
    primary_staff_id: v.primary_staff_id ?? '',
    note: v.note ?? '',
  };
}
