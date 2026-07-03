/**
 * pool-overview v2 zod スキーマ (Stage P-2).
 *
 * BE 契約: POST /api/v1/schedule/v2/pool-overview
 *   Request:  { iso_year, iso_week, office_id: string | null, patient_ids: string[] }
 *   Response: { items: PoolOverviewItem[] }
 *
 * 寛容パース規約 (BE 並行実装のためフィールド欠落・未知値を許容):
 *   - items 欠落 → [] にフォールバック (.default([]))
 *   - best_slot 欠落 → null にフォールバック
 *   - best_delta_minutes 欠落 → null
 *   - candidate_count 欠落 → 0
 *   - top_excluded_reason 欠落 / 未知値 → そのまま保持して表示側でフォールバック
 */
import { z } from 'zod';

// ---------------------------------------------------------------------------
// Request
// ---------------------------------------------------------------------------

export const poolOverviewRequestSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int().min(1).max(53),
  /** null = 全拠点. */
  office_id: z.string().nullable(),
  /** 最大 50 件 (BE は超過時 422 を返す). FE 側で先頭 50 件に切ること. */
  patient_ids: z.array(z.string()).max(50),
});

export type PoolOverviewRequest = z.infer<typeof poolOverviewRequestSchema>;

// ---------------------------------------------------------------------------
// Response
// ---------------------------------------------------------------------------

/**
 * 患者ごとの最良候補スロット情報.
 * BE が返さない場合 null (寛容パース: nullable().default(null))。
 */
export const poolOverviewBestSlotSchema = z.object({
  weekday: z.number().int().min(0).max(6),
  course_code: z.string(),
  office_id: z.string(),
  start_time: z.string(),
  end_time: z.string(),
});
export type PoolOverviewBestSlot = z.infer<typeof poolOverviewBestSlotSchema>;

/**
 * プール患者 1 件分の俯瞰情報.
 *
 * 寛容パース規約:
 *   - best_slot: nullable().optional().default(null) — BE 未送出時 null
 *   - best_delta_minutes: nullable().optional().default(null)
 *   - candidate_count: .default(0)
 *   - top_excluded_reason: nullable().optional().default(null) — 未知値はそのまま文字列保持
 */
export const poolOverviewItemSchema = z.object({
  patient_id: z.string(),
  best_slot: poolOverviewBestSlotSchema.nullable().optional().default(null),
  best_delta_minutes: z.number().nullable().optional().default(null),
  candidate_count: z.number().int().default(0),
  top_excluded_reason: z.string().nullable().optional().default(null),
});
export type PoolOverviewItem = z.infer<typeof poolOverviewItemSchema>;

/**
 * POST /api/v1/schedule/v2/pool-overview のレスポンス。
 *
 * items 欠落 → [] にフォールバック (寛容パース規約)。
 */
export const poolOverviewResponseSchema = z.object({
  items: z.array(poolOverviewItemSchema).default([]),
});
export type PoolOverviewResponse = z.infer<typeof poolOverviewResponseSchema>;
