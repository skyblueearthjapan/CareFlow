/**
 * Auto-schedule v2.0 (Wave 41 v2) zod schemas.
 *
 * Backend ``backend/app/schemas/v2/auto_schedule_v2.py`` の Pydantic schema と
 * **完全一致** させる。エンドポイント:
 *   - POST /api/v1/schedule/v2/diff-add          (差分追加: プール患者の提案を生成)
 *   - POST /api/v1/schedule/v2/full-optimize     (全面最適化: 全 active 患者で再構築)
 *   - POST /api/v1/schedule/v2/apply-individual  (1 患者ずつ採用 → patient_fixed_visits 更新)
 *   - POST /api/v1/schedule/v2/reset-to-fixed    (固定枠に戻す: 1 週間分を patient_fixed_visits から再生成)
 *
 * 設計仕様書: ``docs/plans/auto-schedule-v2.md`` v0.2 §3-§6, §13.5
 *
 * v2 設計思想 (v1 との違い):
 *   - 一括採用 ボタンは設けない (Q2: 患者ごとに 1 件ずつ採用)
 *   - patient_fixed_visits 中心 (visits ではなく固定枠を更新する)
 *   - Before/After は週単位 (曜日タブ切替) + 患者ごと (ポップアップ)
 */
import { z } from 'zod';

// ---------------------------------------------------------------------------
// 共通プリミティブ
// ---------------------------------------------------------------------------

/** Pydantic ``time`` のシリアライズ形式 ("HH:MM" or "HH:MM:SS"). */
const timeStringSchema = z.string().regex(/^\d{2}:\d{2}(:\d{2})?$/, '時刻は HH:MM 形式');

const weekdaySchema = z.number().int().min(0).max(6); // 0=月..6=日

const amPmSchema = z.enum(['am', 'pm', 'any']);
export type AmPmV2 = z.infer<typeof amPmSchema>;

// ---------------------------------------------------------------------------
// W41 v2 拡張 (構造化警告): UI で曜日タブ振り分け + 「⏰ 固定時間を変更」アクション.
// Backend ``V2WarningOut`` と 1:1 対応.
// ---------------------------------------------------------------------------

export const v2WarningTypeSchema = z.enum([
  'same_address_consolidation',
  'course_capacity',
  'course_long_distance',
  'course_count',
  'acceptance_blocked',
  // W41 v2 拡張 (警告 type の分離): UI 分類のため細分化.
  // - travel_time_shortage : 移動時間で希望時刻に間に合わない or 昼休憩バンプ.
  // - two_staff_shortage   : 二人組訪問必須だがスタッフ < 2 名.
  'travel_time_shortage',
  'two_staff_shortage',
  'general',
]);
export type V2WarningType = z.infer<typeof v2WarningTypeSchema>;

export const v2WarningSchema = z.object({
  type: v2WarningTypeSchema,
  message: z.string(),
  weekday: z.number().int().min(0).max(6).nullable().default(null),
  actionable: z.boolean().default(false),
  patient_id: z.string().uuid().nullable().default(null),
  patient_name: z.string().nullable().default(null),
  visit_id: z.string().uuid().nullable().default(null),
  current_time: z.string().nullable().default(null),
  suggested_time: z.string().nullable().default(null),
  time_type: z.string().nullable().default(null),
  preferred_start: z.string().nullable().default(null),
  preferred_end: z.string().nullable().default(null),
});
export type V2Warning = z.infer<typeof v2WarningSchema>;

// ---------------------------------------------------------------------------
// 提案 visit (Backend V2VisitPlan と一致)
// ---------------------------------------------------------------------------

export const v2VisitPlanSchema = z.object({
  weekday: weekdaySchema,
  start_time: timeStringSchema,
  end_time: timeStringSchema,
  duration_min: z.number().int().min(1).max(480),
  course_code: z.string().min(1).max(2),
  office_id: z.string().uuid(),
  am_pm: amPmSchema,
  assigned_staff_id: z.string().uuid().nullable(),
});
export type V2VisitPlan = z.infer<typeof v2VisitPlanSchema>;

