/**
 * 「休み・勤務表」の月の API (`/api/v1/leave/*`) の zod schemas。
 *
 * バックエンドの `backend/app/schemas/leave.py` (LeaveMonthView ほか) と同じ形
 * (正典: docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §5)。
 *
 *   GET    /leave/month/{month}                 LeaveMonthView (管理者)
 *   GET    /leave/month/{month}/summary         LeaveMonthSummary (職員も可)
 *   GET    /leave/month/{month}/kinmu-sheets    KinmuSheetRead[]
 *   POST   /leave/day                           LeaveDayInput → LeaveDayResult
 *   POST   /leave/day/bulk                      LeaveDayBulkInput → LeaveDayBulkResult
 *   DELETE /leave/day?staff_id=&date=           LeaveCancelResult
 *   POST   /leave/requests/approve-batch        LeaveApproveBatchInput → LeaveApproveBatchResult
 *   POST   /leave/requests/{id}/approve         LeaveApproveInput → LeaveApproveResult
 *   POST   /leave/requests/{id}/reject          LeaveRejectInput → LeaveRejectResult
 *   GET    /leave/closed-days?month=            OfficeClosedDayRead[]
 *   PUT    /leave/closed-days                   OfficeClosedDayCreate → OfficeClosedDayRead
 *   DELETE /leave/closed-days/{date}            204
 *   PUT    /leave/months/{month}                LeaveMonthUpdate → LeaveMonthRead
 *   POST   /leave/month/{month}/confirm         LeaveConfirmInput → LeaveConfirmResult
 *
 * 日付は 'YYYY-MM-DD'、時刻は 'HH:MM:SS'、日時は ISO の文字列のまま扱う。
 */
import { z } from 'zod';

const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const uuid = z.string().uuid();

/** 休みの種類 (`app/core/leave.py` の LEAVE_TYPES)。 */
export const leaveTypeCodeSchema = z.enum(['off', 'am_off', 'pm_off', 'paid_off']);
export type LeaveTypeCode = z.infer<typeof leaveTypeCodeSchema>;

/** 休みの出どころ (表の「足」「カ」の印)。 */
export const leaveSourceSchema = z.enum(['request', 'admin', 'kaipoke']);
export type LeaveSource = z.infer<typeof leaveSourceSchema>;

/** 申請の種類 (変更のお願いだけ cancel = 休みを取り消してほしい)。 */
export const leaveRequestTypeSchema = z.enum(['off', 'am_off', 'pm_off', 'paid_off', 'cancel']);
export type LeaveRequestType = z.infer<typeof leaveRequestTypeSchema>;

/** normal = 締め切り前の申請／change = 変更のお願い。 */
export const leaveRequestKindSchema = z.enum(['normal', 'change']);
export type LeaveRequestKind = z.infer<typeof leaveRequestKindSchema>;

/** 休みの送信の状態: 照合前／未送信／送信済み／カイポケで要対応。 */
export const leaveSendStatusSchema = z.enum(['baseline', 'unsent', 'sent', 'kaipoke_action']);
export type LeaveSendStatus = z.infer<typeof leaveSendStatusSchema>;

export const holidaySchema = z.object({ date: isoDate, name: z.string() });
export type Holiday = z.infer<typeof holidaySchema>;

export const officeClosedDaySchema = z.object({
  date: isoDate,
  label: z.string(),
  created_by: uuid.nullable().optional(),
  created_at: z.string(),
});
export type OfficeClosedDay = z.infer<typeof officeClosedDaySchema>;

export const leaveMonthDaySchema = z.object({
  date: isoDate,
  /** 0=月〜6=日。 */
  weekday: z.number().int().min(0).max(6),
  holiday_name: z.string().nullable().optional(),
  closed_label: z.string().nullable().optional(),
  week_generated: z.boolean(),
  off_count: z.number().int(),
  off_warn: z.boolean(),
  pending_count: z.number().int(),
});
export type LeaveMonthDay = z.infer<typeof leaveMonthDaySchema>;

export const leaveMonthStaffSchema = z.object({
  id: uuid,
  order: z.number().int(),
  name: z.string(),
  code: z.string().nullable().optional(),
  employment_type: z.enum(['full_time', 'part_time']),
  is_full_time: z.boolean(),
  is_trainee: z.boolean(),
  status: z.string(),
  /** 型で勤務しない曜日 (0=月〜6=日)。 */
  off_weekdays: z.array(z.number().int().min(0).max(6)),
  holidays: z.number(),
  required_holidays: z.number().int().nullable().optional(),
  shortfall: z.number().nullable().optional(),
  paid_days: z.number().int(),
  pending_count: z.number().int(),
  pending_gain: z.number(),
  confirmed_at: z.string().nullable().optional(),
});
export type LeaveMonthStaff = z.infer<typeof leaveMonthStaffSchema>;

