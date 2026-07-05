/**
 * Integrations (連携センター) zod schemas — Phase 5-1 Wave 2-B.
 *
 * Mirrors backend `app/schemas/integrations.py`. Wave scope is schema + UI
 * only; the actual Playwright execution (fetch/push) lands in Phase 5-2.
 */
import { z } from 'zod';

const numericLike = z
  .union([z.number(), z.string()])
  .transform((v) => (typeof v === 'string' ? Number(v) : v))
  .pipe(z.number().finite());

export const KAIPOKE_JOB_TYPES = ['fetch', 'push'] as const;
export const KAIPOKE_JOB_STATUSES = [
  'pending',
  'running',
  'completed',
  'failed',
  'cancelled',
] as const;
export const KAIPOKE_JOB_ITEM_STATUSES = [
  'pending',
  'running',
  'completed',
  'failed',
  'skipped',
] as const;

export const KaipokeJobTypeSchema = z.enum(KAIPOKE_JOB_TYPES);
export const KaipokeJobStatusSchema = z.enum(KAIPOKE_JOB_STATUSES);
export const KaipokeJobItemStatusSchema = z.enum(KAIPOKE_JOB_ITEM_STATUSES);

export type KaipokeJobType = z.infer<typeof KaipokeJobTypeSchema>;
export type KaipokeJobStatus = z.infer<typeof KaipokeJobStatusSchema>;
export type KaipokeJobItemStatus = z.infer<typeof KaipokeJobItemStatusSchema>;

