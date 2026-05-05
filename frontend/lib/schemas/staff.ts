/**
 * Staff zod schemas — v2 re-export shim (Wave 1 / W1-FE2).
 *
 * 設計仕様書 v0.9 §4.2 + 実装手順書 §2 W1-FE2 に基づき、本ファイルは
 * `frontend/lib/schemas/v2/staff.ts` (W0-C 完成済み) からの **re-export**
 * を主目的とする。
 *
 * 互換性方針:
 * - 一次型 (`StaffCreate` / `StaffUpdate` / `StaffRead`) は v2 schema を流用
 * - 状態 (`status`) は **3 値 (`active` / `on_leave` / `retired`)** に統一
 * - 削除予定フィールド (can_double_team / home_address+lat/lng / areas /
 *   max_per_day / skill_level / assignment_volume) は **deprecated optional**
 *   として型定義に残置 (W1-BE2 マイグレーション完了 / Wave 6 移行までの暫定)。
 *   UI からは削除済み — 新規入力経路は無い。
 * - 既存の helper (`roleLabel` 等) は維持し、v2 enum 値にマップする。
 * - 周辺型 (`StaffShift` / `StaffWeeklyOverride` / `StaffEvent` /
 *   `MentorAssignment` / `WEEKDAY_LABELS`) は v2 では仕様未確定のため
 *   そのまま残す (Wave 2 で再設計予定)。
 *
 * Wave 1 完了 + Wave 6 移行後に **本ファイルを削除**し、`@/lib/schemas/v2/staff`
 * 直接参照に統一する想定。
 */
import { z } from 'zod';

import {
  ROLE_V2_VALUES,
  STAFF_SEX_V2_VALUES,
  STAFF_STATUS_V2_VALUES,
  staffV2BaseSchema,
  staffV2CreateSchema,
  staffV2ReadSchema,
  staffV2UpdateSchema,
  type StaffV2Base,
  type StaffV2Create,
  type StaffV2Read,
  type StaffV2Update,
  type RoleV2,
  type StaffSexV2,
  type StaffStatusV2,
} from '@/lib/schemas/v2/staff';

// ---------------------------------------------------------------------------
// Re-exported v2 enums under their legacy names so call-sites keep compiling
// ---------------------------------------------------------------------------

/** v2: 在籍 / 休職 / 退職 (§4.2). */
export const STAFF_STATUS_VALUES = STAFF_STATUS_V2_VALUES;
/** v2: male / female / unknown. */
export const STAFF_SEX_VALUES = STAFF_SEX_V2_VALUES;
/** v2: admin / manager / staff. */
export const STAFF_ROLE_VALUES = ROLE_V2_VALUES;

// ---------------------------------------------------------------------------
// Deprecated legacy enums — kept so list/detail UIs that haven't been migrated
// yet still type-check. **No new code should reference these.**
// ---------------------------------------------------------------------------

/** @deprecated Removed from UI in W1-FE2; backend will drop in W1-BE2. */
export const STAFF_SKILL_LEVEL_VALUES = ['新人', '中堅', 'ベテラン'] as const;
/** @deprecated Removed from UI in W1-FE2; backend will drop in W1-BE2. */
export const STAFF_ASSIGNMENT_VOLUME_VALUES = ['少なめ', '通常', '多め'] as const;

// ---------------------------------------------------------------------------
// Core schemas — v2 base + deprecated legacy fields kept optional/nullable
// so callers that read older API responses still parse cleanly.
// ---------------------------------------------------------------------------

const optionalCoercedLat = z.preprocess(
  (v) => (v === '' || v === null || v === undefined ? undefined : v),
  z.coerce.number().min(-90).max(90).optional(),
);
const optionalCoercedLng = z.preprocess(
  (v) => (v === '' || v === null || v === undefined ? undefined : v),
  z.coerce.number().min(-180).max(180).optional(),
);

/** Deprecated legacy field set — extend onto v2 base for migration period. */
const legacyDeprecatedSchema = z.object({
  /** @deprecated v2 §4.2: 廃止 (全員 2 名体制可能とみなす). */
  can_double_team: z.boolean().optional(),
  /** @deprecated v2 §4.2: 自宅情報廃止 (主拠点起点). */
  home_address: z.string().max(255).nullable().optional(),
  /** @deprecated v2 §4.2: 自宅情報廃止. */
  home_lat: optionalCoercedLat,
  /** @deprecated v2 §4.2: 自宅情報廃止. */
  home_lng: optionalCoercedLng,
  /** @deprecated v2 §4.2: 拠点担当エリアで代替. */
  areas: z.array(z.string()).nullable().optional(),
  /** @deprecated v2 §4.2: 全員 6 件固定. */
  max_per_day: z.coerce.number().int().min(1).max(20).optional(),
  /** @deprecated v2 §4.2: スキル概念廃止. */
  skill_level: z.enum(STAFF_SKILL_LEVEL_VALUES).nullable().optional(),
  /** @deprecated v2 §4.2: 割付ボリューム廃止. */
  assignment_volume: z.enum(STAFF_ASSIGNMENT_VOLUME_VALUES).nullable().optional(),
});

export const staffBaseSchema = staffV2BaseSchema.merge(legacyDeprecatedSchema);

export const staffCreateSchema = staffV2CreateSchema;
export const staffUpdateSchema = staffV2UpdateSchema;
export const staffReadSchema = staffV2ReadSchema.merge(legacyDeprecatedSchema);

export type StaffCreate = StaffV2Create;
export type StaffUpdate = StaffV2Update;
/** Server response — v2 fields plus deprecated legacy fields (optional). */
export type StaffRead = StaffV2Read & z.infer<typeof legacyDeprecatedSchema>;

// Re-export the v2 type aliases so callers can import them via the
// legacy module path during the transition period.
export type { StaffV2Base, RoleV2, StaffSexV2, StaffStatusV2 };

// ---------------------------------------------------------------------------
// Surrounding domain (read-only Wave 1)
//
// `staff_shifts` は v2 でも維持 (§4.2 確定済み)。それ以外 (overrides / events /
// mentor_assignments) は Wave 2 で再設計予定だが、現状は detail page から
// 参照されているため互換のため残す。
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
// Helpers (kept verbatim per W1-FE2 spec — labels updated to v2 enum values)
// ---------------------------------------------------------------------------

export const sexLabel = (sex: StaffRead['sex']): string => {
  switch (sex) {
    case 'female':
      return '女性';
    case 'male':
      return '男性';
    case 'unknown':
      return 'その他';
    default:
      return '--';
  }
};

export const roleLabel = (role: StaffRead['role'] | string | undefined | null): string => {
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
    case 'on_leave':
      return '休職';
    case 'retired':
      return '退職';
    default:
      return status ?? '--';
  }
};

/** @deprecated v2 §4.2: スキル概念廃止. */
export const skillLevelLabel = (level: StaffRead['skill_level']): string => level ?? '--';

/** @deprecated v2 §4.2: 割付ボリューム廃止. */
export const assignmentVolumeLabel = (volume: StaffRead['assignment_volume']): string =>
  volume ?? '--';

/**
 * v2 status のフォールバック判定。レガシー値 (`inactive` 等) や想定外値が
 * 来た場合は **在籍 (`active`)** にフォールバックする (W1-FE2 受入条件)。
 */
export function fallbackStaffStatus(value: unknown): StaffStatusV2 {
  return STAFF_STATUS_V2_VALUES.includes(value as StaffStatusV2)
    ? (value as StaffStatusV2)
    : 'active';
}