export const leavePendingBriefSchema = z.object({
  request_id: uuid,
  override_type: leaveRequestTypeSchema,
  kind: leaveRequestKindSchema,
});

export const leaveMonthCellSchema = z.object({
  staff_id: uuid,
  date: isoDate,
  leave_type: leaveTypeCodeSchema.nullable().optional(),
  source: leaveSourceSchema.nullable().optional(),
  note: z.string().nullable().optional(),
  approved_request_id: uuid.nullable().optional(),
  time_change_start: z.string().nullable().optional(),
  time_change_end: z.string().nullable().optional(),
  pending: leavePendingBriefSchema.nullable().optional(),
  cancelled_after_approval: z.boolean().default(false),
  visit_count: z.number().int().default(0),
  send_status: leaveSendStatusSchema.nullable().optional(),
});
export type LeaveMonthCell = z.infer<typeof leaveMonthCellSchema>;

export const leavePendingItemSchema = z.object({
  id: uuid,
  staff_id: uuid,
  staff_name: z.string(),
  staff_order: z.number().int(),
  date: isoDate,
  override_type: leaveRequestTypeSchema,
  kind: leaveRequestKindSchema,
  note: z.string().nullable().optional(),
  created_at: z.string(),
  current_leave_type: leaveTypeCodeSchema.nullable().optional(),
  past_day: z.boolean(),
  move_visit_count: z.number().int(),
  off_count_now: z.number().int(),
  off_count_if_approved: z.number().int(),
});
export type LeavePendingItem = z.infer<typeof leavePendingItemSchema>;

export const leaveCompareStatusSchema = z.object({
  last_compared_at: z.string().nullable().optional(),
  last_compare_run_id: uuid.nullable().optional(),
  diff_count: z.number().int().nullable().optional(),
  planned_send_count: z.number().int().nullable().optional(),
  unconfirmed_days: z.number().int().nullable().optional(),
  last_leave_changed_at: z.string().nullable().optional(),
  changed_since_compare: z.boolean(),
  baseline_pending_count: z.number().int(),
});
export type LeaveCompareStatus = z.infer<typeof leaveCompareStatusSchema>;

export const leaveMonthConfirmationSchema = z.object({
  confirmed: z.boolean(),
  confirmed_staff_count: z.number().int(),
  staff_count: z.number().int(),
  last_confirmed_at: z.string().nullable().optional(),
  changed_since_confirm: z.boolean(),
});
export type LeaveMonthConfirmation = z.infer<typeof leaveMonthConfirmationSchema>;

export const leaveMonthViewSchema = z.object({
  month: isoDate,
  today: isoDate,
  required_holidays: z.number().int(),
  required_hours: z.number().nullable().optional(),
  previous_required_hours: z.number().nullable().optional(),
  off_warn_threshold: z.number().int(),
  days: z.array(leaveMonthDaySchema),
  staff: z.array(leaveMonthStaffSchema),
  cells: z.array(leaveMonthCellSchema),
  pending: z.array(leavePendingItemSchema),
  holidays: z.array(holidaySchema),
  closed_days: z.array(officeClosedDaySchema),
  confirmation: leaveMonthConfirmationSchema,
  compare: leaveCompareStatusSchema,
  visits_on_leave_count: z.number().int(),
});
export type LeaveMonthView = z.infer<typeof leaveMonthViewSchema>;

export const leaveMonthSummarySchema = z.object({
  month: isoDate,
  holidays: z.array(holidaySchema),
  closed_days: z.array(holidaySchema),
  request_deadline: z.string(),
  confirmed: z.boolean(),
  confirmed_at: z.string().nullable().optional(),
  /**
   * 管理者だけ (職員には null): 確定済みの人数と表の人数・申請中の件数 (メニューの未処理件数)・
   * 必要休日数に足りない常勤の人数・カイポケで要対応の件数。
   */
  confirmed_staff_count: z.number().int().nullable().optional(),
  staff_count: z.number().int().nullable().optional(),
  pending_count: z.number().int().nullable().optional(),
  short_staff_count: z.number().int().nullable().optional(),
  kaipoke_action_count: z.number().int().nullable().optional(),
});
export type LeaveMonthSummary = z.infer<typeof leaveMonthSummarySchema>;

export const leaveDayInputSchema = z.object({
  staff_id: uuid,
  date: isoDate,
  leave_type: leaveTypeCodeSchema,
  note: z.string().max(500).nullable().optional(),
});
export type LeaveDayInput = z.infer<typeof leaveDayInputSchema>;