export const KaipokeJobItemReadSchema = z.object({
  id: z.string().uuid(),
  job_id: z.string().uuid(),
  seq: z.number().int(),
  status: KaipokeJobItemStatusSchema,
  content: z.record(z.unknown()).default({}),
  error_msg: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const KaipokeJobReadSchema = z.object({
  id: z.string().uuid(),
  job_type: KaipokeJobTypeSchema,
  status: KaipokeJobStatusSchema,
  week_start: z.string(),
  params: z.record(z.unknown()).default({}),
  result_summary: z.record(z.unknown()).nullable().optional(),
  started_at: z.string().nullable().optional(),
  completed_at: z.string().nullable().optional(),
  created_by_user_id: z.string().uuid().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
  items: z.array(KaipokeJobItemReadSchema).default([]),
});

export const KaipokeJobCreateSchema = z.object({
  job_type: KaipokeJobTypeSchema,
  week_start: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, 'YYYY-MM-DD で入力してください'),
  params: z.record(z.unknown()).default({}),
});

export interface Paginated<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type KaipokeJob = z.infer<typeof KaipokeJobReadSchema>;
export type KaipokeJobItem = z.infer<typeof KaipokeJobItemReadSchema>;
export type KaipokeJobCreate = z.infer<typeof KaipokeJobCreateSchema>;

export const GeocodingCacheReadSchema = z.object({
  id: z.string().uuid(),
  address_hash: z.string(),
  address: z.string(),
  lat: numericLike,
  lng: numericLike,
  provider: z.string(),
  looked_up_at: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type GeocodingCache = z.infer<typeof GeocodingCacheReadSchema>;

export const AiInterpretLogReadSchema = z.object({
  id: z.string().uuid(),
  prompt: z.string(),
  response: z.record(z.unknown()).default({}),
  model: z.string(),
  latency_ms: z.number().int(),
  user_id: z.string().uuid().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type AiInterpretLog = z.infer<typeof AiInterpretLogReadSchema>;

// --- Wave 4-A: kaipoke status + relay --------------------------------------

export const KaipokeStatusSchema = z.object({
  kaipoke: z.record(z.unknown()).default({}),
  loginRemainSec: z.number().int().nullable().optional(),
  lastSyncAt: z.string().nullable().optional(),
  runningJob: KaipokeJobReadSchema.nullable().optional(),
  reachable: z.boolean().default(true),
  error: z.string().nullable().optional(),
});
export type KaipokeStatus = z.infer<typeof KaipokeStatusSchema>;

export const LiveSnapshotSchema = z.object({
  reachable: z.boolean().default(true),
  running: z.boolean().default(false),
  command: z.string().nullable().optional(),
  phase: z.string().nullable().optional(),
  processed: z.number().int().nullable().optional(),
  total: z.number().int().nullable().optional(),
  currentName: z.string().nullable().optional(),
  success: z.number().int().nullable().optional(),
  failed: z.number().int().nullable().optional(),
  skipped: z.number().int().nullable().optional(),
  logs: z.array(z.string()).default([]),
  monitorUrl: z.string().nullable().optional(),
  latestJob: KaipokeJobReadSchema.nullable().optional(),
  error: z.string().nullable().optional(),
});
export type LiveSnapshot = z.infer<typeof LiveSnapshotSchema>;

export const JobAcceptedSchema = z.object({
  jobId: z.string().uuid(),
  kaipokeJobId: z.string().nullable().optional(),
  status: KaipokeJobStatusSchema,
});
export type JobAccepted = z.infer<typeof JobAcceptedSchema>;

export const DiffAcceptedSchema = z.object({
  jobId: z.string().uuid(),
  sheetId: z.string().uuid(),
  summary: z.record(z.number().int()).default({}),
});
export type DiffAccepted = z.infer<typeof DiffAcceptedSchema>;

export const ExpandRequestSchema = z.object({
  month: z.string().regex(/^\d{4}-\d{2}$/, 'YYYY-MM で入力してください'),
  dryRun: z.boolean().optional(),
});
export type ExpandRequest = z.infer<typeof ExpandRequestSchema>;

export const ExportRequestSchema = z.object({
  month: z.string().regex(/^\d{4}-\d{2}$/, 'YYYY-MM で入力してください'),
  format: z.enum(['csv', 'xlsx']).default('csv'),
});
export type ExportRequest = z.infer<typeof ExportRequestSchema>;

export const DiffRequestSchema = z.object({
  month: z.string().regex(/^\d{4}-\d{2}$/, 'YYYY-MM で入力してください'),
});
export type DiffRequest = z.infer<typeof DiffRequestSchema>;

// K-2b/2c: CareFlow 内ローカル差分 (週スコープ対応)。
export const DiffLocalRequestSchema = z.object({
  month: z.string().regex(/^\d{4}-\d{2}$/, 'YYYY-MM で入力してください'),
  officeId: z.string().uuid().optional(),
  weekStart: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .optional(),
  weekEnd: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .optional(),
});
export type DiffLocalRequest = z.infer<typeof DiffLocalRequestSchema>;

export const ApplyRequestSchema = z.object({
  sheetId: z.string().uuid(),
  dryRun: z.boolean().optional(),
});
export type ApplyRequest = z.infer<typeof ApplyRequestSchema>;

// K-2 UI: 週スケジュール表示 (CareFlow 確定 visits 由来・コース別)。
export const WeekScheduleRowSchema = z.object({
  visitDate: z.string(),
  weekday: z.number().int().default(0), // 0=月..6=日
  startTime: z.string(),
  endTime: z.string(),
  patientName: z.string(),
  staff1: z.string().default(''),
  staff2: z.string().default(''),
  courseCode: z.string().default(''),
  officeName: z.string().default(''),
});
export type WeekScheduleRow = z.infer<typeof WeekScheduleRowSchema>;

export const WeekScheduleSchema = z.object({
  weekStart: z.string(),
  weekEnd: z.string(),
  rows: z.array(WeekScheduleRowSchema).default([]),
});
export type WeekSchedule = z.infer<typeof WeekScheduleSchema>;

export const ExpandStatusSchema = z.object({
  month: z.string(),
  expanded: z.boolean(),
  expandedAt: z.string().nullable().optional(),
  jobId: z.string().uuid().nullable().optional(),
});
export type ExpandStatus = z.infer<typeof ExpandStatusSchema>;

// --- Wave 4-A: correction sheets / items (Phase C) -------------------------

export const CORRECTION_ACTIONS = ['add', 'delete', 'update', 'companion_change'] as const;

export const CorrectionItemReadSchema = z.object({
  id: z.string().uuid(),
  sheet_id: z.string().uuid(),
  patient_id: z.string().uuid().nullable().optional(),
  visit_id: z.string().uuid().nullable().optional(),
  action: z.string(),
  before: z.record(z.unknown()).nullable().optional(),
  after: z.record(z.unknown()).nullable().optional(),
  include: z.boolean(),
  comment: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type CorrectionItem = z.infer<typeof CorrectionItemReadSchema>;

export const CorrectionSheetReadSchema = z.object({
  id: z.string().uuid(),
  target_month: z.string(),
  status: z.string(),
  created_by_user_id: z.string().uuid().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
  items: z.array(CorrectionItemReadSchema).default([]),
});
export type CorrectionSheet = z.infer<typeof CorrectionSheetReadSchema>;

export const CorrectionItemUpdateSchema = z.object({
  include: z.boolean().optional(),
  comment: z.string().nullable().optional(),
});
export type CorrectionItemUpdate = z.infer<typeof CorrectionItemUpdateSchema>;

export const CorrectionBulkSelectSchema = z.object({
  ids: z.array(z.string().uuid()),
  patch: CorrectionItemUpdateSchema,
});
export type CorrectionBulkSelect = z.infer<typeof CorrectionBulkSelectSchema>;
