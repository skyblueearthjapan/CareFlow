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
  week_start: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/, 'YYYY-MM-DD で入力してください'),
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
