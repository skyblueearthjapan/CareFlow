/**
 * Staff zod schemas — mirrors `backend/app/schemas/staff.py` (StaffBase /
 * StaffCreate / StaffUpdate / StaffRead).
 *
 * v2 (W1-BE2 / §4.2): backend は ``extra="forbid"`` で 9 項目のみ受け付ける。
 * 削除済み: ``can_double_team`` / ``home_address`` / ``home_lat`` / ``home_lng`` /
 * ``areas`` / ``max_per_day`` / ``skill_level`` / ``assignment_volume``。
 *
 * 値域:
 *   sex    ∈ male | female | unknown
 *   role   ∈ admin | manager | staff
 *   status ∈ active | on_leave | retired
 *
 * Read-only Wave 1 scope also includes the surrounding domain types referenced
 * by the detail page: weekly shifts, weekly overrides, events, and mentor
 * assignments. Editable mutations for those are deferred to Wave 2 — until then
 * the detail page renders mock data with TODO markers (see below).
 */
import { z } from 'zod';

// ---------------------------------------------------------------------------
// Core staff CRUD payloads (1:1 with backend pydantic models)
// ---------------------------------------------------------------------------

export const STAFF_SEX_VALUES = ['male', 'female', 'unknown'] as const;
export const STAFF_STATUS_VALUES = ['active', 'on_leave', 'retired'] as const;
export const STAFF_ROLE_VALUES = ['admin', 'manager', 'staff'] as const;

export const staffBaseSchema = z.object({
  code: z.string().max(64).nullable().optional(),
  name: z.string().min(1, '氏名は必須です').max(120),
  kana: z.string().max(120).nullable().optional(),
  sex: z.enum(STAFF_SEX_VALUES).nullable().optional(),
  status: z.enum(STAFF_STATUS_VALUES).default('active'),
  role: z.enum(STAFF_ROLE_VALUES).default('staff'),
  primary_office_id: z.string().uuid().nullable().optional(),
  /** Wave 10: 新人スタッフフラグ。true のとき同行スタッフ割付が必要 (§4.2.x) */
  is_trainee: z.boolean().default(false),
  note: z.string().nullable().optional(),
});

export const staffCreateSchema = staffBaseSchema;

export const staffUpdateSchema = z.object({
  code: z.string().max(64).nullable().optional(),
  name: z.string().min(1).max(120).optional(),
  kana: z.string().max(120).nullable().optional(),
  sex: z.enum(STAFF_SEX_VALUES).nullable().optional(),
  status: z.enum(STAFF_STATUS_VALUES).optional(),
  role: z.enum(STAFF_ROLE_VALUES).optional(),
  primary_office_id: z.string().uuid().nullable().optional(),
  /** Wave 10: 新人スタッフフラグ (§4.2.x) */
  is_trainee: z.boolean().optional(),
  note: z.string().nullable().optional(),
});

export const staffReadSchema = staffBaseSchema.extend({
  id: z.string().uuid(),
  created_at: z.string(),
  updated_at: z.string(),
  deleted_at: z.string().nullable().optional(),
});

export type StaffCreate = z.infer<typeof staffCreateSchema>;
export type StaffUpdate = z.infer<typeof staffUpdateSchema>;
export type StaffRead = z.infer<typeof staffReadSchema>;

// ---------------------------------------------------------------------------
// v1 → v2 後方互換 (DB hot-fix 漏れ救済)
// ---------------------------------------------------------------------------

/**
 * 旧 v1 値 (F/M/X や 男/女) を v2 値 (male/female/unknown) に正規化する。
 * 想定外の値が来た場合は ``null`` を返す。
 */
export function normalizeStaffSex(
  v: string | null | undefined,
): (typeof STAFF_SEX_VALUES)[number] | null {
  if (v === null || v === undefined || v === '') return null;
  if (v === 'male' || v === 'female' || v === 'unknown') return v;
  if (['M', 'm', '男', '男性', 'Male'].includes(v)) return 'male';
  if (['F', 'f', '女', '女性', 'Female'].includes(v)) return 'female';
  if (['X', 'x', 'その他', 'Other', 'other'].includes(v)) return 'unknown';
  return null;
}

/**
 * 旧 v1 値 (``inactive``) を v2 値 (``on_leave``) に正規化する。
 * v2 値 (``active`` / ``on_leave`` / ``retired``) はそのまま通す。
 */
export function normalizeStaffStatus(
  v: string | null | undefined,
): (typeof STAFF_STATUS_VALUES)[number] {
  if (v === 'active' || v === 'on_leave' || v === 'retired') return v;
  if (v === 'inactive' || v === '休職') return 'on_leave';
  if (v === '退職') return 'retired';
  return 'active';
}

// ---------------------------------------------------------------------------
// Surrounding domain (read-only Wave 1)
//
// The backend already models these tables (`staff_shifts`,
// `staff_weekly_overrides`, `staff_events`, `mentor_assignments`) but no REST
// endpoints exist yet. Wave 2 will introduce them — until then the detail page
// uses mock fixtures shaped like these schemas.
// ---------------------------------------------------------------------------

/** 0 = Mon ... 6 = Sun (matches backend `staff_shifts.weekday`). */
export const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

export const staffShiftSchema = z.object({
  staff_id: z.string().uuid(),
  weekday: z.number().int().min(0).max(6),
  is_on: z.boolean(),
  start_time: z.string().nullable().optional(),
  end_time: z.string().nullable().optional(),
});
export type StaffShift = z.infer<typeof staffShiftSchema>;

export const staffWeeklyOverrideSchema = z.object({
  id: z.string().uuid(),
  staff_id: z.string().uuid(),
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  weekday: z.number().int().min(0).max(6),
  override_type: z.enum(['off', 'custom_time']),
  start_time: z.string().nullable().optional(),
  end_time: z.string().nullable().optional(),
  reason: z.string().nullable().optional(),
});
export type StaffWeeklyOverride = z.infer<typeof staffWeeklyOverrideSchema>;

export const staffEventSchema = z.object({
  id: z.string().uuid(),
  staff_id: z.string().uuid(),
  event_type: z.string(),
  starts_at: z.string(),
  ends_at: z.string(),
  title: z.string().nullable().optional(),
  note: z.string().nullable().optional(),
});
export type StaffEvent = z.infer<typeof staffEventSchema>;

// ---------------------------------------------------------------------------
// Helpers shared by list filters
// ---------------------------------------------------------------------------

export const sexLabel = (sex: StaffRead['sex'] | string | null | undefined): string => {
  // 旧 v1 値 (F/M/X) も後方互換で読めるように normalize 経由で表示
  const normalized = typeof sex === 'string' ? normalizeStaffSex(sex) : (sex ?? null);
  switch (normalized) {
    case 'male':
      return '男性';
    case 'female':
      return '女性';
    case 'unknown':
      return '不明';
    default:
      return '--';
  }
};

export const roleLabel = (role: StaffRead['role']): string => {
  switch (role) {
    case 'admin':
      return '管理者';
    case 'manager':
      return 'マネージャー';
    case 'staff':
      return 'スタッフ';
    default:
      return role ?? '--';
  }
};

export const statusLabel = (status: StaffRead['status'] | string | null | undefined): string => {
  const normalized =
    typeof status === 'string' ? normalizeStaffStatus(status) : (status ?? 'active');
  switch (normalized) {
    case 'active':
      return '在籍';
    case 'on_leave':
      return '休職';
    case 'retired':
      return '退職';
    default:
      return '--';
  }
};
