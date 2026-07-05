/**
 * TanStack Query hooks for /api/v1/integrations/* — Phase 5-1 Wave 2-B + 4-A.
 *
 * Covers Kaipoke jobs (list/detail/create/cancel), geocoding cache (admin),
 * AI interpret logs (admin), kaipoke status + relay (expand/export/diff/apply),
 * and correction-sheet editing. Mirrors backend `api/v1/integrations.py`.
 */
'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import type {
  AiInterpretLog,
  ApplyRequest,
  CorrectionItem,
  CorrectionItemUpdate,
  CorrectionSheet,
  DiffAccepted,
  DiffLocalRequest,
  DiffRequest,
  ExpandRequest,
  ExportRequest,
  GeocodingCache,
  JobAccepted,
  KaipokeJob,
  KaipokeJobCreate,
  KaipokeStatus,
  LiveSnapshot,
  Paginated,
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

  return useQuery<Paginated<KaipokeJob>>({
    queryKey: [
      'integrations',
      'kaipoke',
      'jobs',
      weekStart ?? null,
      jobStatus ?? null,
      type ?? null,
      limit,
      offset,
    ],
    queryFn: () => {
      const usp = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      if (weekStart) usp.set('week_start', weekStart);
      if (jobStatus) usp.set('status', jobStatus);
      if (type) usp.set('type', type);
      return fetcher<Paginated<KaipokeJob>>(`/api/v1/integrations/kaipoke/jobs?${usp.toString()}`, {
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

  return useQuery<Paginated<GeocodingCache>>({
    queryKey: ['integrations', 'geocoding', 'cache', q ?? null, limit, offset],
    queryFn: () => {
      const usp = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      if (q) usp.set('q', q);
      return fetcher<Paginated<GeocodingCache>>(
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

// --- Wave 4-A: kaipoke status + relay -------------------------------------

export function useKaipokeStatus(refetchMs = 60_000) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useQuery<KaipokeStatus>({
    queryKey: ['integrations', 'status'],
    queryFn: () =>
      fetcher<KaipokeStatus>('/api/v1/integrations/status', {
        accessToken,
        refreshToken,
      }),
    enabled: status === 'authenticated' && session?.user?.role === 'admin',
    refetchInterval: refetchMs,
  });
}

/**
 * Live single-slot worker snapshot for the monitor UI. Polls adaptively:
 * fast (2s) while a job is running, relaxed (15s) when idle — so the progress
 * panel feels live during execution without hammering the relay at rest.
 */
export function useKaipokeLive(enabled = true) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useQuery<LiveSnapshot>({
    queryKey: ['integrations', 'live'],
    queryFn: () =>
      fetcher<LiveSnapshot>('/api/v1/integrations/live?tail=60', {
        accessToken,
        refreshToken,
      }),
    enabled: enabled && status === 'authenticated' && session?.user?.role === 'admin',
    refetchInterval: (query) => (query.state.data?.running ? 2_000 : 15_000),
  });
}

function useRelayMutation<TReq, TRes>(path: string) {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  return useMutation<TRes, Error, TReq>({
    mutationFn: (payload) =>
      fetcher<TRes>(`/api/v1/integrations/${path}`, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['integrations', 'kaipoke', 'jobs'] });
      void qc.invalidateQueries({ queryKey: ['integrations', 'status'] });
      void qc.invalidateQueries({ queryKey: ['integrations', 'jobs'] });
      // Refresh the live snapshot immediately so the monitor reflects the new
      // job without waiting out the 15s idle poll interval.
      void qc.invalidateQueries({ queryKey: ['integrations', 'live'] });
    },
  });
}

export function useStartExpand() {
  return useRelayMutation<ExpandRequest, JobAccepted>('expand');
}

export function useStartExport() {
  return useRelayMutation<ExportRequest, JobAccepted>('export');
}

export function useStartDiff() {
  return useRelayMutation<DiffRequest, DiffAccepted>('diff');
}

export function useStartDiffLocal() {
  return useRelayMutation<DiffLocalRequest, DiffAccepted>('diff-local');
}

export function useStartApply() {
  return useRelayMutation<ApplyRequest, JobAccepted>('apply');
}

export function useStopJob() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  return useMutation<KaipokeJob, Error, string>({
    mutationFn: (id) =>
      fetcher<KaipokeJob>(`/api/v1/integrations/jobs/${id}/stop`, {
        method: 'POST',
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['integrations'] });
    },
  });
}

export function useIntegrationJobs(limit = 20) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useQuery<Paginated<KaipokeJob>>({
    queryKey: ['integrations', 'jobs', limit],
    queryFn: () =>
      fetcher<Paginated<KaipokeJob>>(`/api/v1/integrations/jobs?limit=${limit}`, {
        accessToken,
        refreshToken,
      }),
    enabled: status === 'authenticated' && session?.user?.role === 'admin',
    refetchInterval: 30_000,
  });
}

// --- Wave 4-A: correction sheets / items ---------------------------------

export function useCorrectionSheet(month?: string) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useQuery<CorrectionSheet>({
    queryKey: ['integrations', 'correction-sheets', 'latest', month ?? null],
    queryFn: () => {
      const usp = new URLSearchParams();
      if (month) usp.set('month', month);
      const q = usp.toString();
      return fetcher<CorrectionSheet>(
        `/api/v1/integrations/correction-sheets/latest${q ? `?${q}` : ''}`,
        { accessToken, refreshToken },
      );
    },
    enabled: status === 'authenticated' && session?.user?.role === 'admin',
    retry: false,
  });
}

export interface UseCorrectionItemsFilter {
  type?: string;
  include?: boolean;
  limit?: number;
  offset?: number;
}

export function useCorrectionItems(
  sheetId: string | undefined,
  filter: UseCorrectionItemsFilter = {},
) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const { type, include, limit = 100, offset = 0 } = filter;

  return useQuery<Paginated<CorrectionItem>>({
    queryKey: [
      'integrations',
      'correction-items',
      sheetId,
      type ?? null,
      include ?? null,
      limit,
      offset,
    ],
    queryFn: () => {
      const usp = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      if (type) usp.set('type', type);
      if (include !== undefined) usp.set('include', String(include));
      return fetcher<Paginated<CorrectionItem>>(
        `/api/v1/integrations/correction-sheets/${sheetId}/items?${usp.toString()}`,
        { accessToken, refreshToken },
      );
    },
    enabled: status === 'authenticated' && Boolean(sheetId),
  });
}

