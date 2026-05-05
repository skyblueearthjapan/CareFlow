/**
 * TanStack Query hook for `/api/v1/audit-logs` (Wave 4-F).
 *
 * Read-only — admin-only on the server. The page-level guard happens in
 * `frontend/app/(app)/admin/audit-logs/page.tsx`.
 */
'use client';

import { useQuery } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import type { AuditLogPage } from '@/lib/schemas/audit-log';

export interface UseAuditLogsParams {
  since?: string;
  until?: string;
  userId?: string;
  role?: string;
  path?: string;
  method?: string;
  statusCode?: number;
  limit?: number;
  offset?: number;
}

export function useAuditLogs(params: UseAuditLogsParams = {}) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const role = session?.user?.role;

  const {
    since,
    until,
    userId,
    role: actorRole,
    path,
    method,
    statusCode,
    limit = 100,
    offset = 0,
  } = params;

  return useQuery<AuditLogPage>({
    queryKey: [
      'audit-logs',
      'list',
      { since, until, userId, actorRole, path, method, statusCode, limit, offset },
    ],
    enabled: status === 'authenticated' && role === 'admin',
    queryFn: () => {
      const usp = new URLSearchParams({
        limit: String(limit),
        offset: String(offset),
      });
      if (since) usp.set('since', since);
      if (until) usp.set('until', until);
      if (userId) usp.set('user_id', userId);
      if (actorRole) usp.set('role', actorRole);
      if (path) usp.set('path', path);
      if (method) usp.set('method', method);
      if (statusCode !== undefined) usp.set('status_code', String(statusCode));
      return fetcher<AuditLogPage>(`/api/v1/audit-logs?${usp.toString()}`, {
        accessToken,
        refreshToken,
      });
    },
  });
}
