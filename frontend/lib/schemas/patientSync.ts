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

// ---------------------------------------------------------------------------
// Bulk endpoints (FullOptimizeDialog 一括 固定時間変更 用)
// ---------------------------------------------------------------------------

/** POST /api/v1/patients/bulk-sync-week-to-fixed のリクエスト. */
export const bulkSyncWeekToFixedRequestSchema = z.object({
  patient_ids: z.array(z.string().uuid()).min(1).max(500),
  iso_year: z.number().int().min(2000).max(2100),
  iso_week: z.number().int().min(1).max(53),
  dry_run: z.boolean().default(true),
});
export type BulkSyncWeekToFixedRequest = z.infer<typeof bulkSyncWeekToFixedRequestSchema>;

/** 1 patient 分の bulk sync 結果. */
export const bulkSyncWeekToFixedItemSchema = z.object({
  patient_id: z.string().uuid(),
  summary: syncWeekToFixedSummarySchema,
  changes: z.array(syncChangeEntrySchema).default([]),
  untouched_existing: z.array(syncPfvSnapshotSchema).default([]),
  transaction_applied: z.boolean(),
  /** 個別 patient の失敗理由 (404 など). 成功時は null. */
  error: z.string().nullable().optional(),
});
export type BulkSyncWeekToFixedItem = z.infer<typeof bulkSyncWeekToFixedItemSchema>;

/** POST /api/v1/patients/bulk-sync-week-to-fixed のレスポンス. */
export const bulkSyncWeekToFixedResponseSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  results: z.array(bulkSyncWeekToFixedItemSchema).default([]),
  total_inserted: z.number().int().min(0).default(0),
  total_updated: z.number().int().min(0).default(0),
  total_unchanged: z.number().int().min(0).default(0),
  total_skipped: z.number().int().min(0).default(0),
  errors: z.array(z.string()).default([]),
  transaction_applied: z.boolean(),
});
export type BulkSyncWeekToFixedResponse = z.infer<typeof bulkSyncWeekToFixedResponseSchema>;

/** 1 件の (patient, weekday) に対する週限定変更. */
export const bulkWeekOnlyPatientVisitChangeSchema = z.object({
  patient_id: z.string().uuid(),
  weekday: weekdaySchema,
  start_time: timeStringSchema,
  duration_min: z.number().int().min(1).max(480),
  course_template_id: z.string().uuid().nullable().optional(),
});
export type BulkWeekOnlyPatientVisitChange = z.infer<typeof bulkWeekOnlyPatientVisitChangeSchema>;

/** POST /api/v1/patients/bulk-apply-week-only-visit-changes のリクエスト. */
export const bulkApplyWeekOnlyVisitChangesRequestSchema = z.object({
  patient_visit_changes: z.array(bulkWeekOnlyPatientVisitChangeSchema).min(1).max(1000),
  iso_year: z.number().int().min(2000).max(2100),
  iso_week: z.number().int().min(1).max(53),
  dry_run: z.boolean().default(true),
});
export type BulkApplyWeekOnlyVisitChangesRequest = z.infer<
  typeof bulkApplyWeekOnlyVisitChangesRequestSchema
>;

export const BULK_WEEK_ONLY_OPERATIONS = ['insert', 'update', 'unchanged', 'skipped'] as const;
export type BulkWeekOnlyOperation = (typeof BULK_WEEK_ONLY_OPERATIONS)[number];

/** 1 件の (patient, weekday) 適用結果. */
export const bulkWeekOnlyChangeOutcomeSchema = z.object({
  patient_id: z.string().uuid(),
  weekday: weekdaySchema,
  operation: z.enum(BULK_WEEK_ONLY_OPERATIONS),
  old_start_time: timeStringSchema.nullable().optional(),
  new_start_time: timeStringSchema.nullable().optional(),
  old_duration_min: z.number().int().nullable().optional(),
  new_duration_min: z.number().int().nullable().optional(),
  visit_id: z.string().uuid().nullable().optional(),
  reason: z.string().nullable().optional(),
});
export type BulkWeekOnlyChangeOutcome = z.infer<typeof bulkWeekOnlyChangeOutcomeSchema>;

/** POST /api/v1/patients/bulk-apply-week-only-visit-changes のレスポンス. */
export const bulkApplyWeekOnlyVisitChangesResponseSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  outcomes: z.array(bulkWeekOnlyChangeOutcomeSchema).default([]),
  total_inserted: z.number().int().min(0).default(0),
  total_updated: z.number().int().min(0).default(0),
  total_unchanged: z.number().int().min(0).default(0),
  total_skipped: z.number().int().min(0).default(0),
  errors: z.array(z.string()).default([]),
  transaction_applied: z.boolean(),
});
export type BulkApplyWeekOnlyVisitChangesResponse = z.infer<
  typeof bulkApplyWeekOnlyVisitChangesResponseSchema
>;
