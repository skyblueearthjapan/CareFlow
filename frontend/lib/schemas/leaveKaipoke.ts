/**
 * 休みのカイポケとの照合と送信 (`/api/v1/leave/compare/*`・`/api/v1/leave/outbound/*`) の zod schemas。
 *
 * 正典はコード: `backend/app/api/v1/leave_kaipoke.py`・`services/leave_compare.py`
 * (LeaveDiff.as_dict)・`services/kaipoke/leave_outbound.py` (LeaveSendPlan.as_dict ほか)。
 * 設計 docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §7-4。
 */
import { z } from 'zod';

const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const kindCode = z.enum(['off', 'am_off', 'pm_off', 'paid_off']);

/** 差分の合わせ方 (`leave_compare.ACTION_*`)。null = ボタンなし (カイポケで合わせて比べ直す)。 */
export const compareActionSchema = z.enum([
  'add_to_rakusuke',
  'match_kaipoke_kind',
  'cancel_rakusuke',
  'choose_kind',
  'refetch_week',
]);
export type CompareAction = z.infer<typeof compareActionSchema>;

/** 種類がわからない予定に選ぶ種類 (`not_leave` = 休みではない)。 */
export const chosenKindSchema = z.enum(['not_leave', 'off', 'am_off', 'pm_off', 'paid_off']);
export type ChosenKind = z.infer<typeof chosenKindSchema>;

/** KaipokeJob の進み具合 (`_job_view`)。 */
export const kaipokeJobViewSchema = z.object({
  id: z.string(),
  op: z.string().nullable().optional(),
  /** pending / running / completed / failed / cancelled */
  status: z.string(),
  month: z.string().nullable().optional(),
  done: z.number().int().default(0),
  total: z.number().int().nullable().optional(),
  current: z.unknown().optional(),
  results: z.array(z.unknown()).default([]),
  failed: z.array(z.unknown()).default([]),
  error: z.string().nullable().optional(),
  stale: z.boolean().default(false),
  started_at: z.string().nullable().optional(),
  completed_at: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
});
export type KaipokeJobView = z.infer<typeof kaipokeJobViewSchema>;

/** 差分 1 件 (`LeaveDiff.as_dict`)。合わせ済みなら `resolved` (誰が・いつ・どう)。 */
export const leaveDiffSchema = z.object({
  kind: z.string(),
  case: z.number().int().nullable().optional(),
  staff_id: z.string().nullable().optional(),
  staff_name: z.string().default(''),
  date: isoDate.nullable().optional(),
  rakusuke: kindCode.nullable().optional(),
  sent: kindCode.nullable().optional(),
  kaipoke: kindCode.nullable().optional(),
  baseline_pending: z.boolean().default(false),
  action: compareActionSchema.nullable().optional(),
  message: z.string(),
  detail: z.record(z.unknown()).default({}),
  resolved: z
    .object({
      action: z.string(),
      by: z.string().nullable().optional(),
      at: z.string().nullable().optional(),
      chosen_kind: z.string().nullable().optional(),
    })
    .passthrough()
    .nullable()
    .optional(),
});
export type LeaveDiff = z.infer<typeof leaveDiffSchema>;

/** 照合の結果 (`_run_view`)。 */
export const leaveCompareRunViewSchema = z.object({
  id: z.string(),
  month: isoDate,
  kaipoke_job_id: z.string().nullable().optional(),
  fetched_at: z.record(z.unknown()).nullable().optional(),
  diffs: z.array(leaveDiffSchema).default([]),
  auto_updates: z.array(z.unknown()).default([]),
  diff_count: z.number().int(),
  planned_send_count: z.number().int(),
  unconfirmed_days: z.number().int(),
  created_at: z.string().nullable().optional(),
});
export type LeaveCompareRunView = z.infer<typeof leaveCompareRunViewSchema>;

/** POST /compare/start → 202。 */
export const compareStartSchema = z.object({
  job_id: z.string(),
  status: z.string(),
  month: z.string(),
  total: z.number().int(),
  evening_warning: z.string().nullable().optional(),
});
export type CompareStart = z.infer<typeof compareStartSchema>;

/** GET /compare/latest?month= */
export const compareLatestSchema = z.object({
  month: z.string(),
  run: leaveCompareRunViewSchema.nullable().optional(),
  job: kaipokeJobViewSchema.nullable().optional(),
  last_compared_at: z.string().nullable().optional(),
  last_leave_changed_at: z.string().nullable().optional(),
  changed_since_compare: z.boolean(),
  evening_warning: z.string().nullable().optional(),
});
export type CompareLatest = z.infer<typeof compareLatestSchema>;