// ---------------------------------------------------------------------------
// UI 表示用 visit サマリ (Backend V2VisitForUI と一致)
// ---------------------------------------------------------------------------

export const v2VisitForUiSchema = z.object({
  patient_id: z.string().uuid(),
  patient_name: z.string(),
  patient_code: z.string().nullable().optional(),
  start_time: timeStringSchema,
  end_time: timeStringSchema,
  duration_min: z.number().int().min(1).max(480),
  am_pm: amPmSchema,
  // W41 v2 (Mode 2 UI 拡張): 住所文字列 + 町レベルのエリアラベル.
  // Before/After カードでエリア偏在を可視化する.
  address: z.string().nullable().default(null),
  area_label: z.string().nullable().default(null),
  // W41 v2 (Mode 2 Before/After 表示拡張): time_type と sex_restriction バッジ.
  time_type: z.string().nullable().default(null),
  sex_restriction: z.string().nullable().default(null),
  // W41 v2 (H2 視覚化): 同住所グループ id (2 名以上のとき同 id, 単独は null).
  same_address_group_id: z.string().nullable().default(null),
  // W41 v2 (UI 時間詳細表示): 希望時間帯 (HH:MM 文字列).
  // time_type='時間帯' のとき範囲 (start-end), '固定' のとき開始時刻のみ保持.
  preferred_start: z.string().nullable().default(null),
  preferred_end: z.string().nullable().default(null),
  // W41 v2 拡張 (訪問間距離): 同コース内で次の patient までの直線距離 (km).
  // コース内 start_time 昇順で隣接ペアの Haversine 距離. 最後の visit は null.
  distance_to_next_km: z.number().nullable().default(null),
  // W41 v2 拡張 (週限定変更): 提案中 visit の ID. apply-week-only 後に DB に
  // 入った visit を「今週限定」変更する際の識別子. 提案算出直後は null.
  visit_id: z.string().uuid().nullable().default(null),
});
export type V2VisitForUI = z.infer<typeof v2VisitForUiSchema>;

// ---------------------------------------------------------------------------
// 1 コース分の Before/After サマリ (Backend V2CourseSummary)
//
// Note: weekday は V2WeekdayBeforeAfter が保持するので重複を避けて削除.
// ---------------------------------------------------------------------------

export const v2CourseSummarySchema = z.object({
  code: z.string(),
  office_id: z.string().uuid(),
  // W41 v2 (Mode 2 Before/After 表示拡張): UI ヘッダーで「拠点名 + コース名」.
  office_name: z.string().nullable().default(null),
  assigned_staff_id: z.string().uuid().nullable(),
  /** visits は型付き V2VisitForUI の配列 (Backend 改修に追従). */
  visits: z.array(v2VisitForUiSchema).default([]),
  distance_km: z.number().nonnegative().default(0),
  visits_count: z.number().int().nonnegative().default(0),
});
export type V2CourseSummary = z.infer<typeof v2CourseSummarySchema>;

// ---------------------------------------------------------------------------
// 1 曜日の Before/After (機能 B; week_proposals 配下)
// ---------------------------------------------------------------------------

export const v2WeekdayBeforeAfterSchema = z.object({
  weekday: weekdaySchema,
  /** ``{ courses: V2CourseSummary[] }`` を期待 (Backend は dict で渡してくる). */
  before: z.object({ courses: z.array(v2CourseSummarySchema).default([]) }).passthrough(),
  after: z.object({ courses: z.array(v2CourseSummarySchema).default([]) }).passthrough(),
});
export type V2WeekdayBeforeAfter = z.infer<typeof v2WeekdayBeforeAfterSchema>;

// ---------------------------------------------------------------------------
// KPI 全体 (Backend V2KpiOverall)
// ---------------------------------------------------------------------------

export const v2KpiOverallSchema = z.object({
  total_distance_km_before: z.number().nonnegative().default(0),
  total_distance_km_after: z.number().nonnegative().default(0),
  distance_reduction_pct: z.number().default(0),
  courses_count_before: z.number().int().nonnegative().default(0),
  courses_count_after: z.number().int().nonnegative().default(0),
  capacity_overflows: z.number().int().nonnegative().default(0),
  h_violations: z.record(z.string(), z.number().int()).default({}),
});
export type V2KpiOverall = z.infer<typeof v2KpiOverallSchema>;

