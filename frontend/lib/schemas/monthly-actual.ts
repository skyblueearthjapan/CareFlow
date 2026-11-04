/**
 * 月の実績 (`/api/v1/monthly-actual/*`・管理者) の zod schemas と画面向けの型。
 *
 * 正典はコード: `backend/app/api/v1/monthly_actual.py`・`services/monthly_actual/service.py`
 * (`row_view`・`run_view`・`job_view`)・`services/monthly_actual/judge.py` (判定の決まり)。
 * 設計 docs/plans/kaipoke-actual-write-design-2026-11-01.md §5-3。
 *
 * BE の応答 (`raw*Schema`) を読み、画面で使う形 (`MonthlyActualRow`・`MonthlyActualList`) へ
 * 変える (`toMonthlyActualRow`・`toMonthlyActualList`)。名前の対応:
 *   rk_arrival → rk_start / rk_departure → rk_end / k2_* → record2_* /
 *   patient → patient_name / selected → apply / apply_status → result_status /
 *   status 'missing' → 'missing_in_kaipoke' / flags[].label → flags (文字列) /
 *   care_record_* → record2_result_* / needs_care_record → needs_record2。
 *
 * 記録書Ⅱ (設計 docs/plans/kaipoke-kiroku2-time-design-2026-11-04.md §2-2): 実績を反映するとき、
 * 記録書Ⅱの「実際の訪問時間」も同じ時刻にそろえる。'k2_only' は実績はもう QR どおりで
 * 記録書Ⅱだけ違う行。結果は実績 (result_*) と記録書Ⅱ (record2_result_*) を別に持つ。
 */
import { z } from 'zod';

const hm = z.string().nullable().optional();
const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);

/** 判定 (BE の値)。 */
export const rawStatusSchema = z.enum([
  'match',
  'apply',
  'k2_only',
  'review',
  'missing',
  'kaipoke_only',
]);

/** 判定 (画面の値)。 */
export type MonthlyActualStatus =
  | 'match'
  | 'apply'
  | 'k2_only'
  | 'review'
  | 'missing_in_kaipoke'
  | 'kaipoke_only';

export const MONTHLY_ACTUAL_STATUS_LABELS: Record<MonthlyActualStatus, string> = {
  match: '一致',
  apply: '反映する',
  k2_only: '記録書Ⅱだけ',
  review: '人が見る',
  missing_in_kaipoke: 'カイポケに無い',
  kaipoke_only: 'カイポケだけ',
};

/** 印 (`judge.Flag.as_dict`)。review=true の印が付くと「人が見る」。 */
export const rawFlagSchema = z.object({
  code: z.string(),
  label: z.string(),
  review: z.boolean(),
});
export type MonthlyActualFlag = z.infer<typeof rawFlagSchema>;

/** 行 (`service.row_view`)。 */
export const rawRowSchema = z.object({
  id: z.string(),
  seq: z.number().int(),
  status: rawStatusSchema,
  status_label: z.string(),
  flags: z.array(rawFlagSchema).default([]),
  item_key: z.string().nullable().optional(),
  date: isoDate,
  patient: z.string(),
  patient_code: z.string().nullable().optional(),
  visit_id: z.string().nullable().optional(),
  rk_staff: z.string().nullable().optional(),
  rk_arrival: hm,
  rk_departure: hm,
  rk_adjusted: z.string().nullable().optional(),
  rk_stay: z.number().int().nullable().optional(),
  new_start: hm,
  new_end: hm,
  kp_patient: z.string().nullable().optional(),
  kp_start: hm,
  kp_end: hm,
  kp_staff1: z.string().nullable().optional(),
  kp_staff: z.string().nullable().optional(),
  kp_service: z.string().nullable().optional(),
  kp_minutes: z.number().int().nullable().optional(),
  k2_start: hm,
  k2_end: hm,
  diff_start: z.number().int().nullable().optional(),
  diff_end: z.number().int().nullable().optional(),
  selectable: z.boolean(),
  selected: z.boolean(),
  selected_by_user: z.boolean().default(false),
  /** queued / success / dry_run_ok / skipped / failed / error */
  apply_status: z.string().nullable().optional(),
  apply_reason: z.string().nullable().optional(),
  apply_message: z.string().nullable().optional(),
  apply_dry_run: z.boolean().nullable().optional(),
  applied_at: z.string().nullable().optional(),
  /** 書き換えたか分からない結果 (カイポケで確かめてから・流し直さない)。 */
  needs_kaipoke_check: z.boolean().default(false),
  /** 今の鍵と新しい時刻で試しを通った (本番はこの行だけ送れる)。 */
  dry_run_passed: z.boolean().default(false),
  /** カイポケの実績の時刻が新しい時刻と違う。 */
  needs_actual: z.boolean().default(true),
  /** 記録書Ⅱがあり、時刻が新しい時刻と違う (記録書Ⅱもそろえる)。 */
  needs_care_record: z.boolean().default(false),
  /** 記録書Ⅱの結果 (status・reason は RPA の値。知らない reason もそのまま出す)。 */
  care_record_status: z.string().nullable().optional(),
  care_record_reason: z.string().nullable().optional(),
  care_record_message: z.string().nullable().optional(),
  care_record_dry_run: z.boolean().nullable().optional(),
});
export type RawMonthlyActualRow = z.infer<typeof rawRowSchema>;

