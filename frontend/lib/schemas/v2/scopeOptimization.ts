/**
 * 範囲最適化 (scope-optimization W1) zod schemas.
 *
 * Backend `backend/app/schemas/v2/scope_optimization.py` の Pydantic v2 schema と
 * **1:1** で一致させる。
 *   POST /api/v1/schedule/v2/scope-optimization/simulate (read-only)
 *   RBAC: admin / manager (BE 側で 403 担保).
 *
 * 設計仕様書: `docs/plans/scope-optimization-design.md` §4.
 *
 * 寛容パース方針:
 *   - 手順 1 件の中身 (`suggestion`) は improvementSuggestionSchema を再利用する
 *     (staff_warnings の `.catch([])` 等の寛容化もそのまま効く)。
 *   - steps 配列は **「最長の有効プレフィックス」だけ残す** 寛容化にする。
 *     手順列は前の手に依存する順序付きチェーンであり、途中の 1 件だけ静かに
 *     抜くと累積効果や W2 のプレフィックス適用の意味が壊れるため、未知 kind 等で
 *     パース不能な手が現れたらそこで打ち切る (以降を捨てる)。
 */
import { z } from 'zod';

import { courseSnapshotSchema, improvementSuggestionSchema } from './improvementSuggestion';

// UI 統一: スナップショットの正典は improvementSuggestion.ts (改善提案と共有)。
// 旧名は互換 re-export (ScopeOptimizeDialog 等の既存 import を壊さない)。
export {
  courseSnapshotSchema as scopeCourseSnapshotSchema,
  courseSnapshotVisitSchema as scopeSnapshotVisitSchema,
} from './improvementSuggestion';
export type {
  CourseSnapshot as ScopeCourseSnapshot,
  CourseSnapshotVisit as ScopeSnapshotVisit,
} from './improvementSuggestion';

/** 手順 1 件 (move または swap)。suggestion は改善提案と同一契約. */
export const scopeOptimizationStepSchema = z.object({
  seq: z.number().int().min(1),
  patient_id: z.string().uuid(),
  patient_name: z.string(),
  suggestion: improvementSuggestionSchema,
  cumulative_delta_minutes: z.number().int(),
  cumulative_delta_km: z.number(),
  // W3: 適用前スナップショット (旧 BE 互換で optional/null 既定).
  source_course: courseSnapshotSchema.nullable().default(null),
  destination_course: courseSnapshotSchema.nullable().default(null),
});
export type ScopeOptimizationStep = z.infer<typeof scopeOptimizationStepSchema>;

/**
 * steps の寛容化: 先頭から順に safeParse し、最初にパース不能になった手から後ろを
 * 捨てる (最長有効プレフィックス)。ドロップが起きても配列自体は有効なので
 * セクション全滅はしない。
 */
export const tolerantStepsArraySchema = z.preprocess((val) => {
  if (!Array.isArray(val)) return val;
  const out: unknown[] = [];
  for (const el of val) {
    if (!scopeOptimizationStepSchema.safeParse(el).success) break;
    out.push(el);
  }
  return out;
}, z.array(scopeOptimizationStepSchema));

/** scope 内合計の健康診断メトリクス (schedule_health と同一物差し). */
export const scopeOptimizationMetricsSchema = z.object({
  visit_count: z.number().int().min(0).default(0),
  travel_minutes: z.number().int().min(0).default(0),
  travel_km: z.number().min(0).default(0),
  buffer_minutes: z.number().int().min(0).default(0),
  gap_minutes: z.number().int().min(0).default(0),
});
export type ScopeOptimizationMetrics = z.infer<typeof scopeOptimizationMetricsSchema>;

/** 黙って消さない (N-6): 動かさなかった / 数えなかった内訳. */
export const scopeOptimizationExcludedSummarySchema = z.object({
  pinned: z.number().int().min(0).default(0),
  locked: z.number().int().min(0).default(0),
  no_current_visit: z.number().int().min(0).default(0),
  dismissed: z.number().int().min(0).default(0),
  confirmation_required_excluded: z.number().int().min(0).default(0),
  truncated: z.boolean().default(false),
});
export type ScopeOptimizationExcludedSummary = z.infer<
  typeof scopeOptimizationExcludedSummarySchema
>;

/** POST /v2/scope-optimization/simulate リクエスト (BE `ScopeOptimizationSimulateRequest`). */
export const scopeOptimizationSimulateRequestSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  scope: z.object({
    office_id: z.string().uuid(),
    weekdays: z.array(z.number().int().min(0).max(6)).nullable().default(null),
    course_codes: z.array(z.string()).nullable().default(null),
  }),
});
export type ScopeOptimizationSimulateRequest = z.input<
  typeof scopeOptimizationSimulateRequestSchema
>;

/**
 * POST /v2/scope-optimization/apply リクエスト (W2. BE `ScopeOptimizationApplyRequest`).
 * steps は simulate レスポンスの先頭からの連続区間 (seq=1..N) をそのまま送る。
 */
export const scopeOptimizationApplyRequestSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  scope: z.object({
    office_id: z.string().uuid(),
    weekdays: z.array(z.number().int().min(0).max(6)).nullable().default(null),
    course_codes: z.array(z.string()).nullable().default(null),
  }),
  state_token: z.string().min(1),
  steps: z.array(scopeOptimizationStepSchema).min(1),
});
export type ScopeOptimizationApplyRequest = z.input<typeof scopeOptimizationApplyRequestSchema>;

/** POST /v2/scope-optimization/apply レスポンス (all-or-nothing). */
export const scopeOptimizationApplyResponseSchema = z.object({
  applied_count: z.number().int().min(0),
  // 寛容パース (warnings 系): 未知文言が混ざっても適用完了フローを止めない.
  warnings: z.array(z.string()).catch([]),
});
export type ScopeOptimizationApplyResponse = z.infer<typeof scopeOptimizationApplyResponseSchema>;

/** コース別の実行後見通し (H2: 恒久パターン基準のシミュレーション値). */
export const scopeOptimizationCourseBeforeAfterSchema = z.object({
  office_id: z.string().uuid(),
  weekday: z.number().int().min(0).max(6),
  course_code: z.string(),
  course_label: z.string(),
  staff_name: z.string().nullable().default(null),
  before: scopeOptimizationMetricsSchema,
  after: scopeOptimizationMetricsSchema,
});
export type ScopeOptimizationCourseBeforeAfter = z.infer<
  typeof scopeOptimizationCourseBeforeAfterSchema
>;

/** POST /v2/scope-optimization/simulate レスポンス. */
export const scopeOptimizationSimulateResponseSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  office_id: z.string().uuid(),
  steps: tolerantStepsArraySchema.default([]),
  before: scopeOptimizationMetricsSchema,
  after: scopeOptimizationMetricsSchema,
  excluded_summary: scopeOptimizationExcludedSummarySchema.default({
    pinned: 0,
    locked: 0,
    no_current_visit: 0,
    dismissed: 0,
    confirmation_required_excluded: 0,
    truncated: false,
  }),
  state_token: z.string(),
  // H2: コース別の実行後見通し (旧 BE 互換で空既定).
  courses: z.array(scopeOptimizationCourseBeforeAfterSchema).default([]),
});
export type ScopeOptimizationSimulateResponse = z.infer<
  typeof scopeOptimizationSimulateResponseSchema
>;
