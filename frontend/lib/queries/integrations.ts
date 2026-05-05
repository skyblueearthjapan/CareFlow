/**
 * TanStack Query hooks for /api/v1/integrations/* — Phase 5-1 Wave 2-B.
 *
 * Covers Kaipoke jobs (list/detail/create/cancel), geocoding cache (admin),
 * and AI interpret logs (admin). Mirrors backend `api/v1/integrations.py`.
 */
'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import type {
  AiInterpretLog,
  GeocodingCache,
  KaipokeJob,
  KaipokeJobCreate,
} from '@/lib/schemas/integration';

// --- Kaipoke jobs ---------------------------------------------------------

export interface UseKaipokeJobsParams {
  weekStart?: string;
  status?: string;
  type?: string;
  limit?: number;
  offset?: number;
}

export function useKaipokeJobs(params: UseKaipokeJobsParams = {}) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  const { weekStart, status: jobStatus, type, limit = 50, offset = 0 } = params;

  return useQuery<KaipokeJob[]>({
    queryKey: ['integrations', 'kaipoke', 'jobs', weekStart ?? null, jobStatus ?? null, type ?? null, limit, offset],
    queryFn: () => {
      const usp = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      if (weekStart) usp.set('week_start', weekStart);
      if (jobStatus) usp.set('status', jobStatus);
      if (type) usp.set('type', type);
      return fetcher<KaipokeJob[]>(`/api/v1/integrations/kaipoke/jobs?${usp.toString()}`, {
        accessToken,
        refreshToken,
      });
    },
    enabled: status === 'authenticated',
  });
}

export function useKaipokeJob(id: string | undefined) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useQuery<KaipokeJob>({
    queryKey: ['integrations', 'kaipoke', 'jobs', 'detail', id],
    queryFn: () =>
      fetcher<KaipokeJob>(`/api/v1/integrations/kaipoke/jobs/${id}`, {
        accessToken,
        refreshToken,
      }),
    enabled: status === 'authenticated' && Boolean(id),
  });
}

export function useCreateKaipokeJob() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  return useMutation<KaipokeJob, Error, KaipokeJobCreate>({
    mutationFn: (payload) =>
      fetcher<KaipokeJob>('/api/v1/integrations/kaipoke/jobs', {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['integrations', 'kaipoke', 'jobs'] });
    },
  });
}

export function useCancelKaipokeJob() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  return useMutation<KaipokeJob, Error, string>({
    mutationFn: (id) =>
      fetcher<KaipokeJob>(`/api/v1/integrations/kaipoke/jobs/${id}/cancel`, {
        method: 'POST',
        accessToken,
        refreshToken,
      }),
    onSuccess: (_data, id) => {
      void qc.invalidateQueries({ queryKey: ['integrations', 'kaipoke', 'jobs'] });
      void qc.invalidateQueries({ queryKey: ['integrations', 'kaipoke', 'jobs', 'detail', id] });
    },
  });
}

// --- Geocoding cache ------------------------------------------------------

export interface UseGeocodingCacheParams {
  q?: string;
  limit?: number;
  offset?: number;
}

export function useGeocodingCache(params: UseGeocodingCacheParams = {}) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  const { q, limit = 100, offset = 0 } = params;

  return useQuery<GeocodingCache[]>({
    queryKey: ['integrations', 'geocoding', 'cache', q ?? null, limit, offset],
    queryFn: () => {
      const usp = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      if (q) usp.set('q', q);
      return fetcher<GeocodingCache[]>(
        `/api/v1/integrations/geocoding/cache?${usp.toString()}`,
        { accessToken, refreshToken },
      );
    },
    enabled: status === 'authenticated',
  });
}

// --- AI interpret logs ----------------------------------------------------

export interface UseAiInterpretLogsParams {
  since?: string;
  until?: string;
  model?: string;
  limit?: number;
  offset?: number;
}

export function useAiInterpretLogs(params: UseAiInterpretLogsParams = {}) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  const { since, until, model, limit = 100, offset = 0 } = params;

  return useQuery<AiInterpretLog[]>({
    queryKey: ['integrations', 'ai', 'logs', since ?? null, until ?? null, model ?? null, limit, offset],
    queryFn: () => {
      const usp = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      if (since) usp.set('since', since);
      if (until) usp.set('until', until);
      if (model) usp.set('model', model);
      return fetcher<AiInterpretLog[]>(
        `/api/v1/integrations/ai/logs?${usp.toString()}`,
        { accessToken, refreshToken },
      );
    },
    enabled: status === 'authenticated',
  });
}