/** 画面で使う行。 */
export interface MonthlyActualRow {
  id: string;
  date: string;
  patient_code: string | null;
  patient_name: string;
  rk_staff: string | null;
  /** QR の到着 (後から合わせた時刻を含む)。 */
  rk_start: string | null;
  /** QR の退出。 */
  rk_end: string | null;
  rk_stay: number | null;
  /** 後から合わせた時刻 (「到着」「退出」「到着・退出」)。 */
  rk_adjusted: string | null;
  kp_patient: string | null;
  kp_start: string | null;
  kp_end: string | null;
  kp_minutes: number | null;
  kp_staff: string | null;
  kp_service: string | null;
  /** 看護記録書Ⅱの時刻 (カイポケの実績の横)。 */
  record2_start: string | null;
  record2_end: string | null;
  status: MonthlyActualStatus;
  status_label: string;
  /** 印の文言。 */
  flags: string[];
  /** 印の詳細 (人が見る印かどうか)。 */
  flag_details: MonthlyActualFlag[];
  /** 反映する (管理者が変えられる)。 */
  apply: boolean;
  /** 反映する／しないを選べる行か。 */
  selectable: boolean;
  /** 管理者が選び直した行か。 */
  apply_changed_by_user: boolean;
  new_start: string | null;
  new_end: string | null;
  /** 到着の差・退出の差 (らく助 − カイポケ・分)。 */
  diff_start: number | null;
  diff_end: number | null;
  /** RPA の結果 (queued / success / dry_run_ok / skipped / failed / error / unknown)。 */
  result_status: string | null;
  /** RPA の理由コード (ACTUAL_TIME_EDIT_SPEC.md の reason)。 */
  result_reason: string | null;
  result_message: string | null;
  result_dry_run: boolean | null;
  /** 書き換えたか分からない結果 (カイポケで確かめてから・流し直さない)。 */
  needs_kaipoke_check: boolean;
  /** 今の鍵と新しい時刻で試しを通った。 */
  dry_run_passed: boolean;
  /** カイポケの実績の時刻が新しい時刻と違う (実績を書き換える)。 */
  needs_actual: boolean;
  /** 記録書Ⅱがあり、時刻が新しい時刻と違う (記録書Ⅱもそろえる)。 */
  needs_record2: boolean;
  /** 記録書Ⅱの結果 (queued / success / dry_run_ok / skipped / failed / error / unknown)。 */
  record2_result_status: string | null;
  record2_result_reason: string | null;
  record2_result_message: string | null;
  record2_result_dry_run: boolean | null;
}

export function toMonthlyActualStatus(
  status: z.infer<typeof rawStatusSchema>,
): MonthlyActualStatus {
  return status === 'missing' ? 'missing_in_kaipoke' : status;
}

