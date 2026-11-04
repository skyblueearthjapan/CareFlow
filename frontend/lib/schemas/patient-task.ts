/**
 * 事務所からのやること (看護師へのタスク) — mirrors `backend/app/schemas/patient_task.py`.
 *
 * 正典 = docs/plans/patient-tasks-design-2026-11-01.md (PO 決定は §2)。
 * やることは「患者 + 日付 (この日から出す)」で持ち、出す訪問は BE が見るたびに決める
 * (その患者の、日付以降の、取消・削除でない最初の訪問)。
 */
import { z } from 'zod';

export const taskItemTypeReadSchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  sort_order: z.number().int(),
  is_active: z.boolean(),
  /**
   * 最初に訪問した看護師が預かる (patient-tasks-hold-first-visitor-design-2026-11-04 §3)。
   * 最初の訪問で済まなかったら、その訪問の主担当が預かり、ほかの訪問へは移らない。
   */
  hold_by_first_visitor: z.boolean(),
});
export type TaskItemTypeRead = z.infer<typeof taskItemTypeReadSchema>;

export const taskItemTypeCreateSchema = z.object({
  name: z.string().trim().min(1).max(100),
  /** 省略すると最後に並ぶ。 */
  sort_order: z.number().int().optional(),
  hold_by_first_visitor: z.boolean().optional(),
});
export type TaskItemTypeCreate = z.infer<typeof taskItemTypeCreateSchema>;

export const taskItemTypeUpdateSchema = z.object({
  name: z.string().trim().min(1).max(100).optional(),
  sort_order: z.number().int().optional(),
  is_active: z.boolean().optional(),
  hold_by_first_visitor: z.boolean().optional(),
});
export type TaskItemTypeUpdate = z.infer<typeof taskItemTypeUpdateSchema>;

export const taskStaffRefSchema = z.object({
  staff_id: z.string().uuid(),
  name: z.string().nullable(),
  /** primary = 主担当 (コース担当を含む) / secondary = 2 名訪問の 2 人目 / accompaniment = 同行 */
  role: z.enum(['primary', 'secondary', 'accompaniment']),
});
export type TaskStaffRef = z.infer<typeof taskStaffRefSchema>;

export const taskVisitBriefSchema = z.object({
  visit_id: z.string().uuid(),
  visit_date: z.string(),
  /** HH:MM */
  start_time: z.string(),
  end_time: z.string(),
  staff: z.array(taskStaffRefSchema),
});
export type TaskVisitBrief = z.infer<typeof taskVisitBriefSchema>;

export const taskStatusSchema = z.enum(['open', 'done', 'cancelled']);
export type TaskStatus = z.infer<typeof taskStatusSchema>;

/**
 * 一覧の絞り込み: open = 未完了 (預かり中を含む) / overdue = 期日を過ぎた / waiting2plus = 未完了で
 * 2 回目以降の訪問待ち (預かり中を除く) / held = 預かり中 / done = 完了 / all = すべて (取消は除く)。
 */
export type TaskListStatus = 'open' | 'overdue' | 'waiting2plus' | 'held' | 'done' | 'all';

/**
 * 預かっている看護師と、預かった訪問 (最初の訪問)。預かり中の未完了だけ入る
 * (patient-tasks-hold-first-visitor-design-2026-11-04 §3)。
 */
export const taskHeldBySchema = z.object({
  staff_id: z.string().uuid(),
  staff_name: z.string().nullable(),
  /** false = 在籍していない (辞めた・削除)。事務所が完了か取り消しをする。 */
  staff_active: z.boolean(),
  visit_id: z.string().uuid(),
  /** YYYY-MM-DD */
  visit_date: z.string(),
  /** HH:MM */
  start_time: z.string(),
});
export type TaskHeldBy = z.infer<typeof taskHeldBySchema>;

