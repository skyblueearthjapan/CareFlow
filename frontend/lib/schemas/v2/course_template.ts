/**
 * CourseTemplate v2 zod schemas (Wave 15 Phase 3 / W15-FE).
 *
 * Backend `backend/app/schemas/v2/course_template.py` の Pydantic schema と
 * **完全一致** させる。
 *
 * 設計サマリ:
 *   - 拠点 (office) 単位で A/B/C/D/E などのラベル
 *   - 月〜日の各曜日の定員 (capacity_*) を 0..50
 *   - 論理削除 (`deleted_at`) 採用
 */
import { z } from 'zod';

// ---------------------------------------------------------------------------
// Base / CRUD schemas
// ---------------------------------------------------------------------------

const capacity = z.number().int().min(0).max(50);

export const courseTemplateBaseSchema = z.object({
  label: z.string().min(1).max(8),
  capacity_mon: capacity.default(0),
  capacity_tue: capacity.default(0),
  capacity_wed: capacity.default(0),
  capacity_thu: capacity.default(0),
  capacity_fri: capacity.default(0),
  capacity_sat: capacity.default(0),
  capacity_sun: capacity.default(0),
  notes: z.string().nullable().optional(),
});

export type CourseTemplateBase = z.infer<typeof courseTemplateBaseSchema>;

export const courseTemplateCreateSchema = courseTemplateBaseSchema.extend({
  office_id: z.string().uuid(),
});

export type CourseTemplateCreate = z.infer<typeof courseTemplateCreateSchema>;

export const courseTemplateUpdateSchema = z.object({
  label: z.string().min(1).max(8).optional(),
  capacity_mon: capacity.optional(),
  capacity_tue: capacity.optional(),
  capacity_wed: capacity.optional(),
  capacity_thu: capacity.optional(),
  capacity_fri: capacity.optional(),
  capacity_sat: capacity.optional(),
  capacity_sun: capacity.optional(),
  notes: z.string().nullable().optional(),
});

export type CourseTemplateUpdate = z.infer<typeof courseTemplateUpdateSchema>;

export const courseTemplateReadSchema = courseTemplateBaseSchema.extend({
  id: z.string().uuid(),
  office_id: z.string().uuid(),
  created_at: z.string(),
  updated_at: z.string(),
  deleted_at: z.string().nullable().optional(),
});

export type CourseTemplateRead = z.infer<typeof courseTemplateReadSchema>;

// ---------------------------------------------------------------------------
// Helpers — capacity_<weekday> アクセサ
// ---------------------------------------------------------------------------

/** 0=Mon..6=Sun → CourseTemplate のキー名. */
const WEEKDAY_KEYS = [
  'capacity_mon',
  'capacity_tue',
  'capacity_wed',
  'capacity_thu',
  'capacity_fri',
  'capacity_sat',
  'capacity_sun',
] as const;

export type CapacityKey = (typeof WEEKDAY_KEYS)[number];

export function capacityKeyForWeekday(weekday: number): CapacityKey | null {
  if (weekday < 0 || weekday > 6) return null;
  return WEEKDAY_KEYS[weekday] ?? null;
}

export function capacityForWeekday(tpl: CourseTemplateRead, weekday: number): number {
  const key = capacityKeyForWeekday(weekday);
  if (!key) return 0;
  return tpl[key] ?? 0;
}