export function toMonthlyActualRow(r: RawMonthlyActualRow): MonthlyActualRow {
  return {
    id: r.id,
    date: r.date,
    patient_code: r.patient_code ?? null,
    patient_name: r.patient,
    rk_staff: r.rk_staff ?? null,
    rk_start: r.rk_arrival ?? null,
    rk_end: r.rk_departure ?? null,
    rk_stay: r.rk_stay ?? null,
    rk_adjusted: r.rk_adjusted ?? null,
    kp_patient: r.kp_patient ?? null,
    kp_start: r.kp_start ?? null,
    kp_end: r.kp_end ?? null,
    kp_minutes: r.kp_minutes ?? null,
    kp_staff: r.kp_staff ?? null,
    kp_service: r.kp_service ?? null,
    record2_start: r.k2_start ?? null,
    record2_end: r.k2_end ?? null,
    status: toMonthlyActualStatus(r.status),
    status_label: r.status_label,
    flags: r.flags.map((f) => f.label),
    flag_details: r.flags,
    apply: r.selected,
    selectable: r.selectable,
    apply_changed_by_user: r.selected_by_user,
    new_start: r.new_start ?? null,
    new_end: r.new_end ?? null,
    diff_start: r.diff_start ?? null,
    diff_end: r.diff_end ?? null,
    result_status: r.apply_status ?? null,
    result_reason: r.apply_reason ?? null,
    result_message: r.apply_message ?? null,
    result_dry_run: r.apply_dry_run ?? null,
    needs_kaipoke_check: r.needs_kaipoke_check,
    dry_run_passed: r.dry_run_passed,
    needs_actual: r.needs_actual,
    needs_record2: r.needs_care_record,
    record2_result_status: r.care_record_status ?? null,
    record2_result_reason: r.care_record_reason ?? null,
    record2_result_message: r.care_record_message ?? null,
    record2_result_dry_run: r.care_record_dry_run ?? null,
  };
}

/** KaipokeJob の進み具合 (`service.job_view`)。 */
export const rawJobSchema = z.object({
  id: z.string(),
  op: z.string().nullable().optional(),
  /** pending / running / completed / failed / cancelled */
  status: z.string(),
  dry_run: z.boolean().nullable().optional(),
  total: z.number().int().nullable().optional(),
  progress: z.record(z.string(), z.unknown()).nullable().optional(),
  counts: z.record(z.string(), z.number()).nullable().optional(),
  care_record_counts: z.record(z.string(), z.number()).nullable().optional(),
  /** いま RPA で動いている口 (actual = 実績と続けて記録書Ⅱ・care_record = 記録書Ⅱだけ)。 */
  phase: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
  started_at: z.string().nullable().optional(),
  completed_at: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
});
export type MonthlyActualJob = z.infer<typeof rawJobSchema>;

const rawCountsSchema = z.object({
  match: z.number().int().default(0),
  apply: z.number().int().default(0),
  k2_only: z.number().int().default(0),
  review: z.number().int().default(0),
  missing: z.number().int().default(0),
  kaipoke_only: z.number().int().default(0),
  total: z.number().int().default(0),
  /** 記録書Ⅱが新しい時刻と違う行の数 (実績と続けて・記録書Ⅱだけ の両方)。 */
  care_record: z.number().int().default(0),
});

const applyCountsSchema = z.object({
  success: z.number().int().default(0),
  dry_run_ok: z.number().int().default(0),
  skipped: z.number().int().default(0),
  failed: z.number().int().default(0),
  error: z.number().int().default(0),
  queued: z.number().int().default(0),
  unknown: z.number().int().default(0),
});
export type MonthlyActualApplyCounts = z.infer<typeof applyCountsSchema>;

/** 最後の反映の結果 (`run.last_apply`)。 */
const lastApplySchema = z
  .object({
    job_id: z.string(),
    dry_run: z.boolean(),
    finished_at: z.string(),
    counts: z.record(z.string(), z.number()),
    care_record_counts: z.record(z.string(), z.number()).nullable().optional(),
    error: z.string().nullable().optional(),
  })
  .nullable()
  .optional();

/** 反映の後に読み取り直して確かめた結果 (`run.verify_result`)。 */
const verifySchema = z
  .object({
    checked_at: z.string(),
    kaipoke_fetched_at: z.string().nullable().optional(),
    applied: z.number().int().optional(),
    matched: z.number().int().optional(),
    actual_applied: z.number().int().optional(),
    actual_matched: z.number().int().optional(),
    care_record_applied: z.number().int().optional(),
    care_record_matched: z.number().int().optional(),
    not_matched: z.array(z.record(z.string(), z.unknown())).optional(),
    error: z.string().nullable().optional(),
  })
  .nullable()
  .optional();

/** 作業 (`service.run_view`)。state: judged / applying / applied / verified / failed。 */
export const rawRunSchema = z.object({
  id: z.string(),
  month: z.string(),
  state: z.string(),
  snapshot_id: z.string().nullable().optional(),
  kaipoke_fetched_at: z.string().nullable().optional(),
  judged_at: z.string().nullable().optional(),
  counts: rawCountsSchema,
  last_apply: lastApplySchema,
  verify_result: verifySchema,
  scheduled_at: z.string().nullable().optional(),
  scheduled_dry_run: z.boolean().default(false),
  refresh_job: rawJobSchema.nullable().optional(),
  apply_job: rawJobSchema.nullable().optional(),
});
export type RawMonthlyActualRun = z.infer<typeof rawRunSchema>;

