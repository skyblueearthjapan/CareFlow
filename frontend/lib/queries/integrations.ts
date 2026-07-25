/**
 * TanStack Query hooks for /api/v1/integrations/* — Phase 5-1 Wave 2-B + 4-A.
 *
 * Covers Kaipoke jobs (list/detail/create/cancel), geocoding cache (admin),
 * kaipoke status + relay (expand/export/diff/apply),
 * and correction-sheet editing. Mirrors backend `api/v1/integrations.py`.
 */
'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import type {
  ApplyInboundRequest,
  ApplyInboundResult,
  ApplyRequest,
  CorrectionItem,
  CorrectionItemUpdate,
  CorrectionSheet,
  DiffAccepted,
  DiffInboundAccepted,
  DiffInboundRequest,
  DiffLocalRequest,
  DiffRequest,
  EventsInboundApplyRequest,
  EventsInboundApplyResult,
  EventsInboundPreview,
  EventsInboundPreviewRequest,
  ExpandRequest,
  ExportRequest,
  GeocodingCache,
  InboundEligibility,
  JobAccepted,
  KaipokeCredentials,
  KaipokeJob,
  KaipokeJobCreate,
  ExpandStatus,
  KaipokeStatus,
  LiveSnapshot,
  Paginated,
  SaveKaipokeCredentialsBody,
  TestKaipokeCredentialsResult,
  WeekSchedule,
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

/** 対象月の展開状況 (展開は月1回・2回目ブロック判定用)。 */
export function useExpandStatus(month: string | null) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useQuery<ExpandStatus>({
    queryKey: ['integrations', 'expand-status', month],
    queryFn: () =>
      fetcher<ExpandStatus>(`/api/v1/integrations/expand-status?month=${month}`, {
        accessToken,
        refreshToken,
      }),
    enabled: status === 'authenticated' && session?.user?.role === 'admin' && Boolean(month),
  });
}

/** 対象週の CareFlow スケジュール (週ビュー表示用)。週が変わると自動で切り替わる。 */
export function useWeekSchedule(weekStart: string | null, weekEnd: string | null) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useQuery<WeekSchedule>({
    queryKey: ['integrations', 'week-schedule', weekStart, weekEnd],
    queryFn: () => {
      const usp = new URLSearchParams({ weekStart: weekStart!, weekEnd: weekEnd! });
      return fetcher<WeekSchedule>(`/api/v1/integrations/week-schedule?${usp.toString()}`, {
        accessToken,
        refreshToken,
      });
    },
    enabled:
      status === 'authenticated' &&
      session?.user?.role === 'admin' &&
      Boolean(weekStart) &&
      Boolean(weekEnd),
    staleTime: 60_000,
  });
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

// --- Inbound sync (カイポケ → CareFlow) ------------------------------------

/** apply 実績ゲート確認: 対象週に dry_run=false の apply 完了ジョブが存在するか。 */
export function useInboundEligibility(weekStart: string | null) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useQuery<InboundEligibility>({
    queryKey: ['integrations', 'inbound-eligibility', weekStart],
    queryFn: () =>
      fetcher<InboundEligibility>(
        `/api/v1/integrations/inbound-eligibility?weekStart=${weekStart}`,
        { accessToken, refreshToken },
      ),
    enabled: status === 'authenticated' && session?.user?.role === 'admin' && Boolean(weekStart),
  });
}

/** カイポケ現況を export → 逆向き CorrectionSheet を作成する（〜1分）。 */
export function useStartDiffInbound() {
  return useRelayMutation<DiffInboundRequest, DiffInboundAccepted>('diff-inbound');
}

/**
 * 逆向きシートを CareFlow visits へ適用する（dry_run 既定 true）。
 * apply 成功後に board/visits 系クエリを invalidate する。
 */
export function useApplyInbound() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  return useMutation<ApplyInboundResult, Error, ApplyInboundRequest>({
    mutationFn: (payload) =>
      fetcher<ApplyInboundResult>('/api/v1/integrations/apply-inbound', {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['integrations', 'kaipoke', 'jobs'] });
      void qc.invalidateQueries({ queryKey: ['integrations', 'live'] });
      // 取り込み後は visits / board 系を再取得して最新状態を反映する。
      void qc.invalidateQueries({ queryKey: ['visits'] });
      void qc.invalidateQueries({ queryKey: ['board'] });
    },
  });
}

// --- Credentials (カイポケ接続設定・admin専用) ---------------------------------

/** カイポケ接続設定を取得する（パスワードは絶対に返らない）。admin専用。 */
export function useKaipokeCredentials() {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useQuery<KaipokeCredentials>({
    queryKey: ['integrations', 'credentials'],
    queryFn: () =>
      fetcher<KaipokeCredentials>('/api/v1/integrations/credentials', {
        accessToken,
        refreshToken,
      }),
    enabled: status === 'authenticated' && session?.user?.role === 'admin',
  });
}

/** カイポケ接続設定を保存する（PUT）。成功後に GET を invalidate。 */
export function useSaveKaipokeCredentials() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  return useMutation<KaipokeCredentials, Error, SaveKaipokeCredentialsBody>({
    mutationFn: (payload) =>
      fetcher<KaipokeCredentials>('/api/v1/integrations/credentials', {
        method: 'PUT',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['integrations', 'credentials'] });
    },
  });
}

/**
 * カイポケ接続テストを実行する（実ログイン試行・約60秒）。
 * タイムアウトは90秒に設定。409(busy)・422(未設定)はエラーとして伝播する。
 */
export function useTestKaipokeCredentials() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<TestKaipokeCredentialsResult, Error, void>({
    mutationFn: () =>
      fetcher<TestKaipokeCredentialsResult>('/api/v1/integrations/credentials/test', {
        method: 'POST',
        accessToken,
        refreshToken,
        signal: AbortSignal.timeout(90_000),
      }),
  });
}

// --- イベント取り込み (個別業務・kaipoke-event-inbound-design.md E-2) -------

/**
 * カイポケ個別業務(イベント)を取得して staff_events との差分計画を返す。
 * RPA 同期取得のため 〜2分かかる (訪問の diff-inbound と直列で使う)。
 */
export function useEventsInboundPreview() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<EventsInboundPreview, Error, EventsInboundPreviewRequest>({
    mutationFn: (payload) =>
      fetcher<EventsInboundPreview>('/api/v1/integrations/events-inbound-preview', {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
        signal: AbortSignal.timeout(180_000),
      }),
  });
}

/**
 * プレビューの changes をエコーバックして staff_events へ適用する (dry_run 既定 true)。
 * 実適用後は staff-events 系クエリを invalidate してイベント帯を最新化する。
 */
export function useApplyEventsInbound() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  return useMutation<EventsInboundApplyResult, Error, EventsInboundApplyRequest>({
    mutationFn: (payload) =>
      fetcher<EventsInboundApplyResult>('/api/v1/integrations/events-inbound-apply', {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
        // apply は DB 書込のみ (RPA 非経由) だが、明示タイムアウトで preview と揃える。
        signal: AbortSignal.timeout(30_000),
      }),
    onSuccess: (res) => {
      if (res.dryRun) return;
      void qc.invalidateQueries({ queryKey: ['integrations', 'kaipoke', 'jobs'] });
      // イベント帯 (スケジュール画面) を最新化する。
      void qc.invalidateQueries({ queryKey: ['staff', 'events'] });
    },
  });
}
