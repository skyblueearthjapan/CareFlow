/**
 * Auto-allocate (Wave 41 v1.0) zod schemas.
 *
 * Backend `backend/app/schemas/v2/auto_allocate.py` の Pydantic schema と
 * **完全一致** させる。エンドポイント:
 *   - POST /api/v1/schedule/auto-allocate
 *   - POST /api/v1/schedule/proposal/{batch_id}/apply
 *   - POST /api/v1/schedule/proposal/{batch_id}/discard
 *
 * 設計仕様書: ``docs/plans/auto-schedule-v1.md`` v0.4 §10 データ書込仕様
 * 実装計画書: ``docs/plans/auto-schedule-implementation-plan.md`` v0.2 §4 Phase 2
 */
import { z } from 'zod';

// ---------------------------------------------------------------------------
// 算出モード — BE Literal['mode_1', 'mode_2'] と一致。
//   - mode_1: v1.0 で実装される実体ロジック
//   - mode_2: v1.0 では NotImplementedError → 501 (UI 上は選択肢として残す)
// ---------------------------------------------------------------------------
export const autoAllocateModeEnum = z.enum(['mode_1', 'mode_2']);
export type AutoAllocateMode = z.infer<typeof autoAllocateModeEnum>;

// ---------------------------------------------------------------------------
// Request — POST /api/v1/schedule/auto-allocate
// ---------------------------------------------------------------------------
export const autoAllocateRequestSchema = z.object({
  iso_year: z.number().int().min(2020).max(2100),
  iso_week: z.number().int().min(1).max(53),
  /** 空配列 = 全拠点。指定があれば対象拠点を絞る。 */
  office_ids: z.array(z.string().uuid()).default([]),
  mode: autoAllocateModeEnum.default('mode_1'),
});
export type AutoAllocateRequest = z.infer<typeof autoAllocateRequestSchema>;

// ---------------------------------------------------------------------------
// KPI — auto_allocate 戻り値の KPI dict と 1:1 対応。
// ---------------------------------------------------------------------------
export const kpiResponseSchema = z.object({
  total_distance_km_before: z.number(),
  total_distance_km_after: z.number(),
  distance_reduction_pct: z.number(),
  staff_load_stddev_before: z.number(),
  staff_load_stddev_after: z.number(),
  /** ハード制約 H1〜H8 の違反件数 (キーは "H1"…"H8")。 */
  h_violations: z.record(z.string(), z.number().int()),
  improvement_score: z.number(),
});
export type KpiResponse = z.infer<typeof kpiResponseSchema>;

// ---------------------------------------------------------------------------
// ProposedCourseSummary — 1 提案コースのサマリ。
// ---------------------------------------------------------------------------
export const proposedCourseSummarySchema = z.object({
  course_id: z.string().uuid(),
  office_id: z.string().uuid(),
  /** 曜日 (0=月..6=日) */
  weekday: z.number().int().min(0).max(6),
  code: z.string(),
  assigned_staff_id: z.string().uuid().nullable().optional(),
  /** UI 表示用の staff 名 (BE 側で join して埋める。未解決なら null)。 */
  assigned_staff_name: z.string().nullable().optional(),
  visits_count: z.number().int().nonnegative(),
});
export type ProposedCourseSummary = z.infer<typeof proposedCourseSummarySchema>;

// ---------------------------------------------------------------------------
// Response — POST /api/v1/schedule/auto-allocate
// ---------------------------------------------------------------------------
export const autoAllocateResponseSchema = z.object({
  proposal_batch_id: z.string().uuid(),
  proposals: z.array(proposedCourseSummarySchema).default([]),
  kpi: kpiResponseSchema,
  warnings: z.array(z.string()).default([]),
});
export type AutoAllocateResponse = z.infer<typeof autoAllocateResponseSchema>;

// ---------------------------------------------------------------------------
// Response — POST /api/v1/schedule/proposal/{batch_id}/apply
// ---------------------------------------------------------------------------
export const proposalApplyResponseSchema = z.object({
  proposal_batch_id: z.string().uuid(),
  courses_committed: z.number().int().nonnegative(),
  visits_committed: z.number().int().nonnegative(),
  staff_assignments_committed: z.number().int().nonnegative(),
  course_ids: z.array(z.string().uuid()).default([]),
});
export type ProposalApplyResponse = z.infer<typeof proposalApplyResponseSchema>;

// ---------------------------------------------------------------------------
// Response — POST /api/v1/schedule/proposal/{batch_id}/discard
// ---------------------------------------------------------------------------
export const proposalDiscardResponseSchema = z.object({
  proposal_batch_id: z.string().uuid(),
  courses_deleted: z.number().int().nonnegative(),
  visits_deleted: z.number().int().nonnegative(),
});
export type ProposalDiscardResponse = z.infer<typeof proposalDiscardResponseSchema>;
