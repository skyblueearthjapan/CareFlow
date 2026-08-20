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
  // 患者性別 (male/female/unknown)。本体スケジュールと同じ性別ウォッシュ意匠で
  // 週ビューのカードを塗る。BE 加算フィールド (nullish で後方互換)。
  patientSex: z.string().nullish(),
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

// --- Inbound sync (カイポケ → CareFlow) ------------------------------------

export const InboundEligibilitySchema = z.object({
  weekStart: z.string(),
  eligible: z.boolean(),
  lastAppliedAt: z.string().nullable(),
});
export type InboundEligibility = z.infer<typeof InboundEligibilitySchema>;

// 取り込み前スナップショット (PO 決定 2026-08-09: 「取り込み前に戻す」)
export const InboundSnapshotSchema = z.object({
  id: z.string().uuid(),
  weekStart: z.string(),
  kind: z.string(),
  visitsCount: z.number().int(),
  createdAt: z.string(),
});
export type InboundSnapshot = z.infer<typeof InboundSnapshotSchema>;

export const InboundSnapshotListSchema = z.object({
  snapshots: z.array(InboundSnapshotSchema),
});
export type InboundSnapshotList = z.infer<typeof InboundSnapshotListSchema>;

export const SnapshotRestoreResultSchema = z.object({
  wiped: z.number().int(),
  restored: z.number().int(),
  coursesRestored: z.number().int(),
  coursesRemoved: z.number().int(),
});
export type SnapshotRestoreResult = z.infer<typeof SnapshotRestoreResultSchema>;

export const DiffInboundRequestSchema = z.object({
  month: z.string().regex(/^\d{4}-\d{2}$/, 'YYYY-MM で入力してください'),
  weekStart: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, 'YYYY-MM-DD で入力してください'),
});
export type DiffInboundRequest = z.infer<typeof DiffInboundRequestSchema>;

// BE は diff-local と同じ DiffAccepted レスポンスを返すため型を共有する。
export const DiffInboundAcceptedSchema = DiffAcceptedSchema;
export type DiffInboundAccepted = DiffAccepted;

export const ApplyInboundRequestSchema = z.object({
  sheetId: z.string().uuid(),
  dryRun: z.boolean().optional(),
  days: z.array(z.string().regex(/^\d{4}-\d{2}-\d{2}$/)).optional(),
});
export type ApplyInboundRequest = z.infer<typeof ApplyInboundRequestSchema>;

export const APPLY_INBOUND_OUTCOMES = [
  'cancelled',
  'updated',
  'added',
  'skipped',
  'failed',
] as const;

export const ApplyInboundResultItemSchema = z.object({
  itemId: z.string().uuid(),
  action: z.string(),
  outcome: z.enum(APPLY_INBOUND_OUTCOMES),
  detail: z.string().nullable().optional(),
  patientName: z.string().nullable().optional(),
  date: z.string().nullable().optional(),
});
export type ApplyInboundResultItem = z.infer<typeof ApplyInboundResultItemSchema>;

// ⛔ NG スタッフ衝突 1 件 (patient-ng-staff-design.md §9 Phase 3)。
// dry-run のみ BE が集計して返す (実適用時は常に空)。取込はブロックしない (カイポケが正)。
export const NgConflictSchema = z.object({
  patientId: z.string(),
  patientName: z.string().default(''),
  staffId: z.string(),
  staffName: z.string().default(''),
  date: z.string().default(''),
  weekday: z.number().int().nullable().optional(),
  courseCode: z.string().nullable().optional(),
});
export type NgConflict = z.infer<typeof NgConflictSchema>;

export const ApplyInboundResultSchema = z.object({
  jobId: z.string().uuid().nullable(),
  dryRun: z.boolean(),
  cancelled: z.number().int().default(0),
  updated: z.number().int().default(0),
  added: z.number().int().default(0),
  skipped: z.number().int().default(0),
  failed: z.number().int().default(0),
  results: z.array(ApplyInboundResultItemSchema).default([]),
  // ⛔ NG スタッフ衝突 (dry-run のみ・旧 BE は返さないため default [])
  ngConflicts: z.array(NgConflictSchema).default([]),
});
export type ApplyInboundResult = z.infer<typeof ApplyInboundResultSchema>;

// --- Credentials (カイポケ接続設定・admin専用) ---------------------------------

export const KaipokeCredentialsSchema = z.object({
  configured: z.boolean(),
  corpId: z.string().nullable(),
  userId: z.string().nullable(),
  updatedAt: z.string().nullable(),
});
export type KaipokeCredentials = z.infer<typeof KaipokeCredentialsSchema>;

export const SaveKaipokeCredentialsBodySchema = z.object({
  corpId: z.string().min(1, '法人IDを入力してください'),
  userId: z.string().min(1, 'ユーザーIDを入力してください'),
  password: z.string().min(1, 'パスワードを入力してください'),
});
export type SaveKaipokeCredentialsBody = z.infer<typeof SaveKaipokeCredentialsBodySchema>;

