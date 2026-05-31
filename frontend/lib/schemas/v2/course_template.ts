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

// ---------------------------------------------------------------------------
// スタッフ数連動の有効定員 (週ビュー: auto-schedule と統一)
// ---------------------------------------------------------------------------

/**
 * A-E コース通常枠の発行上限 (= backend ``auto_allocator_v2._COURSE_CODES_MAX``).
 * 当該曜日の出勤スタッフ数 N に対し A-E は ``min(N, COURSE_CODES_MAX)`` 個だけ開講.
 * backend が API (`/schedule/v2/weekday-staff-capacity`) で `course_codes_max` を
 * 返すので通常はそちらを使う. ここは API 未取得時のフォールバック定数.
 */
export const COURSE_CODES_MAX = 5;

/**
 * template.label が A-E コードなら index (A=0..E=4) を返す. それ以外 (M/M2..) は null.
 *
 * 判定: label を trim + 大文字化し、 1 文字かつ 'A'..'E' のときのみ index を返す.
 * (label が 'Aコース' のような複数文字でも先頭が A-E なら採用 = legacy 互換.)
 */
export function courseCodeIndex(label: string | null | undefined): number | null {
  const up = (label ?? '').trim().toUpperCase();
  if (up.length === 0) return null;
  const first = up.charCodeAt(0);
  const a = 'A'.charCodeAt(0);
  const e = 'E'.charCodeAt(0);
  if (first < a || first > e) return null;
  return first - a;
}

/**
 * 週ビューの「休」/定員判定をスタッフ数連動にした有効定員.
 *
 *   - A-E コース: ``courseCodeIndex < min(staffCount, codesMax)`` なら開講 (定員6),
 *     それ以外は「休」(定員0). auto_allocator_v2 Stage 4 と同一ロジック.
 *   - M系 (M, M2..): 従来通り course_template の静的 capacity_<曜日> を使う.
 *
 * @param tpl        対象 course_template.
 * @param weekday    0=Mon..6=Sun.
 * @param staffCount 当該 (拠点×曜日) の稼働スタッフ数 (API 由来. 未取得は 0).
 * @param codesMax   A-E 発行上限 (API 由来. 省略時は COURSE_CODES_MAX=5).
 */
export function effectiveCapacity(
  tpl: CourseTemplateRead,
  weekday: number,
  staffCount: number,
  codesMax: number = COURSE_CODES_MAX,
): number {
  const idx = courseCodeIndex(tpl.label);
  if (idx === null) {
    // M系: 静的マスタ値をそのまま使う.
    return capacityForWeekday(tpl, weekday);
  }
  const opens = idx < Math.min(staffCount, codesMax);
  return opens ? 6 : 0;
}
