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

/**
 * 提案種別 (suggestion_dismissals.kind と同一値域).
 * 'swap' = P3-② (2 患者の入れ替え). dismiss も同一値域を受ける (BE migration 0049).
 */
export const IMPROVEMENT_KINDS = ['time_change', 'day_change', 'swap'] as const;
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

/**
 * スワップ提案 (kind='swap') の相手患者 Y の情報 (P3-②).
 *
 * 主提案 (ImprovementSuggestion) の current/candidate は対象患者 X の視点.
 * Y は X の現在枠に自分の duration で移り、X は Y の枠 (candidate) に移る.
 * BE `SwapCounterpart` と 1:1. 後方互換のため optional/nullable.
 */
export const swapCounterpartSchema = z.object({
  patient_id: z.string().uuid(),
  patient_name: z.string(),
  current_weekday: z.number().int().min(0).max(6),
  current_start_time: z.string(),
  new_weekday: z.number().int().min(0).max(6),
  new_start_time: z.string(),
  requires_patient_confirmation: z.boolean().default(false),
  // #P4-B: 相手患者 Y の移動先が Y の希望範囲内かどうか (省略時 false = 従来の要確認ロジック).
  within_preference: z.boolean().default(false),
});
export type SwapCounterpart = z.infer<typeof swapCounterpartSchema>;

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
  // #P4-B: 提案先が対象患者の希望範囲内かどうか (省略時 false). true で「ご希望の範囲内」バッジを出す.
  within_preference: z.boolean().default(false),
  // kind='swap' のときのみ相手患者 Y の情報を持つ (後方互換: 既定 null).
  swap_counterpart: swapCounterpartSchema.nullable().default(null),
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

/**
 * 将来互換の寛容化 (再発防止): BE が未知の kind を返しても suggestions セクションを
 * 全滅させない. 配列要素を preprocess で 1 件ずつ safeParse し、パース不能な要素
 * (= 未知 kind 等) を **静かに除外** してから既知スキーマ配列でパースする.
 * これにより既知 3 種 (time_change / day_change / swap) の挙動は完全不変のまま、
 * 新 kind 追加時の「セクション全滅」構造を恒久解消する.
 */
export const tolerantSuggestionsArraySchema = z.preprocess((val) => {
  if (!Array.isArray(val)) return val;
  return val.filter((el) => improvementSuggestionSchema.safeParse(el).success);
}, z.array(improvementSuggestionSchema));

/** GET /v2/improvement-suggestions レスポンス. */
export const improvementSuggestionsResponseSchema = z.object({
  patient_id: z.string().uuid(),
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  suggestions: tolerantSuggestionsArraySchema.default([]),
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

// ---------------------------------------------------------------------------
// POST apply-swap (P3-②: 2 患者の入れ替えを 1 TX で適用)
// ---------------------------------------------------------------------------

/** スワップ後の 1 患者の新枠 (移動先). BE `SwapNewSlot` と 1:1. start_time は HH:MM. */
export const swapNewSlotSchema = z.object({
  weekday: z.number().int().min(0).max(6),
  start_time: z.string(),
  course_template_id: z.string().uuid().nullable().optional(),
});
export type SwapNewSlot = z.input<typeof swapNewSlotSchema>;

/**
 * POST /v2/improvement-suggestions/apply-swap リクエスト. BE `ApplySwapRequest` と 1:1.
 * A は B の旧枠へ、B は A の旧枠へ移る (a_new = B の旧位置, b_new = A の旧位置).
 */
export const applySwapRequestSchema = z.object({
  patient_a_id: z.string().uuid(),
  patient_b_id: z.string().uuid(),
  a_new: swapNewSlotSchema,
  b_new: swapNewSlotSchema,
  iso_year: z.number().int(),
  iso_week: z.number().int(),
});
export type ApplySwapRequest = z.input<typeof applySwapRequestSchema>;

/** POST /v2/improvement-suggestions/apply-swap レスポンス (all-or-nothing). */
export const applySwapResponseSchema = z.object({
  applied: z.boolean(),
  // 寛容パース (warnings 系): 未知コードが混ざっても適用完了フローを止めない.
  warnings: z.array(z.string()).catch([]),
});
export type ApplySwapResponse = z.infer<typeof applySwapResponseSchema>;
