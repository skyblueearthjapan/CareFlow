/**
 * 改善提案 (P2-C) zod schemas.
 *
 * Backend `backend/app/schemas/v2/improvement_suggestion.py` の Pydantic v2 schema と
 * **1:1** で一致させる。
 *   GET  /api/v1/schedule/v2/improvement-suggestions?patient_id&iso_year&iso_week
 *   POST /api/v1/schedule/v2/improvement-suggestions/dismiss
 *   RBAC: admin / manager (BE 側で 403 担保).
 *
 * 設計仕様書: `docs/plans/p2-improvement-mvp-design.md` §2.3 / §3.
 *
 * 寛容パース方針:
 *   - 契約は基本 strict (BE と同型). ただし **warnings 系のみ** 寛容にする —
 *     `staff_warnings` は未知コードが増えても採用/見送りフローを止めないよう
 *     `.catch([])` で drift を吸収する (P0-1 の 3 コード再利用だが将来増える前提).
 *   - `feasibility_basis` / `requires_patient_confirmation` は BE default と同値の
 *     `.default()` を付け、旧 BE レスポンスでも壊れないようにする.
 */
import { z } from 'zod';

import { weekdayCodeSchema } from './board';

/** 提案種別 (suggestion_dismissals.kind と同一値域). */
export const IMPROVEMENT_KINDS = ['time_change', 'day_change'] as const;
export const improvementKindSchema = z.enum(IMPROVEMENT_KINDS);
export type ImprovementKind = z.infer<typeof improvementKindSchema>;

/** 却下理由 (suggestion_dismissals.reason と同一値域). */
export const IMPROVEMENT_DISMISS_REASONS = [
  'day_immovable',
  'time_immovable',
  'staff_relation',
  'other',
] as const;
export const improvementDismissReasonSchema = z.enum(IMPROVEMENT_DISMISS_REASONS);
export type ImprovementDismissReason = z.infer<typeof improvementDismissReasonSchema>;

// ---------------------------------------------------------------------------
// GET レスポンス sub-models
// ---------------------------------------------------------------------------

/** 限界コスト方式による効果差分 (現在枠 − 候補枠). 正 = 改善 (削減). */
export const improvementDeltaSchema = z.object({
  travel_minutes_saved: z.number().int(),
  travel_km_saved: z.number(),
});
export type ImprovementDelta = z.infer<typeof improvementDeltaSchema>;

/** 対象患者の現在の固定枠 (PFV ベース). */
export const improvementCurrentSlotSchema = z.object({
  office_id: z.string().uuid(),
  weekday: z.number().int().min(0).max(6),
  weekday_code: weekdayCodeSchema,
  start_time: z.string(),
  end_time: z.string(),
  course_label: z.string(),
  staff_name: z.string().nullable().default(null),
});
export type ImprovementCurrentSlot = z.infer<typeof improvementCurrentSlotSchema>;

/** 提案する移動先の枠. candidate は weekday/start_time/end_time/course_code/office を持つ. */
export const improvementCandidateSlotSchema = z.object({
  office_id: z.string().uuid(),
  office_name: z.string().nullable().default(null),
  weekday: z.number().int().min(0).max(6),
  weekday_code: weekdayCodeSchema,
  start_time: z.string(),
  end_time: z.string(),
  course_code: z.string(),
  course_label: z.string(),
  staff_name: z.string().nullable().default(null),
});
export type ImprovementCandidateSlot = z.infer<typeof improvementCandidateSlotSchema>;

/** 変わるもの / 変わらないものの日本語差分. */
export const improvementChangesSchema = z.object({
  changes: z.array(z.string()).default([]),
  unchanged: z.array(z.string()).default([]),
});
export type ImprovementChanges = z.infer<typeof improvementChangesSchema>;

/** 改善提案 1 件. */
export const improvementSuggestionSchema = z.object({
  kind: improvementKindSchema,
  target_weekday: z.number().int().min(0).max(6),
  current: improvementCurrentSlotSchema,
  candidate: improvementCandidateSlotSchema,
  delta: improvementDeltaSchema,
  changes: improvementChangesSchema,
  // 寛容パース (warnings 系): 未知コードが混ざっても採用/見送りフローを止めない.
  staff_warnings: z.array(z.string()).catch([]),
  feasibility_basis: z.string().default('pfv'),
  requires_patient_confirmation: z.boolean().default(false),
});
export type ImprovementSuggestion = z.infer<typeof improvementSuggestionSchema>;

/** 黙って消さない (N-6): 提案を出さなかった内訳件数. */
export const improvementFilteredSummarySchema = z.object({
  pinned: z.number().int().min(0).default(0),
  locked: z.number().int().min(0).default(0),
  no_current_visit: z.number().int().min(0).default(0),
  dismissed: z.number().int().min(0).default(0),
  below_threshold: z.number().int().min(0).default(0),
  day_restricted: z.number().int().min(0).default(0),
});
export type ImprovementFilteredSummary = z.infer<typeof improvementFilteredSummarySchema>;

/** GET /v2/improvement-suggestions レスポンス. */
export const improvementSuggestionsResponseSchema = z.object({
  patient_id: z.string().uuid(),
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  suggestions: z.array(improvementSuggestionSchema).default([]),
  filtered_summary: improvementFilteredSummarySchema.default({
    pinned: 0,
    locked: 0,
    no_current_visit: 0,
    dismissed: 0,
    below_threshold: 0,
    day_restricted: 0,
  }),
});
export type ImprovementSuggestionsResponse = z.infer<typeof improvementSuggestionsResponseSchema>;

// ---------------------------------------------------------------------------
// POST dismiss
// ---------------------------------------------------------------------------

/** POST /v2/improvement-suggestions/dismiss リクエスト. */
export const improvementDismissRequestSchema = z.object({
  patient_id: z.string().uuid(),
  kind: improvementKindSchema,
  target_weekday: z.number().int().min(0).max(6),
  reason: improvementDismissReasonSchema,
  reason_note: z.string().nullable().optional(),
  promote_movability: z.boolean().default(false),
});
export type ImprovementDismissRequest = z.input<typeof improvementDismissRequestSchema>;

/** POST /v2/improvement-suggestions/dismiss レスポンス. */
export const improvementDismissResponseSchema = z.object({
  dismissal_id: z.string().uuid(),
  movability_updated: z.boolean().default(false),
  new_movability: z.string().nullable().default(null),
});
export type ImprovementDismissResponse = z.infer<typeof improvementDismissResponseSchema>;
