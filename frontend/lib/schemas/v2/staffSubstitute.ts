/**
 * P3-① 当日欠勤の代替スタッフ提案 — zod schemas.
 *
 * Backend `backend/app/schemas/v2/staff_substitute.py` の Pydantic schema と
 * **1:1** でミラーする。設計書 `docs/plans/p3-1-staff-substitute-design.md` §1。
 *
 *   GET  /api/v1/schedule/staff-substitute/candidates
 *   POST /api/v1/schedule/staff-substitute/apply
 *
 * 時刻は HH:MM (秒なし) 文字列。target_date は YYYY-MM-DD (backend `date`)。
 */
import { z } from 'zod';

/** YYYY-MM-DD (backend の `date` に対応)。 */
export const isoDateSchema = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, '日付は YYYY-MM-DD 形式で入力してください');

// ---------------------------------------------------------------------------
// GET candidates レスポンス
// ---------------------------------------------------------------------------

/** 候補スコアの内訳 (設計書 §3)。 */
export const substituteScoreBreakdownSchema = z.object({
  continuity_bonus: z.number(),
  travel_penalty: z.number(),
  load_penalty: z.number(),
});
export type SubstituteScoreBreakdown = z.infer<typeof substituteScoreBreakdownSchema>;

/** 1 visit に対する 1 候補スタッフ (score 降順)。 */
export const substituteCandidateSchema = z.object({
  staff_id: z.string().uuid(),
  staff_name: z.string(),
  staff_sex: z.string().nullish(),
  score: z.number(),
  score_breakdown: substituteScoreBreakdownSchema,
});
export type SubstituteCandidate = z.infer<typeof substituteCandidateSchema>;

/** 候補ゼロ理由コードと件数 (N-6)。除外ハード制約の集計。 */
export const substituteNoCandidateReasonSchema = z.object({
  code: z.string(),
  count: z.number().int().nonnegative(),
});
export type SubstituteNoCandidateReason = z.infer<typeof substituteNoCandidateReasonSchema>;

/** 欠勤スタッフの当日 planned visit 1 件 + その候補ランキング。 */
export const substituteVisitCandidatesSchema = z.object({
  visit_id: z.string().uuid(),
  patient_id: z.string().uuid(),
  patient_name: z.string(),
  start_time: z.string(),
  end_time: z.string(),
  sex_restriction: z.string().nullish(),
  visit_group_id: z.string().uuid().nullish(),
  required_staff_count: z.number().int().min(1).max(2),
  is_secondary: z.boolean().default(false),
  candidates: z.array(substituteCandidateSchema).default([]),
  no_candidate_reasons: z.array(substituteNoCandidateReasonSchema).default([]),
});
export type SubstituteVisitCandidates = z.infer<typeof substituteVisitCandidatesSchema>;

/** GET candidates レスポンス。 */
export const substituteCandidatesResponseSchema = z.object({
  absent_staff_id: z.string().uuid(),
  absent_staff_name: z.string(),
  target_date: isoDateSchema,
  visits: z.array(substituteVisitCandidatesSchema).default([]),
});
export type SubstituteCandidatesResponse = z.infer<typeof substituteCandidatesResponseSchema>;

// ---------------------------------------------------------------------------
// POST apply リクエスト / レスポンス
// ---------------------------------------------------------------------------

/** 1 visit の差替え指示。 */
export const substituteApplyItemSchema = z.object({
  visit_id: z.string().uuid(),
  substitute_staff_id: z.string().uuid(),
});
export type SubstituteApplyItem = z.infer<typeof substituteApplyItemSchema>;

/** POST apply リクエスト (all-or-nothing)。 */
export const substituteApplyRequestSchema = z.object({
  absent_staff_id: z.string().uuid(),
  target_date: isoDateSchema,
  substitutions: z.array(substituteApplyItemSchema).min(1),
  register_absence: z.boolean().default(false),
});
export type SubstituteApplyRequest = z.input<typeof substituteApplyRequestSchema>;

/** 適用済み 1 件 (差替え結果)。 */
export const substituteAppliedSchema = z.object({
  visit_id: z.string().uuid(),
  original_staff_id: z.string().uuid(),
  substitute_staff_id: z.string().uuid(),
  visit_group_partner_visit_id: z.string().uuid().nullish(),
});
export type SubstituteApplied = z.infer<typeof substituteAppliedSchema>;

/** POST apply レスポンス。 */
export const substituteApplyResponseSchema = z.object({
  applied: z.array(substituteAppliedSchema).default([]),
  warnings: z.array(z.string()).default([]),
});
export type SubstituteApplyResponse = z.infer<typeof substituteApplyResponseSchema>;

// ---------------------------------------------------------------------------
// N-6 理由コード → 日本語辞書 (UI 表示用)
// ---------------------------------------------------------------------------

/** 候補ゼロ理由コード (backend `SubstituteNoCandidateReason.code`) の日本語ラベル。 */
export const NO_CANDIDATE_REASON_LABEL_JA: Record<string, string> = {
  no_work_day: '当日出勤なし',
  sex_mismatch: '性別条件',
  office_mismatch: '拠点違い',
  event_overlap: '予定重複 (±15分)',
  time_conflict: '時間衝突',
  trainee_solo: '新人単独不可',
};

export function noCandidateReasonLabel(code: string): string {
  return NO_CANDIDATE_REASON_LABEL_JA[code] ?? code;
}