export function useUpdateCorrectionItem() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  return useMutation<CorrectionItem, Error, { id: string; patch: CorrectionItemUpdate }>({
    mutationFn: ({ id, patch }) =>
      fetcher<CorrectionItem>(`/api/v1/integrations/correction-items/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['integrations', 'correction-items'] });
      void qc.invalidateQueries({ queryKey: ['integrations', 'correction-sheets'] });
    },
  });
}

export function useBulkUpdateItems() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  return useMutation<
    { updated: number },
    Error,
    { sheetId: string; ids: string[]; patch: CorrectionItemUpdate }
  >({
    mutationFn: ({ sheetId, ids, patch }) =>
      fetcher<{ updated: number }>(`/api/v1/integrations/correction-sheets/${sheetId}/items/bulk`, {
        method: 'POST',
        body: JSON.stringify({ ids, patch }),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['integrations', 'correction-items'] });
      void qc.invalidateQueries({ queryKey: ['integrations', 'correction-sheets'] });
    },
  });
}

export function useAiInterpretLogs(params: UseAiInterpretLogsParams = {}) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  const { since, until, model, limit = 100, offset = 0 } = params;

  return useQuery<Paginated<AiInterpretLog>>({
    queryKey: [
      'integrations',
      'ai',
      'logs',
      since ?? null,
      until ?? null,
      model ?? null,
      limit,
      offset,
    ],
    queryFn: () => {
      const usp = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      if (since) usp.set('since', since);
      if (until) usp.set('until', until);
      if (model) usp.set('model', model);
      return fetcher<Paginated<AiInterpretLog>>(`/api/v1/integrations/ai/logs?${usp.toString()}`, {
        accessToken,
        refreshToken,
      });
    },
    enabled: status === 'authenticated',
  });
}