export const TestKaipokeCredentialsResultSchema = z.object({
  ok: z.boolean(),
  message: z.string(),
});
export type TestKaipokeCredentialsResult = z.infer<typeof TestKaipokeCredentialsResultSchema>;

// --- イベント取り込み (個別業務・kaipoke-event-inbound-design.md E-2) -------

export const EVENTS_INBOUND_ACTIONS = ['add', 'update', 'delete'] as const;

export const EventsInboundChangeSchema = z.object({
  action: z.enum(EVENTS_INBOUND_ACTIONS),
  externalId: z.string(),
  staffId: z.string().uuid(),
  staffName: z.string().default(''),
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  start: z.string().regex(/^\d{2}:\d{2}$/),
  end: z.string().regex(/^\d{2}:\d{2}$/),
  title: z.string().default(''),
  isMemo: z.boolean().default(false),
  beforeStart: z.string().nullable().optional(),
  beforeEnd: z.string().nullable().optional(),
  beforeTitle: z.string().nullable().optional(),
});
export type EventsInboundChange = z.infer<typeof EventsInboundChangeSchema>;

export const EventsInboundUnmatchedSchema = z.object({
  staffName: z.string(),
  count: z.number().int(),
});
export type EventsInboundUnmatched = z.infer<typeof EventsInboundUnmatchedSchema>;

/** 取込イベント × 既存訪問の時間重なり (案A・隠さず警告)。 */
export const EventsInboundConflictSchema = z.object({
  staffName: z.string(),
  date: z.string(),
  eventTitle: z.string(),
  eventStart: z.string(),
  eventEnd: z.string(),
  patientName: z.string(),
  visitStart: z.string(),
  visitEnd: z.string(),
});
export type EventsInboundConflict = z.infer<typeof EventsInboundConflictSchema>;

export const EventsInboundPreviewSchema = z.object({
  weekStart: z.string(),
  weekEnd: z.string(),
  fetchedTotal: z.number().int().default(0),
  sundaySkipped: z.number().int().default(0),
  memoCount: z.number().int().default(0),
  adds: z.number().int().default(0),
  updates: z.number().int().default(0),
  deletes: z.number().int().default(0),
  changes: z.array(EventsInboundChangeSchema).default([]),
  unmatched: z.array(EventsInboundUnmatchedSchema).default([]),
  conflicts: z.array(EventsInboundConflictSchema).default([]),
});
export type EventsInboundPreview = z.infer<typeof EventsInboundPreviewSchema>;

export const EventsInboundPreviewRequestSchema = z.object({
  weekStart: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
});
export type EventsInboundPreviewRequest = z.infer<typeof EventsInboundPreviewRequestSchema>;

/** 非同期プレビュー起動 (202) の応答。jobId で status をポーリングする。 */
export const EventsInboundStartSchema = z.object({
  jobId: z.string(),
  status: z.literal('running').default('running'),
});
export type EventsInboundStart = z.infer<typeof EventsInboundStartSchema>;

/** 非同期プレビューの進行状況。completed のとき preview が入る。 */
export const EventsInboundStatusSchema = z.object({
  status: z.enum(['running', 'completed', 'failed']),
  error: z.string().nullable().optional(),
  preview: EventsInboundPreviewSchema.nullable().optional(),
});
export type EventsInboundStatus = z.infer<typeof EventsInboundStatusSchema>;

/** イベント送信 (outbound・らく助→カイポケ・Phase 3) — プレビュー1行 */
export const EventsOutboundItemSchema = z.object({
  eventId: z.string().uuid(),
  staffId: z.string().uuid(),
  staffName: z.string(),
  date: z.string(),
  start: z.string(),
  end: z.string(),
  title: z.string(),
  isMemo: z.boolean().default(false),
  sendable: z.boolean(),
  reason: z.string().nullable().optional(),
});
export type EventsOutboundItem = z.infer<typeof EventsOutboundItemSchema>;

export const EventsOutboundPreviewSchema = z.object({
  weekStart: z.string(),
  weekEnd: z.string(),
  items: z.array(EventsOutboundItemSchema).default([]),
  sendableCount: z.number().int().default(0),
});
export type EventsOutboundPreview = z.infer<typeof EventsOutboundPreviewSchema>;

export const EventsOutboundStartSchema = z.object({
  jobId: z.string(),
  status: z.literal('running').default('running'),
  count: z.number().int(),
});
export type EventsOutboundStart = z.infer<typeof EventsOutboundStartSchema>;

export const EventsOutboundStatusSchema = z.object({
  status: z.enum(['running', 'completed', 'failed']),
  error: z.string().nullable().optional(),
  summary: z.record(z.unknown()).nullable().optional(),
  results: z.array(z.record(z.unknown())).nullable().optional(),
});
export type EventsOutboundStatus = z.infer<typeof EventsOutboundStatusSchema>;

