/**
 * W-12d 詰まり解消相談 (propose-unblock) zod スキーマ。
 *
 * BE 契約 (設計書 `docs/plans/unblock-consult-design.md` §2.2 を正典とする):
 *   POST /api/v1/schedule/v2/propose-unblock       (read-only 探索)
 *   POST /api/v1/schedule/v2/propose-unblock/apply  (1 TX 適用)
 *   RBAC: admin / manager (BE 側で担保)。
 *
 * BE は並行実装中のため、契約を正として寛容パース (.default/.catch/nullish) で
 * 先行実装する (propose_slots / poolBulk の後方互換規約を踏襲)。
 *
 * リクエストの candidate 情報は propose-slots と同形 (address/lat/lng/希望条件 +
 * existing_patient_id) + { office_id, iso_year, iso_week, limit?: 5 }。
 */
import { z } from 'zod';

import { weekdayCodeSchema } from './board';
import { proposeTimeTypeSchema, proposeVisitFrequencySchema } from './propose_slots';

// ---------------------------------------------------------------------------
// 寛容パースヘルパー (未知要素をセクション全滅させず静かに除外)
// ---------------------------------------------------------------------------

/** 配列を要素単位で safeParse し、パース不能な要素を除外する寛容配列スキーマ。 */
function tolerantArray<T extends z.ZodTypeAny>(schema: T) {
  return z.preprocess((val) => {
    if (!Array.isArray(val)) return val;
    return val.filter((el) => schema.safeParse(el).success);
  }, z.array(schema));
}

// ---------------------------------------------------------------------------
// Request
// ---------------------------------------------------------------------------

/**
 * POST /v2/propose-unblock リクエスト。
 * candidate 情報は propose-slots と同形。office_id は単一 (state_token が拠点単位の
 * 指紋のため)。limit は既定 5 (上位プランのみ)。
 */
export const proposeUnblockRequestSchema = z.object({
  // 候補の座標確定 (address 優先 → lat/lng)。
  address: z.string().nullish(),
  lat: z.number().min(-90).max(90).nullish(),
  lng: z.number().min(-180).max(180).nullish(),

  // 候補の希望 (患者マスタ項目に準拠)。
  service_minutes: z.number().int().min(1).max(480),
  time_type: proposeTimeTypeSchema.nullish(),
  preferred_start: z.string().nullish(),
  preferred_end: z.string().nullish(),
  preferred_weekdays: z.array(weekdayCodeSchema).default([]),
  visit_frequency: proposeVisitFrequencySchema.nullish(),
  frequency_per_week: z.number().int().min(1).max(7).nullish(),
  requires_multiple_staff: z.boolean().default(false),
  sex_restriction: z.string().nullish(),
  existing_patient_id: z.string().uuid().nullish(),

  // 対象週 / 拠点 (単一)。
  // W-13a: office_id は省略可能。省略時 BE は対象患者の主担当拠点 (primary_office_id)
  // から自動解決する。ページで拠点選択中はそれを送る (従来どおり)。
  office_id: z.string().uuid().nullish(),
  iso_year: z.number().int().min(2020).max(2100),
  iso_week: z.number().int().min(1).max(53),

  limit: z.number().int().min(1).max(20).default(5),
});
export type ProposeUnblockRequest = z.input<typeof proposeUnblockRequestSchema>;

// ---------------------------------------------------------------------------
// Response sub-models
// ---------------------------------------------------------------------------

/** 手の始点 / 終点 (曜日・コース・時刻)。course_label は BE 未送出なら code で代替。 */
export const unblockMoveEndpointSchema = z.object({
  weekday: z.number().int().min(0).max(6),
  course_code: z.string(),
  course_label: z.string().nullish(),
  start_time: z.string(),
});
export type UnblockMoveEndpoint = z.infer<typeof unblockMoveEndpointSchema>;

/** 既存訪問 1 件の退避 (from → to)。within_preference=true は「希望範囲内」の手。 */
export const unblockMoveSchema = z.object({
  patient_id: z.string(),
  patient_name: z.string(),
  from: unblockMoveEndpointSchema,
  to: unblockMoveEndpointSchema,
  /** 手の効果 (分/週)。compute_exact_marginal。未評価は null。 */
  delta_minutes: z.number().nullish(),
  within_preference: z.boolean().default(false),
});
export type UnblockMove = z.infer<typeof unblockMoveSchema>;

/** 対象患者の配置先 (この枠に入れる)。partner_* は 2名体制ペアのときのみ。 */
export const unblockInsertSchema = z.object({
  weekday: z.number().int().min(0).max(6),
  course_code: z.string(),
  course_label: z.string().nullish(),
  start_time: z.string(),
  end_time: z.string().nullish(),
  /** 2名体制ペアの相方コース (非 null ならペア配置)。 */
  partner_course_code: z.string().nullish(),
  partner_course_label: z.string().nullish(),
});
export type UnblockInsert = z.infer<typeof unblockInsertSchema>;

/**
 * W-13b: 影響コースの before/after スナップショット 1 件 (BE が plan ごとに返す)。
 * entries は improvement 系スナップショット (CourseSnapshotVisit) と同形。
 * before/after を CourseMoveTimeline 系の正典部品で「変更前 → 変更後」の 2 列表示する。
 * 旧 BE (courses 未送出) でも壊れないよう寛容パース。
 */
