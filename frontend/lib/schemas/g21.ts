/**
 * Phase G-21 zod schemas.
 *
 * Backend endpoints (T1/T2) と完全一致させる:
 *   - PATCH /api/v1/patients/fixed-visits/{pfv_id}/pin       body: { is_pinned: boolean }
 *   - POST  /api/v1/patients/fixed-visits/pin/bulk           body: [{pfv_id, is_pinned}, ...]
 *   - GET   /api/v1/patients/{patient_id}/same-address-candidates
 *   - POST  /api/v1/patient-same-address-links               body: {patient_a_id, patient_b_id, pair_mode, note?}
 *   - DELETE /api/v1/patient-same-address-links/{a}/{b}
 *   - GET   /api/v1/patient-same-address-links?patient_id=X
 *   - GET   /api/v1/office-feature-flags
 *   - POST  /api/v1/office-feature-flags                     body: {office_id, feature_key, enabled, note?}
 *   - DELETE /api/v1/office-feature-flags/{office_id}/{feature_key}
 */
import { z } from 'zod';

// ─────────────────────────────────────────────────────────────────────────
// 完全固定 PFV (pin)
// ─────────────────────────────────────────────────────────────────────────

/** PATCH /pin リクエスト body. */
export const pfvPinPatchSchema = z.object({
  is_pinned: z.boolean(),
});
export type PfvPinPatch = z.infer<typeof pfvPinPatchSchema>;

/** bulk PUT/POST 1 件分. */
export const pfvPinBulkItemSchema = z.object({
  pfv_id: z.string().uuid(),
  is_pinned: z.boolean(),
});
export type PfvPinBulkItem = z.infer<typeof pfvPinBulkItemSchema>;

/**
 * bulk POST body は items[] そのもの.
 *
 * Phase G-21 T4 reviewer L2: BE 側 (`pin/bulk` 422 検証) と同様、payload 内に
 * 重複した `pfv_id` があれば FE 側でも事前検出して error にする (= 同 PFV を
 * 矛盾した `is_pinned` 値で複数回更新するような不整合 payload を防ぐ).
 */
export const pfvPinBulkRequestSchema = z.array(pfvPinBulkItemSchema).superRefine((items, ctx) => {
  const seen = new Set<string>();
  items.forEach((it, i) => {
    if (seen.has(it.pfv_id)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: [i, 'pfv_id'],
        message: '同じ pfv_id が複数回指定されています',
      });
    }
    seen.add(it.pfv_id);
  });
});
export type PfvPinBulkRequest = z.infer<typeof pfvPinBulkRequestSchema>;

// ─────────────────────────────────────────────────────────────────────────
// pair_mode (同住所紐付け)
// ─────────────────────────────────────────────────────────────────────────

/**
 * 同住所患者間の紐付け方針.
 * - preferred: ペア優先 (= 既定 / 明示エントリで blocked/required を解除する用途)
 * - required : なるべくペアにする (= 強い preference)
 * - blocked  : 絶対にペア禁止
 *
 * Phase G-21 T4 reviewer L1: UI では preferred → required → blocked の順で
 * 描画したいため、 enum 列挙順をこの順に揃える (= schema enum もこの順).
 */
export const PAIR_MODES = ['preferred', 'required', 'blocked'] as const;
export type PairMode = (typeof PAIR_MODES)[number];
export const pairModeSchema = z.enum(PAIR_MODES);

// ─────────────────────────────────────────────────────────────────────────
// 同住所候補 / 紐付け
// ─────────────────────────────────────────────────────────────────────────

/**
 * GET /patients/{patient_id}/same-address-candidates のレスポンス 1 件.
 *
 * 候補リストには「紐付けが未設定の候補 (pair_mode=null)」と
 * 「既に紐付け済の候補 (pair_mode=blocked/required/preferred)」が混在しうる.
 */
export const sameAddressCandidateSchema = z.object({
  patient_id: z.string().uuid(),
  patient_code: z.string().nullable().optional(),
  patient_name: z.string().nullable().optional(),
  address: z.string().nullable().optional(),
  /** 既存リンクが無ければ null (= 既定の preferred 相当として扱う). */
  pair_mode: pairModeSchema.nullable().optional(),
  decided_by_user_id: z.string().uuid().nullable().optional(),
  note: z.string().nullable().optional(),
});
export type SameAddressCandidate = z.infer<typeof sameAddressCandidateSchema>;