export const patientTaskReadSchema = z.object({
  id: z.string().uuid(),
  patient_id: z.string().uuid(),
  patient_name: z.string().nullable(),
  /** この日から出す (YYYY-MM-DD)。 */
  start_date: z.string(),
  item_type_id: z.string().uuid().nullable(),
  item_type_name: z.string().nullable(),
  /** 項目なし = 伝言。 */
  is_message: z.boolean(),
  memo: z.string().nullable(),
  created_by: z.string().uuid().nullable(),
  created_by_name: z.string().nullable(),
  created_at: z.string(),
  status: taskStatusSchema,
  /** 期日 (任意・YYYY-MM-DD)。null = 期日なし (patient-tasks-due-design-2026-11-04 §2)。 */
  due_date: z.string().nullable(),
  /** 未完了で、期日が今日 (JST) より前。期日がないものは false。 */
  overdue: z.boolean(),
  /**
   * 何回目の訪問待ち (1 以上)。一覧・作成は「いまの時刻から見て」、
   * GET /visits/{id}/tasks は「その訪問から見て」の回数。
   */
  visit_round: z.number().int().min(1),
  /** 訪問ごとの読み出しだけ: この訪問より前に出す訪問があった (2 回目以降と同じ意味)。 */
  carried_over: z.boolean(),
  done_at: z.string().nullable(),
  done_by_user_id: z.string().uuid().nullable(),
  done_by_staff_id: z.string().uuid().nullable(),
  done_by_name: z.string().nullable(),
  done_visit_id: z.string().uuid().nullable(),
  cancelled_at: z.string().nullable(),
  /** 毎月の設定から作ったときだけ: どの設定から。手で付けたものは null。 */
  recurring_rule_id: z.string().uuid().nullish(),
  /** 毎月の設定から作ったときだけ: 何月分か (その月の 1 日・YYYY-MM-DD)。 */
  recurring_month: z.string().nullish(),
  /** 預かり中の未完了だけ: 預かっている看護師と最初の訪問。それ以外は null。 */
  held_by: taskHeldBySchema.nullish(),
  /** 一覧・作成だけ: 未完了なら出す訪問 (今日以降・日付以降の最初の訪問)。無ければ・預かり中は null。 */
  next_visit: taskVisitBriefSchema.nullable(),
  /** 一覧だけ: 完了した訪問。 */
  done_visit: taskVisitBriefSchema.nullable(),
  /** 呼んだ人がこの完了を戻せるか (職員は自分が押した完了だけ)。 */
  can_reopen: z.boolean(),
});
export type PatientTaskRead = z.infer<typeof patientTaskReadSchema>;

export const patientTaskCreateSchema = z
  .object({
    patient_id: z.string().uuid(),
    /** YYYY-MM-DD */
    start_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
    /** 複数なら 1 件ずつ作る。空なら伝言だけの 1 件。 */
    item_type_ids: z.array(z.string().uuid()).max(50).default([]),
    /** 伝言 (任意)。項目と一緒なら最初の 1 件に付く。 */
    memo: z.string().max(2000).nullable().optional(),
    /** 期日 (任意・YYYY-MM-DD)。日付より前は BE も 422。 */
    due_date: z
      .string()
      .regex(/^\d{4}-\d{2}-\d{2}$/)
      .nullable()
      .optional(),
  })
  .refine((v) => v.item_type_ids.length > 0 || (v.memo ?? '').trim().length > 0, {
    message: '項目か伝言のどちらかを入れてください',
    path: ['memo'],
  })
  .refine((v) => !v.due_date || v.due_date >= v.start_date, {
    message: '期日は日付と同じ日か、後の日にしてください',
    path: ['due_date'],
  });
export type PatientTaskCreate = z.input<typeof patientTaskCreateSchema>;

export const patientTaskCreateResultSchema = z.object({
  tasks: z.array(patientTaskReadSchema),
  next_visit: taskVisitBriefSchema.nullable(),
  notified_user_count: z.number().int().nonnegative(),
});
export type PatientTaskCreateResult = z.infer<typeof patientTaskCreateResultSchema>;

export const patientTaskDueUpdateSchema = z.object({
  /** YYYY-MM-DD。null = 期日なしに戻す。 */
  due_date: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .nullable(),
});
export type PatientTaskDueUpdate = z.infer<typeof patientTaskDueUpdateSchema>;

export const visitTaskCountSchema = z.object({
  open_count: z.number().int().nonnegative(),
  total_count: z.number().int().nonnegative(),
  /** 未完了のうち、2 回目以降の訪問待ちがある。 */
  has_carried_over: z.boolean(),
  /** 未完了のうち、期日を過ぎたものがある。 */
  has_overdue: z.boolean(),
  all_done: z.boolean(),
});
export type VisitTaskCount = z.infer<typeof visitTaskCountSchema>;

export const visitTaskCountsSchema = z.object({
  date: z.string(),
  /** 訪問 id → 件数。やることが出ない訪問は含まれない。 */
  counts: z.record(visitTaskCountSchema),
});
export type VisitTaskCounts = z.infer<typeof visitTaskCountsSchema>;
