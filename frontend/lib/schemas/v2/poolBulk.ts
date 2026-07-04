/**
 * プール一括投入 (W-2) zod スキーマ.
 *
 * BE 契約 (設計書 `docs/plans/pool-bulk-insert-design.md` §4 を正典とする):
 *   POST /api/v1/schedule/v2/pool-bulk-simulate (read-only)
 *   POST /api/v1/schedule/v2/pool-bulk-apply    (1 TX 一括適用)
 *   RBAC: admin / manager (BE 側で 403 担保).
 *
 * BE simulate schema (`backend/app/schemas/v2/pool_bulk.py`, コミット 1cb702d) と 1:1。
 * apply の Request/Response は設計書 §4 を正典とする (BE 並行実装中)。
 *
 * 寛容パース規約 (improvementSuggestion の kind 方式踏襲):
 *   - placements / partial / unplaced は要素単位で safeParse し、パース不能な要素
 *     (= 未知 enum 値等) を静かに除外する。これによりセクション全滅を防ぐ。
 *   - warnings 系は `.catch([])`、KPI 欠落フィールドは `.default()` で drift 吸収。
 *   - start_time は "HH:MM:SS" (pool-overview と同形式) をそのまま文字列保持。
 */
import { z } from 'zod';

import { v2WeekdayBeforeAfterSchema } from './autoScheduleV2';

/** pool-bulk API 上限 (BE POOL_BULK_MAX_PATIENTS と同値。超過は 422). */
export const POOL_BULK_MAX_PATIENTS = 50;

// ---------------------------------------------------------------------------
// 寛容パースヘルパー (未知要素をセクション全滅させず静かに除外)
// ---------------------------------------------------------------------------

/**
 * 配列を要素単位で safeParse し、パース不能な要素を除外する寛容配列スキーマ。
 * improvementSuggestion.ts の tolerantSuggestionsArraySchema と同じ設計。
 */
function tolerantArray<T extends z.ZodTypeAny>(schema: T) {
  return z.preprocess((val) => {
    if (!Array.isArray(val)) return val;
    return val.filter((el) => schema.safeParse(el).success);
  }, z.array(schema));
}

// ---------------------------------------------------------------------------
// Response sub-models
// ---------------------------------------------------------------------------

/** 投入成功した 1 枠 (患者 × 曜日 × コース). seq は投入順の通し番号. */
export const poolBulkPlacementSchema = z.object({
  seq: z.number().int(),
  patient_id: z.string(),
  patient_name: z.string(),
  weekday: z.number().int().min(0).max(6),
  course_code: z.string(),
  office_id: z.string(),
  /** "HH:MM:SS" (pool-overview と同形式). 表示側で秒を落とす. */
  start_time: z.string(),
  service_minutes: z.number().int(),
  /** 挿入の厳密限界コスト (分/週). 個別提案と同一の物差し. 未評価は null. */
  delta_minutes: z.number().nullable().optional().default(null),
  /** propose-slots と同一語彙の警告コード (staff_absent 等). */
  warnings: z.array(z.string()).catch([]),
});
export type PoolBulkPlacement = z.infer<typeof poolBulkPlacementSchema>;

/** 部分投入 (不足曜日の一部だけ入った) 患者 1 名の要約. */
export const poolBulkPartialSchema = z.object({
  patient_id: z.string(),
  patient_name: z.string(),
  placed_days: z.number().int(),
  missing_days: z.number().int(),
  /** 投入できなかった希望曜日 (str) → 理由コード (excluded_summary と同語彙). */
  unplaced_reasons: z.record(z.string(), z.string()).catch({}),
});
export type PoolBulkPartial = z.infer<typeof poolBulkPartialSchema>;

/** 投入不能 (1 枠も入らなかった) 患者 1 名の要約. */
export const poolBulkUnplacedSchema = z.object({
  patient_id: z.string(),
  patient_name: z.string(),
  /** 理由コード (capacity_full/travel_shortage/lunch_window/no_gap/course_closed/no_coordinates). */
  reason: z.string(),
});
export type PoolBulkUnplaced = z.infer<typeof poolBulkUnplacedSchema>;

/** プレビュー用サマリ KPI (投入件数 + 週全体の移動時間/距離 before→after). */
export const poolBulkKpiSchema = z.object({
  placed_patients: z.number().int().default(0),
  placed_slots: z.number().int().default(0),
  travel_minutes_before: z.number().default(0),
  travel_minutes_after: z.number().default(0),
  travel_km_before: z.number().default(0),
  travel_km_after: z.number().default(0),
});
export type PoolBulkKpi = z.infer<typeof poolBulkKpiSchema>;

// ---------------------------------------------------------------------------
// simulate: Request / Response
// ---------------------------------------------------------------------------

export const poolBulkSimulateRequestSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int().min(1).max(53),
  /** 対象拠点 (単一必須). state_token が拠点単位の指紋のため. */
  office_id: z.string(),
  /** 保留プール患者 id 列 (FE が抽出して送る. pool-overview と同方式). 最大 50 件. */
  patient_ids: z.array(z.string()).max(POOL_BULK_MAX_PATIENTS),
  /** 投入順序. v1 は hybrid のみ (D-1). */
  ordering: z.literal('hybrid').default('hybrid'),
});
export type PoolBulkSimulateRequest = z.input<typeof poolBulkSimulateRequestSchema>;

export const poolBulkSimulateResponseSchema = z.object({
  placements: tolerantArray(poolBulkPlacementSchema).default([]),
  partial: tolerantArray(poolBulkPartialSchema).default([]),
  unplaced: tolerantArray(poolBulkUnplacedSchema).default([]),
  /** ProposalWeekCalendar / BeforeAfterWeekPanel 互換の before/after 週ビュー. */
  week_before_after: tolerantArray(v2WeekdayBeforeAfterSchema).default([]),
  kpi: poolBulkKpiSchema.default({}),
  /** 楽観ロック用指紋 (apply で再計算し不一致なら 409). */
  state_token: z.string(),
});
export type PoolBulkSimulateResponse = z.infer<typeof poolBulkSimulateResponseSchema>;

// ---------------------------------------------------------------------------
// apply: Request / Response
// ---------------------------------------------------------------------------

/**
 * POST /v2/pool-bulk-apply リクエスト (設計書 §4).
 *   - placements: simulate の placements をそのまま送る (プレフィックス選択は v1 なし = 全件).
 *   - change_scope は持たせない = 仕様として pattern_and_week (A) 固定 (D-2).
 *   - state_token 不一致は BE が 409 を返す (FE は再計算導線を出す).
 */
export const poolBulkApplyRequestSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int().min(1).max(53),
  office_id: z.string(),
  placements: z.array(poolBulkPlacementSchema),
  state_token: z.string(),
});
export type PoolBulkApplyRequest = z.input<typeof poolBulkApplyRequestSchema>;

/** POST /v2/pool-bulk-apply レスポンス (設計書 §4). */
export const poolBulkApplyResponseSchema = z.object({
  applied_patients: z.number().int().default(0),
  applied_slots: z.number().int().default(0),
  warnings: z.array(z.string()).catch([]),
});
export type PoolBulkApplyResponse = z.infer<typeof poolBulkApplyResponseSchema>;
