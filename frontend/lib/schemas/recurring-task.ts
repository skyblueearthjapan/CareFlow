/**
 * 毎月のやること (繰り返しの設定) — mirrors `backend/app/schemas/recurring_task.py`.
 *
 * 正典 = docs/plans/patient-tasks-recurring-design-2026-11-02.md。
 * 毎月その日に、対象の方へやることを 1 件ずつ作る。作ったやることの出し方・持ち越し・
 * 完了は、手で付けたものと同じ。
 */
import { z } from 'zod';

import { taskVisitBriefSchema } from '@/lib/schemas/patient-task';

export const visitCategorySchema = z.enum(['psychiatric', 'general']);
export type VisitCategory = z.infer<typeof visitCategorySchema>;

export const VISIT_CATEGORY_LABELS: Record<VisitCategory, string> = {
  psychiatric: '精神科',
  general: '通常',
};

export const PATIENT_STATUS_LABELS: Record<string, string> = {
  active: '稼働中',
  suspended: '一時休止',
  admitted: '入院中',
  pending: '開始前',
  cancelled: '終了',
};

export const recurringTaskRuleReadSchema = z.object({
  id: z.string().uuid(),
  item_type_id: z.string().uuid().nullable(),
  item_type_name: z.string().nullable(),
  /** 項目を「使わない」にした設定は作らない。伝言だけの設定は true。 */
  item_type_active: z.boolean(),
  memo: z.string().nullable(),
  day_of_month: z.number().int(),
  /** 毎月何日まで (期日)。null = なし・0 = 末日・1〜28 = その日。 */
  due_day: z.number().int().nullable(),
  target_categories: z.array(z.string()),
  /** 最初に作る月 (その月の 1 日)。 */
  start_month: z.string(),
  is_active: z.boolean(),
  excluded_patients: z.array(
    z.object({ patient_id: z.string().uuid(), name: z.string().nullable() }),
  ),
  /** いま稼働中で対象の方の人数。 */
  target_count: z.number().int(),
  /** この設定から作った未完了の件数。 */
  open_count: z.number().int(),
  created_at: z.string(),
  /** 作る・変えるの応答だけ: 保存したときに今月の分を作った件数。 */
  created_now: z.number().int().optional(),
});
export type RecurringTaskRuleRead = z.infer<typeof recurringTaskRuleReadSchema>;

export const recurringTaskRuleInputSchema = z
  .object({
    item_type_id: z.string().uuid().nullable(),
    memo: z.string().max(2000).nullable(),
    day_of_month: z.number().int().min(1).max(28),
    /** 毎月何日まで。null = なし・0 = 末日・1〜28 (「から」より前は BE も 422)。 */
    due_day: z.number().int().min(0).max(28).nullable(),
    target_categories: z.array(visitCategorySchema).min(1, '訪問の区分を 1 つ以上選んでください'),
    /** YYYY-MM-01 */
    start_month: z.string().regex(/^\d{4}-\d{2}-01$/),
    is_active: z.boolean(),
    excluded_patient_ids: z.array(z.string().uuid()),
  })
  .refine((v) => v.item_type_id !== null || (v.memo ?? '').trim().length > 0, {
    message: '項目か伝言のどちらかを入れてください',
    path: ['memo'],
  })
  .refine((v) => v.due_day === null || v.due_day === 0 || v.due_day >= v.day_of_month, {
    message: '「まで」は「から」と同じ日か、後の日にしてください',
    path: ['due_day'],
  });
export type RecurringTaskRuleInput = z.infer<typeof recurringTaskRuleInputSchema>;

export const recurringCandidateSchema = z.object({
  patient_id: z.string().uuid(),
  name: z.string(),
  kana: z.string().nullable(),
  visit_category: z.string(),
  status: z.string(),
});
export type RecurringCandidate = z.infer<typeof recurringCandidateSchema>;

export const recurringPreviewResultSchema = z.enum([
  'create',
  'exists',
  'manual',
  'excluded',
  'not_active',
]);
export type RecurringPreviewResult = z.infer<typeof recurringPreviewResultSchema>;

export const recurringPreviewSchema = z.object({
  rule_id: z.string().uuid(),
  month: z.string(),
  start_date: z.string(),
  is_active: z.boolean(),
  item_is_active: z.boolean(),
  counts: z.record(z.number().int()),
  prev_open_count: z.number().int(),
  rows: z.array(
    z.object({
      patient_id: z.string().uuid(),
      patient_name: z.string().nullable(),
      status: z.string(),
      visit_category: z.string(),
      result: recurringPreviewResultSchema,
      visit: taskVisitBriefSchema.nullable(),
      /** この設定の前の月の分が未完了で残っている。 */
      prev_open: z.boolean(),
    }),
  ),
});
export type RecurringPreview = z.infer<typeof recurringPreviewSchema>;
