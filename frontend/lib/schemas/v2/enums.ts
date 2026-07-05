/**
 * v2 共有 enum 定義 (Wave 0-C).
 *
 * 設計仕様書 v0.9:
 *   - §4.5: CourseStatus (`proposed` / `course_fixed` / `staff_assigned`)
 *   - §4.4: RequestType / RequestStatus / RequestScope
 *
 * これらは backend `app/schemas/v2/enums.py` と **完全一致** させる
 * (API 契約の前提)。値を増減する場合は両方同時に更新する。
 */
import { z } from 'zod';

// ─────────────────────────────────────────────────────────────────────────
// CourseStatus (§4.5)
//   遷移: proposed → course_fixed → staff_assigned → (週終了でアーカイブ)
// ─────────────────────────────────────────────────────────────────────────
export const COURSE_STATUS_VALUES = ['proposed', 'course_fixed', 'staff_assigned'] as const;
export const courseStatusEnum = z.enum(COURSE_STATUS_VALUES);
export type CourseStatus = z.infer<typeof courseStatusEnum>;

// ─────────────────────────────────────────────────────────────────────────
// RequestType (§4.4)
//   9 種類 (`docs/plans/v2-api-contracts.md` §9 対応表参照)
// ─────────────────────────────────────────────────────────────────────────
export const REQUEST_TYPE_VALUES = [
  'staff_off',
  'staff_event',
  'staff_mentor',
  'staff_create',
  'patient_create',
  'patient_cancel',
  'patient_reschedule',
  'patient_special_week_on',
  'patient_special_week_off',
  'staff_status_update',
  'patient_status_update',
  // Phase G-84: 現場ボードの空き枠への既存患者の直接配置 (normal PFV に 1 枠マージ)。
  'patient_visit_add',
] as const;
export const requestTypeEnum = z.enum(REQUEST_TYPE_VALUES);
export type RequestType = z.infer<typeof requestTypeEnum>;

// ─────────────────────────────────────────────────────────────────────────
// RequestStatus (§3.5.4 / §4.4)
//   `pending` で作成され、`approved` (即時 / 後追い) または `rejected` に遷移。
// ─────────────────────────────────────────────────────────────────────────
export const REQUEST_STATUS_VALUES = ['pending', 'approved', 'rejected'] as const;
export const requestStatusEnum = z.enum(REQUEST_STATUS_VALUES);
export type RequestStatus = z.infer<typeof requestStatusEnum>;

// ─────────────────────────────────────────────────────────────────────────
// RequestScope (§3.5.6 / §4.4)
//   patient_reschedule のときのみ必須。それ以外の request_type では NULL。
// ─────────────────────────────────────────────────────────────────────────
export const REQUEST_SCOPE_VALUES = ['one_time', 'permanent'] as const;
export const requestScopeEnum = z.enum(REQUEST_SCOPE_VALUES);
export type RequestScope = z.infer<typeof requestScopeEnum>;
