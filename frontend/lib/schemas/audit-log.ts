/**
 * Audit-log zod schemas — mirrors `backend/app/schemas/audit_log.py`.
 *
 * Only a read schema is exported; the audit table is server-managed.
 */
import { z } from 'zod';

export const auditLogReadSchema = z.object({
  id: z.number().int(),
  actor_user_id: z.string().uuid().nullable().optional(),
  role: z.string().nullable().optional(),
  action: z.string(),
  target_table: z.string(),
  target_id: z.string(),
  method: z.string().nullable().optional(),
  path: z.string().nullable().optional(),
  status_code: z.number().int().nullable().optional(),
  latency_ms: z.number().int().nullable().optional(),
  ip_address: z.string().nullable().optional(),
  user_agent: z.string().nullable().optional(),
  request_body: z.record(z.unknown()).nullable().optional(),
  before: z.record(z.unknown()).nullable().optional(),
  after: z.record(z.unknown()).nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type AuditLogRead = z.infer<typeof auditLogReadSchema>;

export interface AuditLogPage {
  items: AuditLogRead[];
  total: number;
  limit: number;
  offset: number;
}