export const compareStateSchema = z.enum([
  'fetching',
  'comparing',
  'done',
  'compare_failed',
  'failed',
  'cancelled',
]);
export type CompareState = z.infer<typeof compareStateSchema>;

/** GET /compare/{job_id} */
export const compareJobStatusSchema = z.object({
  job: kaipokeJobViewSchema,
  state: compareStateSchema,
  run: leaveCompareRunViewSchema.nullable().optional(),
  compare_error: z.string().nullable().optional(),
});
export type CompareJobStatus = z.infer<typeof compareJobStatusSchema>;

/** POST /compare/apply の 1 件。 */
export interface CompareApplyItem {
  staff_id: string;
  date: string;
  action: Exclude<CompareAction, 'refetch_week'>;
  content_hash?: string | null;
  chosen_kind?: ChosenKind | null;
}

export const compareApplyResultSchema = z.object({
  applied: z.array(z.record(z.unknown())),
  recompare_needed: z.boolean(),
  moved_visit_count: z.number().int(),
  notified: z.number().int(),
});
export type CompareApplyResult = z.infer<typeof compareApplyResultSchema>;

/** 送ったあとに変わった休み (カイポケで要対応)。 */
export const leaveNeedsActionSchema = z.object({
  staff_id: z.string(),
  staff_name: z.string(),
  date: isoDate,
  rakusuke: kindCode.nullable().optional(),
  sent: z.string(),
  message: z.string(),
});
export type LeaveNeedsAction = z.infer<typeof leaveNeedsActionSchema>;

/** 送る前の一覧の 1 行 (`LeaveSendItem.as_dict`)。 */
export const leaveSendItemSchema = z.object({
  staff_id: z.string(),
  staff_name: z.string(),
  date: isoDate,
  leave_type: kindCode,
  title: z.string(),
  start: z.string().nullable().optional(),
  end: z.string().nullable().optional(),
  sendable: z.boolean(),
  reason: z.string().nullable().optional(),
  record_id: z.string().nullable().optional(),
});
export type LeaveSendItem = z.infer<typeof leaveSendItemSchema>;

/** GET /outbound/preview?month= */
export const outboundPreviewSchema = z.object({
  month: z.string(),
  last_compared_at: z.string().nullable().optional(),
  blocked_reason: z.string().nullable().optional(),
  items: z.array(leaveSendItemSchema),
  sendable_count: z.number().int(),
  needs_action: z.array(leaveNeedsActionSchema).default([]),
  baseline_count: z.number().int().default(0),
  sent_count: z.number().int().default(0),
  evening_warning: z.string().nullable().optional(),
});
export type OutboundPreview = z.infer<typeof outboundPreviewSchema>;

/** POST /outbound/start → 202。 */
export const outboundStartSchema = z.object({
  job_id: z.string(),
  status: z.string(),
  month: z.string(),
  count: z.number().int(),
  total: z.number().int(),
  needs_action: z.array(leaveNeedsActionSchema).default([]),
  evening_warning: z.string().nullable().optional(),
});
export type OutboundStart = z.infer<typeof outboundStartSchema>;

/** 送信の 1 件の結果 (`_outcome`)。outcome = sent / failed / skipped_changed。 */
export const outboundOutcomeSchema = z.object({
  staff_id: z.string(),
  staff_name: z.string().nullable().optional(),
  date: isoDate,
  leave_type: z.string(),
  title: z.string(),
  start: z.string().nullable().optional(),
  end: z.string().nullable().optional(),
  outcome: z.string(),
  external_key: z.string().nullable().optional(),
  reason: z.string().nullable().optional(),
});
export type OutboundOutcome = z.infer<typeof outboundOutcomeSchema>;

/** GET /outbound/{job_id} */
export const outboundJobStatusSchema = z.object({
  job: kaipokeJobViewSchema,
  count: z.number().int(),
  items: z.array(outboundOutcomeSchema).default([]),
  sent_count: z.number().int(),
  failed_count: z.number().int(),
  skipped_count: z.number().int(),
  needs_action: z.array(leaveNeedsActionSchema).default([]),
});
export type OutboundJobStatus = z.infer<typeof outboundJobStatusSchema>;

/** ジョブが動いている (pending / running)。 */
export function isJobActive(job: { status: string } | null | undefined): boolean {
  return job?.status === 'pending' || job?.status === 'running';
}