/**
 * 閲覧系 (患者詳細の「訪問条件」など) の 1 行表示用ラベル。
 * 編集 UI (SameAddressLinksSection の radio) は説明的な長いラベルを使うため、
 * 一覧に並べる用の短縮版をここに置く。
 */
export const PAIR_MODE_SHORT_LABEL: Record<PairMode, string> = {
  preferred: 'ペア優先',
  required: 'ペア必須',
  blocked: 'ペア禁止',
};

/**
 * 同住所候補のうち **明示設定済み** (pair_mode あり) のものを
 * 「氏名（モード）・氏名（モード）」形式に整形する。0 件は「なし」。
 *
 * 手本: `formatNgStaffNames` (`lib/schemas/patient_ng_staff.ts`)。
 */
export function formatSameAddressLinkNames(candidates: readonly SameAddressCandidate[]): string {
  const decided = candidates.filter(
    (c): c is SameAddressCandidate & { pair_mode: PairMode } => c.pair_mode != null,
  );
  if (decided.length === 0) return 'なし';
  return decided
    .map((c) => `${c.patient_name ?? '(氏名未登録)'}（${PAIR_MODE_SHORT_LABEL[c.pair_mode]}）`)
    .join('・');
}

/**
 * POST /patient-same-address-links body.
 *
 * Phase G-21 T4 reviewer M3: FE 側 input maxLength=500 と整合させるため zod でも
 * note を 500 文字に制限する (BE は無制限だが UI で 500 を超えないため不一致を解消).
 */
export const patientSameAddressLinkCreateSchema = z.object({
  patient_a_id: z.string().uuid(),
  patient_b_id: z.string().uuid(),
  pair_mode: pairModeSchema,
  note: z.string().max(500, 'note は 500 文字以内で入力してください').nullable().optional(),
});
export type PatientSameAddressLinkCreate = z.infer<typeof patientSameAddressLinkCreateSchema>;

/** GET /patient-same-address-links レスポンス 1 件 (= persisted link). */
export const patientSameAddressLinkSchema = z.object({
  patient_a_id: z.string().uuid(),
  patient_b_id: z.string().uuid(),
  pair_mode: pairModeSchema,
  decided_by_user_id: z.string().uuid().nullable().optional(),
  note: z.string().nullable().optional(),
  created_at: z.string().optional(),
  updated_at: z.string().optional(),
});
export type PatientSameAddressLink = z.infer<typeof patientSameAddressLinkSchema>;

// ─────────────────────────────────────────────────────────────────────────
// Office feature flag
// ─────────────────────────────────────────────────────────────────────────

/**
 * フロント側で扱う feature key 一覧.
 * 現状は g21_new_algorithm のみだが、将来追加に備えて enum 化.
 */
export const OFFICE_FEATURE_KEYS = ['g21_new_algorithm'] as const;
export type OfficeFeatureKey = (typeof OFFICE_FEATURE_KEYS)[number];
export const officeFeatureKeySchema = z.enum(OFFICE_FEATURE_KEYS);

/**
 * Phase G-21 T4 reviewer C1: BE `OfficeFeatureFlagRead` は `enabled: bool` を
 * 直接返さず、レコード存在 (= `enabled_at IS NOT NULL`) を「有効」と派生計算する.
 * したがって FE schema からは `enabled` を required 必須から外し、optional/相手任せ
 * とする (互換のため field 自体は受け取れるよう残す). UI 側では
 * `enabled = flag?.enabled_at != null` で派生判定する.
 */
export const officeFeatureFlagSchema = z.object({
  office_id: z.string().uuid(),
  feature_key: officeFeatureKeySchema,
  /**
   * Deprecated: BE は返さない. 互換のため受け取れるが UI 表示には使わない.
   * FE は `enabled_at != null` を真の判定とする.
   */
  enabled: z.boolean().optional(),
  enabled_at: z.string().nullable().optional(),
  enabled_by_user_id: z.string().uuid().nullable().optional(),
  note: z.string().nullable().optional(),
});
export type OfficeFeatureFlag = z.infer<typeof officeFeatureFlagSchema>;

/** POST /office-feature-flags body. */
export const officeFeatureFlagUpsertSchema = z.object({
  office_id: z.string().uuid(),
  feature_key: officeFeatureKeySchema,
  enabled: z.boolean(),
  note: z.string().nullable().optional(),
});
export type OfficeFeatureFlagUpsert = z.infer<typeof officeFeatureFlagUpsertSchema>;
