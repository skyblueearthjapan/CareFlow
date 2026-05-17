/**
 * Patient sync zod schemas.
 *
 * Backend `backend/app/schemas/v2/patient_sync.py` の Pydantic schema と
 * **完全一致** させる.
 *
 * Endpoint:
 *   POST /api/v1/patients/{patient_id}/sync-week-visits-to-fixed
 *     今週 visits を patient_fixed_visits (mode='normal', slot_index=0) に upsert.
 *     dry_run=true (default) で diff のみ計算 / false で commit.
 *
 * Wave Next 1 cross-review 対応:
 *   - operation に "skipped" を追加 (multi-staff pair で当該 weekday を見送るケース).
 *   - SyncChangeEntry.reason / new=null を許容.
 *   - レスポンスに untouched_existing (今週 visit が無い既存 PFV) を追加.
 *   - summary に pfv_skipped を追加.
 */
import { z } from 'zod';

// ---------------------------------------------------------------------------
// 共通プリミティブ
// ---------------------------------------------------------------------------

const timeStringSchema = z
  .string()
  .regex(/^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/, '時刻は HH:MM 形式');

const weekdaySchema = z.number().int().min(0).max(6);

export const SYNC_CHANGE_OPERATIONS = ['insert', 'update', 'unchanged', 'skipped'] as const;
export type SyncChangeOperation = (typeof SYNC_CHANGE_OPERATIONS)[number];

// ---------------------------------------------------------------------------
// Request
// ---------------------------------------------------------------------------

export const syncWeekToFixedRequestSchema = z.object({
  iso_year: z.number().int().min(2000).max(2100),
  iso_week: z.number().int().min(1).max(53),
  /** True = diff のみ計算 (DB 変更なし) / False = 1 TX commit. default true. */
  dry_run: z.boolean().default(true),
});
export type SyncWeekToFixedRequest = z.infer<typeof syncWeekToFixedRequestSchema>;

// ---------------------------------------------------------------------------
// Response
// ---------------------------------------------------------------------------

export const syncPfvSnapshotSchema = z.object({
  weekday: weekdaySchema,
  start_time: timeStringSchema,
  duration_min: z.number().int().min(1).max(480),
  course_template_id: z.string().uuid().nullable().optional(),
});
export type SyncPfvSnapshot = z.infer<typeof syncPfvSnapshotSchema>;

export const syncChangeEntrySchema = z.object({
  weekday: weekdaySchema,
  operation: z.enum(SYNC_CHANGE_OPERATIONS),
  /** insert の場合は null. update / unchanged では既存 PFV のスナップショット. */
  old: syncPfvSnapshotSchema.nullable().optional(),
  /** ``skipped`` の場合は null. */
  new: syncPfvSnapshotSchema.nullable().optional(),
  reason: z.string().nullable().optional(),
});
export type SyncChangeEntry = z.infer<typeof syncChangeEntrySchema>;

export const syncWeekToFixedSummarySchema = z.object({
  pfv_inserted: z.number().int().min(0),
  pfv_updated: z.number().int().min(0),
  pfv_unchanged: z.number().int().min(0),
  /** Wave Next 1 H2: multi-staff pair などで触らなかった weekday の件数. */
  pfv_skipped: z.number().int().min(0).default(0),
});
export type SyncWeekToFixedSummary = z.infer<typeof syncWeekToFixedSummarySchema>;

export const syncWeekToFixedResponseSchema = z.object({
  patient_id: z.string().uuid(),
  summary: syncWeekToFixedSummarySchema,
  changes: z.array(syncChangeEntrySchema),
  /**
   * Wave Next 1 M3: 今週 visit が無い既存 PFV (zombie 候補) のスナップショット.
   * FE 側で「今週の visit に対応が無い固定枠」を可視化したい用途を想定.
   */
  untouched_existing: z.array(syncPfvSnapshotSchema).default([]),
  /** True なら DB に commit 済み. False なら dry_run (diff のみ). */
  transaction_applied: z.boolean(),
});
export type SyncWeekToFixedResponse = z.infer<typeof syncWeekToFixedResponseSchema>;
