/**
 * Staff zod schemas — mirrors `backend/app/schemas/staff.py` (StaffBase /
 * StaffCreate / StaffUpdate / StaffRead).
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

export const STAFF_SEX_VALUES = ['F', 'M', 'X'] as const;
export const STAFF_STATUS_VALUES = ['active', 'inactive'] as const;
export const STAFF_ROLE_VALUES = ['admin', 'manager', 'staff'] as const;

// W3-D additions — mirror backend `staff` model + pydantic schema columns
// added in W3-A (home_address / home_lat / home_lng / areas / max_per_day /
// skill_level / assignment_volume). Treated as optional on the FE so older
// clients / responses without the fields still parse cleanly.
export const STAFF_SKILL_LEVEL_VALUES = ['新人', '中堅', 'ベテラン'] as const;
export const STAFF_ASSIGNMENT_VOLUME_VALUES = ['少なめ', '通常', '多め'] as const;

const optionalCoercedLat = z.preprocess(
  (v) => (v === '' || v === null || v === undefined ? undefined : v),
  z.coerce.number().min(-90).max(90).optional(),
);
const optionalCoercedLng = z.preprocess(
  (v) => (v === '' || v === null || v === undefined ? undefined : v),
  z.coerce.number().min(-180).max(180).optional(),
);

export const staffBaseSchema = z.object({
  code: z.string().max(32).nullable().optional(),
  name: z.string().min(1, '氏名は必須です').max(120),
  kana: z.string().max(120).nullable().optional(),
  sex: z.enum(STAFF_SEX_VALUES).nullable().optional(),
  status: z.enum(STAFF_STATUS_VALUES).default('active'),
  role: z.enum(STAFF_ROLE_VALUES).default('staff'),
  primary_office_id: z.string().uuid().nullable().optional(),
  can_double_team: z.boolean().default(false),
  mentor_id: z.string().uuid().nullable().optional(),
  // W3-D additions
  home_address: z.string().max(255).nullable().optional(),
  home_lat: optionalCoercedLat,
  home_lng: optionalCoercedLng,
  areas: z.array(z.string()).nullable().optional().default([]),
  max_per_day: z.coerce.number().int().min(1).max(20).default(6),
  skill_level: z.enum(STAFF_SKILL_LEVEL_VALUES).nullable().optional(),
  assignment_volume: z.enum(STAFF_ASSIGNMENT_VOLUME_VALUES).nullable().optional(),
  note: z.string().nullable().optional(),
});

export const staffCreateSchema = staffBaseSchema;

export const staffUpdateSchema = z.object({
  code: z.string().max(32).nullable().optional(),
  name: z.string().min(1).max(120).optional(),
  kana: z.string().max(120).nullable().optional(),
  sex: z.enum(STAFF_SEX_VALUES).nullable().optional(),
  status: z.enum(STAFF_STATUS_VALUES).optional(),
  role: z.enum(STAFF_ROLE_VALUES).optional(),
  primary_office_id: z.string().uuid().nullable().optional(),
  can_double_team: z.boolean().optional(),
  mentor_id: z.string().uuid().nullable().optional(),
  // W3-D additions
  home_address: z.string().max(255).nullable().optional(),
  home_lat: optionalCoercedLat,
  home_lng: optionalCoercedLng,
  areas: z.array(z.string()).nullable().optional(),
  max_per_day: z.coerce.number().int().min(1).max(20).optional(),
  skill_level: z.enum(STAFF_SKILL_LEVEL_VALUES).nullable().optional(),
  assignment_volume: z.enum(STAFF_ASSIGNMENT_VOLUME_VALUES).nullable().optional(),
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

export const mentorAssignmentSchema = z.object({
  id: z.string().uuid(),
  mentor_id: z.string().uuid(),
  mentee_id: z.string().uuid(),
  start_date: z.string(),
  end_date: z.string().nullable().optional(),
});
export type MentorAssignment = z.infer<typeof mentorAssignmentSchema>;

// ---------------------------------------------------------------------------
// Helpers shared by list filters
// ---------------------------------------------------------------------------

export const sexLabel = (sex: StaffRead['sex']): string => {
  switch (sex) {
    case 'F':
      return '女性';
    case 'M':
      return '男性';
    case 'X':
      return 'その他';
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

export const statusLabel = (status: StaffRead['status']): string => {
  switch (status) {
    case 'active':
      return '在籍';
    case 'inactive':
      return '休止';
    default:
      return status ?? '--';
  }
};

export const skillLevelLabel = (level: StaffRead['skill_level']): string =>
  level ?? '--';

export const assignmentVolumeLabel = (
  volume: StaffRead['assignment_volume'],
): string => volume ?? '--';