// ---------------------------------------------------------------------------
// 差分サマリ (Before/After 1 件あたり) — Backend V2ProposalDelta
// ---------------------------------------------------------------------------

export const v2ProposalDeltaSchema = z.object({
  distance_km: z.number().default(0),
  capacity: z.string().nullable().default(null),
  course_visits_count_before: z.number().int().default(0),
  course_visits_count_after: z.number().int().default(0),
});
export type V2ProposalDelta = z.infer<typeof v2ProposalDeltaSchema>;

// ---------------------------------------------------------------------------
// 1 患者の採用前後サマリ (機能 A) — Backend V2BeforeAfterSummary
// ---------------------------------------------------------------------------

export const v2BeforeAfterSummarySchema = z.object({
  course_visits_count: z.number().int().nonnegative().default(0),
  distance_km: z.number().nonnegative().default(0),
});
export type V2BeforeAfterSummary = z.infer<typeof v2BeforeAfterSummarySchema>;

// ---------------------------------------------------------------------------
// 機能 A: 差分追加 (POST /diff-add)
// ---------------------------------------------------------------------------

export const diffAddRequestSchema = z.object({
  iso_year: z.number().int().min(2020).max(2100),
  iso_week: z.number().int().min(1).max(53),
  /** 空配列 = 全拠点 (Backend 契約). */
  office_ids: z.array(z.string().uuid()).default([]),
});
export type DiffAddRequest = z.infer<typeof diffAddRequestSchema>;

export const diffAddProposalSchema = z.object({
  proposal_id: z.string().uuid(),
  patient_id: z.string().uuid(),
  patient_name: z.string(),
  patient_code: z.string().nullable().optional(),
  suggested: v2VisitPlanSchema,
  suggested_visits: z.array(v2VisitPlanSchema).default([]),
  before_summary: v2BeforeAfterSummarySchema,
  after_summary: v2BeforeAfterSummarySchema,
  delta: v2ProposalDeltaSchema,
  warnings: z.array(v2WarningSchema).default([]),
});
export type DiffAddProposal = z.infer<typeof diffAddProposalSchema>;

export const diffAddResponseSchema = z.object({
  proposal_batch_id: z.string().uuid(),
  proposals: z.array(diffAddProposalSchema).default([]),
  kpi_overall: v2KpiOverallSchema,
  warnings: z.array(v2WarningSchema).default([]),
});
export type DiffAddResponse = z.infer<typeof diffAddResponseSchema>;

// ---------------------------------------------------------------------------
// 機能 B: 全面最適化 (POST /full-optimize)
// ---------------------------------------------------------------------------

// W41 v2 拡張 (今週限定オーバーレイ): 提案算出時に PFV を一時的に上書きするための
// 保留変更. /full-optimize と /apply-week-only に渡せる. マスター (PFV) は変更しない.
export const pendingFixedTimeEditSchema = z.object({
  patient_id: z.string().uuid(),
  weekday: z.number().int().min(0).max(6),
  new_start: z.string(),
  new_end: z.string().nullable().default(null),
  // W41 v2 cross-review (M-Codex-3): backend Literal と一致させる.
  // string のままでは UI ロジックの誤った値を silent に通してしまうため enum 化.
  new_time_type: z.enum(['固定', '時間帯', '午前', '午後', '終日']).nullable().default(null),
});
export type PendingFixedTimeEdit = z.infer<typeof pendingFixedTimeEditSchema>;

export const fullOptimizeRequestSchema = z.object({
  iso_year: z.number().int().min(2020).max(2100),
  iso_week: z.number().int().min(1).max(53),
  office_ids: z.array(z.string().uuid()).default([]),
  // W41 v2 拡張: 今週限定オーバーレイ. 空配列で従来通り.
  pending_edits: z.array(pendingFixedTimeEditSchema).default([]),
});
export type FullOptimizeRequest = z.infer<typeof fullOptimizeRequestSchema>;

