/**
 * Checkin settings (QR 訪問チェックインしきい値) zod schemas — Phase 4。
 *
 * Mirrors the backend contract (`/api/v1/checkin-settings`):
 *   GET  → { values: {...6項目}, is_default: {...6項目 bool} }
 *   PUT  → 部分更新 (omit=不変, 明示 null=既定リセット), 範囲外/review<match→422, admin/manager.
 *          レスポンスは GET と同形.
 *   GET /public → { match_m, review_m, accuracy_m } (全ログイン可・モバイル同期用).
 *
 * 6 項目 (すべて全社一律・1 セット):
 *   ① match_m            int 既定 100 / 10..1000
 *   ② review_m           int 既定 300 / 10..2000  (review_m >= match_m)
 *   ③ accuracy_m         int 既定 50  / 5..500
 *   ④ no_show_grace_min  int 既定 20  / 0..240
 *   ⑤ late_min           int 既定 15  / 0..240
 *   ⑥ max_inprogress_min int 既定 240 / 30..1440
 *
 * 範囲チェックは UI 側でも clamp するが、最終的な 422 は BE が担保する。
 */
import { z } from 'zod';

/** GET / PUT レスポンスの `values`。BE が常に全 6 項目を返す。 */
export const checkinSettingsValuesSchema = z.object({
  match_m: z.number().int(),
  review_m: z.number().int(),
  accuracy_m: z.number().int(),
  no_show_grace_min: z.number().int(),
  late_min: z.number().int(),
  max_inprogress_min: z.number().int(),
});

/** 各項目が既定値由来か (= ユーザー未設定) を示すフラグ群。 */
export const checkinSettingsIsDefaultSchema = z.object({
  match_m: z.boolean(),
  review_m: z.boolean(),
  accuracy_m: z.boolean(),
  no_show_grace_min: z.boolean(),
  late_min: z.boolean(),
  max_inprogress_min: z.boolean(),
});

/** GET / PUT のレスポンス全体。 */
export const checkinSettingsResponseSchema = z.object({
  values: checkinSettingsValuesSchema,
  is_default: checkinSettingsIsDefaultSchema,
});

/**
 * PUT のリクエストボディ。
 *   - 省略 (undefined) = 不変
 *   - 明示 null        = 既定値にリセット
 *   - 値               = 更新
 */
export const checkinSettingsUpdateSchema = z.object({
  match_m: z.number().int().nullable().optional(),
  review_m: z.number().int().nullable().optional(),
  accuracy_m: z.number().int().nullable().optional(),
  no_show_grace_min: z.number().int().nullable().optional(),
  late_min: z.number().int().nullable().optional(),
  max_inprogress_min: z.number().int().nullable().optional(),
});

/** GET /public のレスポンス (距離系のみ・モバイル到着プレビュー同期用)。 */
export const checkinSettingsPublicSchema = z.object({
  match_m: z.number().int(),
  review_m: z.number().int(),
  accuracy_m: z.number().int(),
});

export type CheckinSettingsValues = z.infer<typeof checkinSettingsValuesSchema>;
export type CheckinSettingsIsDefault = z.infer<typeof checkinSettingsIsDefaultSchema>;
export type CheckinSettingsResponse = z.infer<typeof checkinSettingsResponseSchema>;
export type CheckinSettingsUpdate = z.infer<typeof checkinSettingsUpdateSchema>;
export type CheckinSettingsPublic = z.infer<typeof checkinSettingsPublicSchema>;

/** PUT で「全項目を既定にリセット」する payload (各項目 null)。 */
export const CHECKIN_SETTINGS_RESET_ALL: CheckinSettingsUpdate = {
  match_m: null,
  review_m: null,
  accuracy_m: null,
  no_show_grace_min: null,
  late_min: null,
  max_inprogress_min: null,
};

/** UI の範囲制約 (スライダー / バリデーション用)。BE 契約と一致。 */
export const CHECKIN_RANGES = {
  match_m: { min: 10, max: 1000, step: 10 },
  review_m: { min: 10, max: 2000, step: 10 },
  accuracy_m: { min: 5, max: 500, step: 5 },
  no_show_grace_min: { min: 0, max: 240, step: 5 },
  late_min: { min: 0, max: 240, step: 5 },
  max_inprogress_min: { min: 30, max: 1440, step: 30 },
} as const;

/** 距離プレビュー (概算) のフォールバック既定値 (public 取得失敗時)。 */
export const CHECKIN_PUBLIC_FALLBACK: CheckinSettingsPublic = {
  match_m: 100,
  review_m: 300,
  accuracy_m: 50,
};