/** GET /monthly-actual/{month} の BE の応答。 */
export const rawListSchema = z.object({
  month: z.string(),
  is_past_month: z.boolean(),
  kaipoke: z
    .object({
      snapshot_id: z.string(),
      fetched_at: z.string().nullable().optional(),
      row_count: z.number().int(),
      source_op: z.string(),
    })
    .nullable(),
  run: rawRunSchema.nullable(),
  counts: rawCountsSchema,
  selected_count: z.number().int(),
  apply_counts: applyCountsSchema,
  care_record_counts: applyCountsSchema.default({}),
  rows: z.array(rawRowSchema),
  evening_warning: z.string().nullable().optional(),
});

export interface MonthlyActualCounts {
  match: number;
  apply: number;
  /** 実績はもう QR どおりで、記録書Ⅱだけ違う行。 */
  k2_only: number;
  review: number;
  missing_in_kaipoke: number;
  kaipoke_only: number;
  total: number;
  /** 記録書Ⅱもそろえる行 (実績と続けて・記録書Ⅱだけ の両方)。 */
  record2: number;
}

/** 反映の作業のまとめ (一覧の上に出すもの)。 */
export interface MonthlyActualJobSummary {
  /** judged / applying / applied / verified / failed */
  state: string;
  /** 最後に送った件数。 */
  total: number;
  /** 成功 (試しなら登録の直前まで進めた件数)。 */
  success: number;
  /** 失敗 (failed + error)。 */
  failed: number;
  /** 記録書Ⅱの成功 (試しなら保存の直前まで進めた件数)・失敗。 */
  record2_success: number;
  record2_failed: number;
  dry_run: boolean | null;
  scheduled_at?: string | null;
  scheduled_dry_run: boolean;
  /** 反映のジョブが動いているか。 */
  running: boolean;
  error: string | null;
}

/** 画面で使う一覧。 */
export interface MonthlyActualList {
  month: string;
  /** カイポケの実績を読み取った時刻 (まだなら null)。 */
  kp_fetched_at: string | null;
  counts: MonthlyActualCounts;
  rows: MonthlyActualRow[];
  job?: MonthlyActualJobSummary;
  /** 終わった月か (反映は終わった月だけ)。 */
  is_past_month: boolean;
  /** いま送る行の数 (選んだ・送れる・まだ反映していない)。 */
  selected_count: number;
  /** カイポケの実績を取り直すジョブが動いているか。 */
  refreshing: boolean;
  refresh_error: string | null;
  verify_result: z.infer<typeof verifySchema> | null;
  evening_warning: string | null;
}

export function isJobRunning(job: MonthlyActualJob | null | undefined): boolean {
  return !!job && (job.status === 'pending' || job.status === 'running');
}

function toCounts(c: z.infer<typeof rawCountsSchema>): MonthlyActualCounts {
  return {
    match: c.match,
    apply: c.apply,
    k2_only: c.k2_only,
    review: c.review,
    missing_in_kaipoke: c.missing,
    kaipoke_only: c.kaipoke_only,
    total: c.total,
    record2: c.care_record,
  };
}

function toJobSummary(
  run: RawMonthlyActualRun,
  applyCounts: MonthlyActualApplyCounts,
  record2Counts: MonthlyActualApplyCounts,
): MonthlyActualJobSummary {
  const job = run.apply_job ?? null;
  const dryRun = job?.dry_run ?? run.last_apply?.dry_run ?? null;
  return {
    state: run.state,
    total: job?.total ?? 0,
    success: dryRun ? applyCounts.dry_run_ok : applyCounts.success,
    failed: applyCounts.failed + applyCounts.error,
    record2_success: dryRun ? record2Counts.dry_run_ok : record2Counts.success,
    record2_failed: record2Counts.failed + record2Counts.error,
    dry_run: dryRun,
    scheduled_at: run.scheduled_at ?? null,
    scheduled_dry_run: run.scheduled_dry_run,
    running: isJobRunning(job),
    error: job?.error ?? run.last_apply?.error ?? null,
  };
}

export function toMonthlyActualList(raw: z.infer<typeof rawListSchema>): MonthlyActualList {
  const run = raw.run;
  return {
    month: raw.month,
    kp_fetched_at: run?.kaipoke_fetched_at ?? raw.kaipoke?.fetched_at ?? null,
    counts: toCounts(raw.counts),
    rows: raw.rows.map(toMonthlyActualRow),
    job: run ? toJobSummary(run, raw.apply_counts, raw.care_record_counts) : undefined,
    is_past_month: raw.is_past_month,
    selected_count: raw.selected_count,
    refreshing: isJobRunning(run?.refresh_job),
    refresh_error: run?.refresh_job?.error ?? null,
    verify_result: run?.verify_result ?? null,
    evening_warning: raw.evening_warning ?? null,
  };
}