export const individualProposalSchema = z.object({
  proposal_id: z.string().uuid(),
  patient_id: z.string().uuid(),
  patient_name: z.string(),
  patient_code: z.string().nullable().optional(),
  current_pfv: z.array(v2VisitPlanSchema).default([]),
  proposed_pfv: z.array(v2VisitPlanSchema).default([]),
  delta: v2ProposalDeltaSchema,
  warnings: z.array(v2WarningSchema).default([]),
});
export type IndividualProposal = z.infer<typeof individualProposalSchema>;

// ---------------------------------------------------------------------------
// W41 v2 (Mode 2 UI 拡張): 未割当患者 — 全面最適化で after_visits に
// 入らなかった患者と理由
// ---------------------------------------------------------------------------

export const unassignedPatientSchema = z.object({
  patient_id: z.string().uuid(),
  patient_name: z.string(),
  patient_code: z.string().nullable().default(null),
  reason: z.string(),
});
export type UnassignedPatient = z.infer<typeof unassignedPatientSchema>;

export const fullOptimizeResponseSchema = z.object({
  proposal_batch_id: z.string().uuid(),
  week_proposals: z.array(v2WeekdayBeforeAfterSchema).default([]),
  individual_proposals: z.array(individualProposalSchema).default([]),
  kpi_overall: v2KpiOverallSchema,
  warnings: z.array(v2WarningSchema).default([]),
  // W41 v2 (Mode 2 UI 拡張): pool に入れたが after_visits に出てこなかった患者.
  unassigned_patients: z.array(unassignedPatientSchema).default([]),
});
export type FullOptimizeResponse = z.infer<typeof fullOptimizeResponseSchema>;

// ---------------------------------------------------------------------------
// 機能 A/B 共通: 個別採用 (POST /apply-individual)
//
// 仕様書 §13.5 Q2: 患者ごとに 1 件ずつ採用 → patient_fixed_visits を更新する。
// 採用時は ``visit_plans`` (採用する visit 配置の全リスト) を送る stateless 設計.
// ---------------------------------------------------------------------------

export const applyIndividualRequestSchema = z.object({
  /** 差分追加から来た場合: proposal_id のみで足りる */
  proposal_id: z.string().uuid().optional(),
  /** 全面最適化から来た場合: batch_id + patient_id で 1 患者分を採用 */
  proposal_batch_id: z.string().uuid().optional(),
  patient_id: z.string().uuid().optional(),
  /** 必須: 採用確認フラグ (BE 側で confirm=true を強制). */
  confirm: z.literal(true).default(true),
  iso_year: z.number().int().min(2020).max(2100).optional(),
  iso_week: z.number().int().min(1).max(53).optional(),
  /** 採用する visit 配置. patient_id の固定枠を全置換する. */
  visit_plans: z.array(v2VisitPlanSchema).default([]),
});
export type ApplyIndividualRequest = z.infer<typeof applyIndividualRequestSchema>;

export const applyIndividualResponseSchema = z.object({
  patient_id: z.string().uuid(),
  applied: z.boolean(),
  fixed_visit_ids: z.array(z.string().uuid()).default([]),
  idempotent: z.boolean().default(false),
  warnings: z.array(z.string()).default([]),
});
export type ApplyIndividualResponse = z.infer<typeof applyIndividualResponseSchema>;

// ---------------------------------------------------------------------------
// 機能 D: 固定枠に戻す (POST /reset-to-fixed)
// ---------------------------------------------------------------------------

export const resetToFixedRequestSchema = z.object({
  iso_year: z.number().int().min(2020).max(2100),
  iso_week: z.number().int().min(1).max(53),
  office_ids: z.array(z.string().uuid()).default([]),
  // W41 v2 final cross-review (M-Codex-1): UI 側の確認ダイアログを経由した
  // ことを示すマーカー. Backend の AutoScheduleV2ResetToFixedRequest.confirm が
  // Literal[True] のため必ず true を送る必要がある.
  confirm: z.literal(true).default(true),
});
export type ResetToFixedRequest = z.infer<typeof resetToFixedRequestSchema>;

