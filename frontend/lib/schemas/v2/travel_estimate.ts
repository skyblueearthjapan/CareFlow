/**
 * 移動時間見積 API (Phase G-84) zod schemas — 現場ボードの空き枠直接配置用。
 *
 * Backend `backend/app/schemas/v2/travel_estimate.py` の Pydantic schema と
 * **完全一致** させる。POST /api/v1/schedule/v2/travel-estimate のリクエスト /
 * レスポンスを型安全に扱う。
 *
 * 用途: 空き枠タップ時の PlacementSheet で「直前訪問の患者 → 配置先」の移動 +
 * バッファー所要分を求め、開始時刻の初期値 (案2) を算出する (read-only)。
 *   - 新規患者: 配置先住所を `to_address` で送る (サーバが geocode)。
 *   - 既存患者: 既知座標を `to_lat` / `to_lng` に載せ geocode を省略する。
 *   - from は直前 visit の patient_id (無ければ from_lat/lng フォールバック)。
 * どちらか座標が確定できない場合は minutes が null で返り、フロントは枠頭
 * (案1) にフォールバックする。
 */
import { z } from 'zod';

/** POST /v2/travel-estimate リクエスト (全項目 任意 / null 可)。 */
export const travelEstimateRequestSchema = z.object({
  /** 直前訪問の患者 (board visit.patient_id)。座標解決の起点。 */
  from_patient_id: z.string().uuid().nullish(),
  /** from_patient_id が無いとき用の起点座標フォールバック。 */
  from_lat: z.number().min(-90).max(90).nullish(),
  from_lng: z.number().min(-180).max(180).nullish(),
  /** 新規患者: 配置先住所 (サーバ側 geocode)。 */
  to_address: z.string().nullish(),
  /** 既存患者: 既知座標 (geocode 省略)。 */
  to_lat: z.number().min(-90).max(90).nullish(),
  to_lng: z.number().min(-180).max(180).nullish(),
});
export type TravelEstimateRequest = z.input<typeof travelEstimateRequestSchema>;

/** POST /v2/travel-estimate レスポンス (移動 + バッファー所要分)。 */
export const travelEstimateResponseSchema = z.object({
  /** 起点座標を確定できたか。 */
  from_resolved: z.boolean(),
  /** 配置先座標を確定できたか。 */
  to_resolved: z.boolean(),
  /** 移動所要 (分)。座標未確定なら null。 */
  travel_minutes: z.number().int().nullable(),
  /** バッファー (分) = VISIT_BUFFER_MINUTES。 */
  buffer_minutes: z.number().int(),
  /** travel + buffer (分)。travel が null なら null。 */
  total_minutes: z.number().int().nullable(),
});
export type TravelEstimateResponse = z.infer<typeof travelEstimateResponseSchema>;