export function parseMonthlyActualList(raw: unknown): MonthlyActualList {
  return toMonthlyActualList(rawListSchema.parse(raw));
}

/** POST /refresh・/apply の応答 (202)。 */
export const jobAcceptedSchema = z.object({
  job_id: z.string(),
  status: z.string(),
  month: z.string(),
  dry_run: z.boolean().optional(),
  total: z.number().int().optional(),
  evening_warning: z.string().nullable().optional(),
});
export type MonthlyActualJobAccepted = z.infer<typeof jobAcceptedSchema>;

/** GET /{month}/apply/status の BE の応答。 */
export const rawApplyStatusSchema = z.object({
  month: z.string(),
  state: z.string(),
  job: rawJobSchema.nullable(),
  running: z.boolean(),
  counts: applyCountsSchema,
  care_record_counts: applyCountsSchema.default({}),
  last_apply: lastApplySchema,
  verify_result: verifySchema,
  rows: z.array(rawRowSchema),
});

export interface MonthlyActualApplyStatus {
  month: string;
  state: string;
  job: MonthlyActualJob | null;
  running: boolean;
  counts: MonthlyActualApplyCounts;
  /** 記録書Ⅱの結果の件数。 */
  record2_counts: MonthlyActualApplyCounts;
  /** 進み具合 (RPA の progress: processed・total・current_name など)。 */
  progress: Record<string, unknown> | null;
  verify_result: z.infer<typeof verifySchema> | null;
  /** 送った行 (結果つき)。 */
  rows: MonthlyActualRow[];
}

export function parseMonthlyActualApplyStatus(raw: unknown): MonthlyActualApplyStatus {
  const r = rawApplyStatusSchema.parse(raw);
  return {
    month: r.month,
    state: r.state,
    job: r.job,
    running: r.running,
    counts: r.counts,
    record2_counts: r.care_record_counts,
    progress: r.job?.progress ?? null,
    verify_result: r.verify_result ?? null,
    rows: r.rows.map(toMonthlyActualRow),
  };
}

// --- 看護師別の月の実績 (`GET /{month}/staff-summary`・`services/monthly_actual/staff_report.py`) ---

const num = z.number().nullable();

/** 1 人 × 1 週 (`StaffWeek.as_dict`)。 */
export const staffWeekSchema = z.object({
  start: isoDate,
  end: isoDate,
  days: z.number(),
  visits: z.number(),
  patients: z.number(),
  provided_min: z.number(),
  km: z.number(),
});

/** 1 人の月の実績 (`StaffMonth.as_dict`)。 */
export const staffMonthSchema = z.object({
  staff_id: z.string(),
  name: z.string(),
  office_short: z.string().nullable(),
  visits: z.number(),
  days: z.number(),
  per_day: num,
  patients: z.number(),
  weekly_patients_avg: num,
  accompany_visits: z.number(),
  provided_min_total: z.number(),
  provided_samples: z.number(),
  avg_stay_min: num,
  short_stay_count: z.number(),
  no_departure_count: z.number(),
  no_arrival_count: z.number(),
  km_total: z.number(),
  km_per_day: num,
  km_per_visit: num,
  skipped_legs: z.number(),
  weeks: z.array(staffWeekSchema),
});
export type MonthlyStaffRow = z.infer<typeof staffMonthSchema>;

export const staffSummarySchema = z.object({
  month: z.string(),
  date_from: isoDate,
  date_to: isoDate,
  /** 月の途中なら今日 (JST)。到着の記録なし・要確認はこの日までを数える。月が終わっていれば null。 */
  counted_until: isoDate.nullable().optional(),
  short_stay_minutes: z.number(),
  short_stay_end_minutes: z.number(),
  weeks: z.array(z.object({ start: isoDate, end: isoDate })),
  totals: z.object({
    staff_count: z.number(),
    visits: z.number(),
    accompany_visits: z.number(),
    provided_min_total: z.number(),
    short_stay_count: z.number(),
    no_departure_count: z.number(),
    no_arrival_count: z.number(),
    km_total: z.number(),
    skipped_legs: z.number(),
  }),
  staff: z.array(staffMonthSchema),
});
export type MonthlyStaffSummary = z.infer<typeof staffSummarySchema>;