export const EventsInboundApplyRequestSchema = z.object({
  weekStart: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  dryRun: z.boolean().optional(),
  changes: z.array(EventsInboundChangeSchema),
});
export type EventsInboundApplyRequest = z.infer<typeof EventsInboundApplyRequestSchema>;

export const EVENTS_INBOUND_OUTCOMES = [
  'added',
  'updated',
  'deleted',
  'skipped',
  'failed',
] as const;

export const EventsInboundApplyItemSchema = z.object({
  action: z.string(),
  externalId: z.string(),
  staffName: z.string().default(''),
  date: z.string().default(''),
  title: z.string().default(''),
  outcome: z.enum(EVENTS_INBOUND_OUTCOMES),
  detail: z.string().nullable().optional(),
});
export type EventsInboundApplyItem = z.infer<typeof EventsInboundApplyItemSchema>;

export const EventsInboundApplyResultSchema = z.object({
  jobId: z.string().uuid().nullable(),
  dryRun: z.boolean(),
  added: z.number().int().default(0),
  updated: z.number().int().default(0),
  deleted: z.number().int().default(0),
  skipped: z.number().int().default(0),
  failed: z.number().int().default(0),
  results: z.array(EventsInboundApplyItemSchema).default([]),
  conflicts: z.array(EventsInboundConflictSchema).default([]),
});
export type EventsInboundApplyResult = z.infer<typeof EventsInboundApplyResultSchema>;

// --- 置換取り込み (週白紙化→カイポケ全挿入・2026-07-26 PO確定) --------------

export const ReplaceInboundRequestSchema = z.object({
  weekStart: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  dryRun: z.boolean().optional(),
});
export type ReplaceInboundRequest = z.infer<typeof ReplaceInboundRequestSchema>;

export const ReplaceInboundSkipSchema = z.object({
  reason: z.string(),
  userName: z.string().default(''),
  staffName: z.string().default(''),
  date: z.string().default(''),
  start: z.string().default(''),
});
export type ReplaceInboundSkip = z.infer<typeof ReplaceInboundSkipSchema>;

export const ReplaceInboundTraineeSoloSchema = z.object({
  staffName: z.string(),
  count: z.number().int(),
});
export type ReplaceInboundTraineeSolo = z.infer<typeof ReplaceInboundTraineeSoloSchema>;

export const ReplaceInboundResultSchema = z.object({
  jobId: z.string().uuid().nullable(),
  weekStart: z.string(),
  weekEnd: z.string(),
  dryRun: z.boolean(),
  wiped: z.number().int().default(0),
  inserted: z.number().int().default(0),
  sundaySkipped: z.number().int().default(0),
  tempCourses: z.number().int().default(0),
  // コース担当をカイポケの現実へ付け替えた数 (臨時コース乱立の根治・2026-07-26)
  coursesReassigned: z.number().int().default(0),
  // 未使用テンプレートから新設したコース行の数 (例: 稲毛E)
  coursesCreated: z.number().int().default(0),
  skipped: z.array(ReplaceInboundSkipSchema).default([]),
  // ⚠新人の単独訪問 (取り込み済み・新人フラグ見直しの判断材料)
  traineeSolo: z.array(ReplaceInboundTraineeSoloSchema).default([]),
  // ⛔ NG スタッフ衝突 (dry-run のみ・旧 BE は返さないため default [])
  ngConflicts: z.array(NgConflictSchema).default([]),
});
export type ReplaceInboundResult = z.infer<typeof ReplaceInboundResultSchema>;

// --- smart-inbound (日単位ハイブリッド自動判別・2026-07-26 PO確定) -----------

export const SmartInboundPreviewSchema = z.object({
  weekStart: z.string(),
  weekEnd: z.string(),
  /** 打刻実績のある日 (差分モード担当・行を残して直す) */
  protectedDays: z.array(z.string()).default([]),
  /** 打刻の無い日 (置換モード担当・白紙化して書き直す) */
  replaceDays: z.array(z.string()).default([]),
  sheetId: z.string().uuid().nullable(),
  diffSummary: z.record(z.string(), z.number()).default({}),
  replace: ReplaceInboundResultSchema.nullable(),
});
export type SmartInboundPreview = z.infer<typeof SmartInboundPreviewSchema>;

export const SmartInboundApplyRequestSchema = z.object({
  weekStart: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  sheetId: z.string().uuid().nullable(),
  dryRun: z.boolean().optional(),
});
export type SmartInboundApplyRequest = z.infer<typeof SmartInboundApplyRequestSchema>;

export const SmartInboundApplyResultSchema = z.object({
  weekStart: z.string(),
  protectedDays: z.array(z.string()).default([]),
  replaceDays: z.array(z.string()).default([]),
  dryRun: z.boolean(),
  diff: ApplyInboundResultSchema.nullable(),
  replace: ReplaceInboundResultSchema.nullable(),
});
export type SmartInboundApplyResult = z.infer<typeof SmartInboundApplyResultSchema>;