export const resetToFixedResponseSchema = z.object({
  visits_regenerated: z.number().int().nonnegative(),
  visits_soft_deleted: z.number().int().nonnegative(),
  courses_used: z.number().int().nonnegative(),
  warnings: z.array(z.string()).default([]),
});
export type ResetToFixedResponse = z.infer<typeof resetToFixedResponseSchema>;

// ---------------------------------------------------------------------------
// この週だけ反映 (POST /apply-week-only)
//
// 全面最適化結果を **その週の visits だけ** に反映する慎重モード.
// patient_fixed_visits は更新しないので、翌週からは元の固定枠ベースに戻る.
// ---------------------------------------------------------------------------

export const v2PatientVisitPlansSchema = z.object({
  patient_id: z.string().uuid(),
  visit_plans: z.array(v2VisitPlanSchema).default([]),
});
export type V2PatientVisitPlans = z.infer<typeof v2PatientVisitPlansSchema>;

export const applyWeekOnlyRequestSchema = z.object({
  iso_year: z.number().int().min(2020).max(2100),
  iso_week: z.number().int().min(1).max(53),
  office_ids: z.array(z.string().uuid()).default([]),
  visit_plans_per_patient: z.array(v2PatientVisitPlansSchema).default([]),
  // W41 v2 拡張 (今週限定オーバーレイ): visit_plans に既に反映済みでも、念のため
  // backend 側でも (patient_id, weekday) ベースで再適用される.
  pending_edits: z.array(pendingFixedTimeEditSchema).default([]),
  /** UI 側の確認ダイアログを経由したことを示すマーカー (BE は Literal[True]). */
  confirm: z.literal(true).default(true),
});
export type ApplyWeekOnlyRequest = z.infer<typeof applyWeekOnlyRequestSchema>;

export const applyWeekOnlyResponseSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  visits_created: z.number().int().nonnegative(),
  visits_soft_deleted: z.number().int().nonnegative(),
  courses_created: z.number().int().nonnegative(),
  visit_staff_assignments_created: z.number().int().nonnegative(),
  warnings: z.array(z.string()).default([]),
});
export type ApplyWeekOnlyResponse = z.infer<typeof applyWeekOnlyResponseSchema>;

// ---------------------------------------------------------------------------
// W41 v2 拡張: 固定時間変更 (警告アクションから呼ぶ)
// ---------------------------------------------------------------------------

const hhmmSchema = z.string().regex(/^\d{1,2}:\d{2}(:\d{2})?$/, '時刻は HH:MM 形式');

export const updateFixedTimeMasterRequestSchema = z.object({
  patient_id: z.string().uuid(),
  weekday: weekdaySchema,
  new_start: hhmmSchema,
  new_end: hhmmSchema.nullable().optional(),
  new_time_type: z.enum(['固定', '時間帯', '午前', '午後', '終日']).nullable().optional(),
});
export type UpdateFixedTimeMasterRequest = z.infer<typeof updateFixedTimeMasterRequestSchema>;

export const updateFixedTimeMasterResponseSchema = z.object({
  updated: z.boolean(),
  patient_id: z.string().uuid(),
  weekday: weekdaySchema,
});
export type UpdateFixedTimeMasterResponse = z.infer<typeof updateFixedTimeMasterResponseSchema>;

export const updateFixedTimeWeekOnlyRequestSchema = z.object({
  visit_id: z.string().uuid(),
  new_start: hhmmSchema,
  new_end: hhmmSchema.nullable().optional(),
});
export type UpdateFixedTimeWeekOnlyRequest = z.infer<typeof updateFixedTimeWeekOnlyRequestSchema>;

export const updateFixedTimeWeekOnlyResponseSchema = z.object({
  updated: z.boolean(),
  visit_id: z.string().uuid(),
});
export type UpdateFixedTimeWeekOnlyResponse = z.infer<typeof updateFixedTimeWeekOnlyResponseSchema>;
