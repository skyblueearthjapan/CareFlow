/**
 * OfficeV2 zod schemas (Wave 0-C).
 *
 * 設計仕様書 v0.9 §4.3 に基づく拠点マスタ v2。
 * v1 から大きな変更はないが、v2 では:
 *   - スタッフの自宅起点を廃止 → 距離計算の起点は **常に拠点住所**
 *   - 患者は住所から自動で拠点に紐付く (`OfficeAssigner`、W1-BE3)
 *   - 拠点マスタの編集は **AI 経由不可** (地理データを含むため Web フォームから)
 *
 * `allowed_cities` は M2M (`office_cities`) の id list を schema に直接含める方針。
 *
 * Backend `backend/app/schemas/v2/office.py` の Pydantic schema と
 * **完全一致** させる。
 */
import { z } from 'zod';

/**
 * Phase G-45: 拠点稼働曜日 (= operating_weekdays).
 *
 * 0=月..6=日 の int 配列で表現。重複なし、最低 1 個必須。
 * Backend 側の Pydantic validator (`_validate_operating_weekdays`) と
 * 同じ規約をフロントでも適用する。
 */
export const operatingWeekdaysSchema = z
  .array(z.number().int().min(0).max(6))
  .min(1, '稼働曜日は最低 1 日選択してください')
  .refine((arr) => new Set(arr).size === arr.length, {
    message: '稼働曜日に重複する曜日があります',
  });
export const DEFAULT_OPERATING_WEEKDAYS = [0, 1, 2, 3, 4, 5] as const;

/**
 * 拠点マスタ v2 (§4.3).
 *
 * 距離計算の起点として使う `address / lat / lng` を必須情報として扱う
 * (実装上は nullable だが、稲毛 / 都賀の運用では必ず値が入る)。
 */
export const officeV2BaseSchema = z.object({
  code: z.string().max(64).nullable().optional(),
  name: z.string().min(1, '拠点名は必須です').max(120),
  prefecture: z.string().max(8).nullable().optional(),
  /** 距離計算の起点として使用 (§4.3) */
  address: z.string().nullable().optional(),
  lat: z.number().min(-90).max(90).nullable().optional(),
  lng: z.number().min(-180).max(180).nullable().optional(),
  note: z.string().nullable().optional(),
  /** Phase G-45: 拠点稼働曜日 (0=月..6=日 の int 配列). default = 月-土. */
  operating_weekdays: operatingWeekdaysSchema.default([...DEFAULT_OPERATING_WEEKDAYS]),
});
export type OfficeV2Base = z.infer<typeof officeV2BaseSchema>;

/**
 * POST /api/v1/offices リクエスト (W1-BE3).
 *
 * `allowed_cities` は cities テーブルの id 配列。
 * M2M テーブル `office_cities` に永続化される。
 * 1 拠点が複数市区町村を担当可能 (§4.3)。
 */
export const officeV2CreateSchema = officeV2BaseSchema.extend({
  allowed_cities: z.array(z.string().uuid()).default([]),
});
export type OfficeV2Create = z.infer<typeof officeV2CreateSchema>;

/** PATCH /api/v1/offices/{id} リクエスト. */
export const officeV2UpdateSchema = z.object({
  code: z.string().max(64).nullable().optional(),
  name: z.string().min(1).max(120).optional(),
  prefecture: z.string().max(8).nullable().optional(),
  address: z.string().nullable().optional(),
  lat: z.number().min(-90).max(90).nullable().optional(),
  lng: z.number().min(-180).max(180).nullable().optional(),
  note: z.string().nullable().optional(),
  /** Phase G-45: PATCH では未指定 (undefined) で「触らない」. */
  operating_weekdays: operatingWeekdaysSchema.optional(),
  allowed_cities: z.array(z.string().uuid()).nullable().optional(),
});
export type OfficeV2Update = z.infer<typeof officeV2UpdateSchema>;

/** GET /api/v1/offices/{id} レスポンス. */
export const officeV2ReadSchema = officeV2BaseSchema.extend({
  id: z.string().uuid(),
  created_at: z.string(),
  updated_at: z.string(),
  deleted_at: z.string().nullable().optional(),
  allowed_cities: z.array(z.string().uuid()).default([]),
});
export type OfficeV2Read = z.infer<typeof officeV2ReadSchema>;

// ---------------------------------------------------------------------------
// POST /api/v1/offices/resolve — W1-BE3 (W12-FE: frontend integration)
// ---------------------------------------------------------------------------

/**
 * confidence レベル:
 *   - 'exact'  → 住所が担当市区町村に完全一致
 *   - 'fuzzy'  → 部分一致 / 近隣エリア
 *   - 'none'   → 担当エリア外 (手動選択を促す)
 */
export const RESOLVE_CONFIDENCE = ['exact', 'fuzzy', 'none'] as const;
export type ResolveConfidence = (typeof RESOLVE_CONFIDENCE)[number];

/** POST /api/v1/offices/resolve レスポンス. */
export const officeResolveResponseSchema = z.object({
  office_id: z.string().uuid().nullable(),
  office_name: z.string().nullable(),
  matched_city_id: z.string().uuid().nullable(),
  confidence: z.enum(RESOLVE_CONFIDENCE),
});
export type OfficeResolveResponse = z.infer<typeof officeResolveResponseSchema>;

/** POST /api/v1/offices/resolve リクエスト. */
export const officeResolveRequestSchema = z.object({
  address: z.string().min(1),
});