export const unblockCourseEntrySchema = z.object({
  patient_id: z.string(),
  patient_name: z.string(),
  start_time: z.string(),
  end_time: z.string(),
});
export type UnblockCourseEntry = z.infer<typeof unblockCourseEntrySchema>;

export const unblockCourseBeforeAfterSchema = z.object({
  office_name: z.string().nullish(),
  weekday: z.number().int().min(0).max(6),
  course_code: z.string(),
  course_label: z.string().nullish(),
  before: tolerantArray(unblockCourseEntrySchema).default([]),
  after: tolerantArray(unblockCourseEntrySchema).default([]),
  // イベント表示 (2026-07-27): 担当スタッフのイベント (before/after 共通・緑カード)。
  events: z
    .array(z.object({ title: z.string(), start_time: z.string(), end_time: z.string() }))
    .catch([]),
});
export type UnblockCourseBeforeAfter = z.infer<typeof unblockCourseBeforeAfterSchema>;

/** 開通プラン 1 件 (moves ≤ 2 + insert 1)。乱れの小さい順にランキング済み。 */
export const unblockPlanSchema = z.object({
  plan_id: z.string(),
  moves: z.array(unblockMoveSchema).default([]),
  insert: unblockInsertSchema,
  total_delta_minutes: z.number().default(0),
  moved_count: z.number().int().default(0),
  // W-13b: 影響する全コース (移動元・移動先・配置先) の before/after。旧 BE 未送出 → []。
  courses: tolerantArray(unblockCourseBeforeAfterSchema).default([]),
  // W-15: 定員起因 (capacity_full) の探索で、ブロッカーを他コースへ退避させて定員を
  // 空けるプランなら true。FE は小バッジ「定員内に収まります」で明示する。
  // 旧 BE / 時間起因プラン (未送出) → false (寛容パース)。
  frees_capacity: z.boolean().default(false),
});
export type UnblockPlan = z.infer<typeof unblockPlanSchema>;

/** 動かせない訪問の内訳会計 (N-6「黙って諦めない」)。全欠落は 0 に落とす。 */
export const unblockUnmovableSummarySchema = z.object({
  pinned: z.number().int().catch(0).default(0),
  locked: z.number().int().catch(0).default(0),
  two_staff: z.number().int().catch(0).default(0),
  pair: z.number().int().catch(0).default(0),
  dismissed: z.number().int().catch(0).default(0),
  confirmation_required: z.number().int().catch(0).default(0),
});
export type UnblockUnmovableSummary = z.infer<typeof unblockUnmovableSummarySchema>;

const EMPTY_UNMOVABLE: UnblockUnmovableSummary = {
  pinned: 0,
  locked: 0,
  two_staff: 0,
  pair: 0,
  dismissed: 0,
  confirmation_required: 0,
};

/** POST /v2/propose-unblock レスポンス。 */
export const proposeUnblockResponseSchema = z.object({
  plans: tolerantArray(unblockPlanSchema).default([]),
  unmovable_summary: unblockUnmovableSummarySchema.catch(EMPTY_UNMOVABLE).default(EMPTY_UNMOVABLE),
  /** 楽観ロック用指紋 (apply で再計算し不一致なら 409)。scope-opt と同じ PFV 指紋。 */
  state_token: z.string(),
  /**
   * W-13a: office_id 省略時に BE が主担当拠点から解決した拠点 ID (apply の office スコープに使う)。
   * 旧 BE 未送出 → null。FE は apply 時 officeId ?? resolved_office_id の順で解決する。
   */
  resolved_office_id: z.string().uuid().nullish(),
});
export type ProposeUnblockResponse = z.infer<typeof proposeUnblockResponseSchema>;

// ---------------------------------------------------------------------------
// apply: Request / Response
// ---------------------------------------------------------------------------

/**
 * POST /v2/propose-unblock/apply リクエスト (設計書 §2.2)。
 *   - plan: propose-unblock の plan をそのまま送る。
 *   - target_patient_id: 対象患者 UUID。BE が plan_id 内の患者 ID との一致＋指紋照合で 422 を返す。
 *   - state_token 不一致は BE が 409 (FE は再探索導線)。
 *   - 反映先は pattern_and_week 固定 (v1・P-6)。change_scope は持たせない。
 */
export const unblockApplyRequestSchema = z.object({
  // W-13a: office_id 省略時は BE が対象患者の主担当拠点から再解決する (探索と同じ挙動)。
  office_id: z.string().nullish(),
  iso_year: z.number().int(),
  iso_week: z.number().int().min(1).max(53),
  /** 対象患者 UUID (BE の指紋照合に使用)。 */
  target_patient_id: z.string().uuid(),
  plan: unblockPlanSchema,
  state_token: z.string(),
});
export type UnblockApplyRequest = z.input<typeof unblockApplyRequestSchema>;

/** POST /v2/propose-unblock/apply レスポンス (設計書 §2.2)。 */
export const unblockApplyResponseSchema = z.object({
  applied_moves: z.number().int().default(0),
  inserted: z.boolean().default(false),
  warnings: z.array(z.string()).catch([]),
});
export type UnblockApplyResponse = z.infer<typeof unblockApplyResponseSchema>;