export const leaveDayResultSchema = z.object({
  staff_id: uuid,
  date: isoDate,
  leave_type: leaveTypeCodeSchema,
  past_day: z.boolean(),
  moved_visit_count: z.number().int(),
  moved_patient_count: z.number().int(),
  skipped_visit_count: z.number().int(),
  cancelled_event_count: z.number().int(),
  accompaniment_count: z.number().int(),
});
export type LeaveDayResult = z.infer<typeof leaveDayResultSchema>;

export const leaveCancelResultSchema = z.object({
  staff_id: uuid,
  date: isoDate,
  cancelled_type: leaveTypeCodeSchema,
  /** この休みで担当なしへ移した訪問の件数 (自動では戻らない)。 */
  moved_visit_count: z.number().int().default(0),
});
export type LeaveCancelResult = z.infer<typeof leaveCancelResultSchema>;

/** 「この日の全員に公休を入れる」(staff_ids を省くとサーバーが相手を決める)。 */
export const leaveDayBulkInputSchema = z.object({
  date: isoDate,
  override_type: leaveTypeCodeSchema.default('off'),
  staff_ids: z.array(uuid).max(200).nullable().optional(),
  note: z.string().max(500).nullable().optional(),
});
export type LeaveDayBulkInput = z.input<typeof leaveDayBulkInputSchema>;

export const leaveDayBulkResultSchema = z.object({
  date: isoDate,
  override_type: leaveTypeCodeSchema,
  applied: z.array(leaveDayResultSchema),
  /** 休みがすでに入っていて飛ばした人 (staff_ids を渡したとき)。 */
  skipped: z
    .array(
      z.object({
        staff_id: uuid,
        reason: z.literal('already_on_leave'),
        leave_type: leaveTypeCodeSchema.nullable().optional(),
      }),
    )
    .default([]),
  moved_visit_count: z.number().int(),
});
export type LeaveDayBulkResult = z.infer<typeof leaveDayBulkResultSchema>;

export const leaveApproveResultSchema = z.object({
  request_id: uuid,
  staff_id: uuid,
  date: isoDate,
  override_type: leaveRequestTypeSchema,
  result: leaveDayResultSchema.nullable().optional(),
});
export type LeaveApproveResult = z.infer<typeof leaveApproveResultSchema>;

export const leaveApproveBatchResultSchema = z.object({
  approved: z.array(leaveApproveResultSchema),
  moved_visit_count: z.number().int(),
});
export type LeaveApproveBatchResult = z.infer<typeof leaveApproveBatchResultSchema>;

export const leaveRejectResultSchema = z.object({
  request_id: uuid,
  target_staff_id: uuid.nullable().optional(),
  target_date: isoDate.nullable().optional(),
  reason: z.string(),
});
export type LeaveRejectResult = z.infer<typeof leaveRejectResultSchema>;

export const leaveMonthReadSchema = z.object({
  month: isoDate,
  required_hours: z.number().nullable().optional(),
  last_leave_changed_at: z.string().nullable().optional(),
  last_compared_at: z.string().nullable().optional(),
  last_compare_run_id: uuid.nullable().optional(),
  actual_recorded_at: z.string().nullable().optional(),
  actual_recorded_by: uuid.nullable().optional(),
});
export type LeaveMonthRead = z.infer<typeof leaveMonthReadSchema>;

export const leaveShortStaffSchema = z.object({
  staff_id: uuid,
  name: z.string(),
  holidays: z.number(),
  required_holidays: z.number().int(),
  shortfall: z.number(),
});

/** 確定の本文 (任意)。staff_ids が無ければ表の全員。 */
export const leaveConfirmInputSchema = z.object({
  staff_ids: z.array(uuid).min(1).max(200).nullable().optional(),
});
export type LeaveConfirmInput = z.infer<typeof leaveConfirmInputSchema>;

export const kinmuSheetSchema = z.object({
  id: uuid,
  month: isoDate,
  kind: z.enum(['plan', 'final']),
  version: z.number().int(),
  drive_file_id: z.string().nullable().optional(),
  url: z.string().nullable().optional(),
  reason: z.string().nullable().optional(),
  created_by: uuid.nullable().optional(),
  created_at: z.string(),
});
export type KinmuSheet = z.infer<typeof kinmuSheetSchema>;

export const leaveConfirmResultSchema = z.object({
  month: isoDate,
  confirmed_at: z.string(),
  confirmed_staff_count: z.number().int(),
  notified_count: z.number().int(),
  pending_remaining: z.number().int(),
  short_staff: z.array(leaveShortStaffSchema),
  required_hours: z.number().nullable().optional(),
});
export type LeaveConfirmResult = z.infer<typeof leaveConfirmResultSchema>;
