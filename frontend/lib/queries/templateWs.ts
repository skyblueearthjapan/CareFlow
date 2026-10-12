'use client';

/**
 * 型の作業場 (template workspace) の API クライアント — /api/v1/template-ws.
 * 下書きの編集は全部ここを通る（人もらく助エージェントも同じ API）。変更は expected_version を送り、
 * 他で変わっていたら 409。読む側は 3 秒ごとに /version を見て、変わっていたら読み直す（エージェントの編集がすぐ見える）。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import type {
  Actor,
  CommitPreview,
  CompareResult,
  DraftSummary,
  DraftView,
  InputsView,
  Job,
  WeekPreview,
} from '@/lib/templateWs/types';

const BASE = '/api/v1/template-ws';
export const POLL_MS = 3000;

function useAuth() {
  const { data: session } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    isAdmin: session?.user?.role === 'admin',
  };
}

/** 型の作業場を使える拠点 (feature flag ON)。管理者以外・OFF は空。 */
export function useTemplateAvailability() {
  const a = useAuth();
  return useQuery({
    queryKey: ['template-ws', 'availability'],
    enabled: a.isAdmin,
    staleTime: 60_000,
    retry: false,
    queryFn: async () => {
      try {
        const r = await fetcher<{ office_ids: string[] }>(`${BASE}/availability`, a);
        return r.office_ids;
      } catch {
        return [] as string[];
      }
    },
  });
}

export function useDrafts(officeId: string | null) {
  const a = useAuth();
  return useQuery({
    queryKey: ['template-ws', 'drafts', officeId],
    enabled: a.isAdmin && !!officeId,
    queryFn: () => fetcher<DraftSummary[]>(`${BASE}/drafts?office_id=${officeId}`, a),
  });
}

/** 下書き 1 つ。3 秒ごとに版を見て、変わっていたら読み直す。 */
export function useDraft(draftId: string | null, compareWith: string | null) {
  const a = useAuth();
  const qc = useQueryClient();
  const key = ['template-ws', 'draft', draftId, compareWith];
  const view = useQuery({
    queryKey: key,
    enabled: a.isAdmin && !!draftId,
    queryFn: () =>
      fetcher<DraftView>(
        `${BASE}/drafts/${draftId}${compareWith ? `?compare_with=${compareWith}` : ''}`,
        a,
      ),
  });
  const known = view.data?.version;
  useQuery({
    queryKey: ['template-ws', 'version', draftId],
    enabled: a.isAdmin && !!draftId && known != null,
    refetchInterval: POLL_MS,
    queryFn: async () => {
      const v = await fetcher<{ version: number; running: boolean }>(
        `${BASE}/drafts/${draftId}/version`,
        a,
      );
      if (known != null && v.version !== known) void qc.invalidateQueries({ queryKey: key });
      return v;
    },
  });
  return view;
}

export class ConflictError extends Error {}

export function useDraftActions(draftId: string | null) {
  const a = useAuth();
  const qc = useQueryClient();
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['template-ws', 'draft', draftId] });
    void qc.invalidateQueries({ queryKey: ['template-ws', 'drafts'] });
  };
  const post = <T,>(tail: string, body: unknown) =>
    fetcher<T>(`${BASE}/drafts/${draftId}/${tail}`, {
      ...a,
      method: 'POST',
      body: JSON.stringify({ actor: 'human' satisfies Actor, ...(body as object) }),
    });
  const useEdit = (tail: string) =>
    useMutation({
      mutationFn: (body: Record<string, unknown>) => post<{ view: DraftView; new_issues?: string[]; travel?: number[] }>(tail, body),
      onSuccess: (res) => {
        qc.setQueryData(['template-ws', 'draft', draftId, null], res.view);
        refresh();
      },
    });
  return {
    move: useEdit('move'),
    whatif: useEdit('whatif'),
    undo: useEdit('undo'),
    redo: useEdit('redo'),
    optimise: useMutation({
      mutationFn: (body: Record<string, unknown>) => post<Job>('optimise', body),
    }),
    refresh,
  };
}

export function useJob(jobId: string | null, onDone: (j: Job) => void) {
  const a = useAuth();
  return useQuery({
    queryKey: ['template-ws', 'job', jobId],
    enabled: !!jobId,
    refetchInterval: (q) => (q.state.data && ['done', 'failed'].includes(q.state.data.status) ? false : 1500),
    queryFn: async () => {
      const j = await fetcher<Job>(`${BASE}/jobs/${jobId}`, a);
      if (j.status === 'done' || j.status === 'failed') onDone(j);
      return j;
    },
  });
}

export function useCreateDraft() {
  const a = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { officeId: string; name: string; fromDraftId?: string | null }) =>
      fetcher<DraftView>(`${BASE}/drafts`, {
        ...a,
        method: 'POST',
        body: JSON.stringify({ office_id: v.officeId, name: v.name, from_draft_id: v.fromDraftId ?? null }),
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['template-ws', 'drafts'] }),
  });
}

export function usePatchDraft() {
  const a = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { id: string; name?: string; archived?: boolean }) =>
      fetcher(`${BASE}/drafts/${v.id}`, {
        ...a,
        method: 'PATCH',
        body: JSON.stringify({ name: v.name, archived: v.archived }),
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['template-ws'] }),
  });
}

export function useCompare(draftId: string | null, other: string | null) {
  const a = useAuth();
  return useQuery({
    queryKey: ['template-ws', 'compare', draftId, other],
    enabled: !!draftId && !!other,
    queryFn: () => fetcher<CompareResult>(`${BASE}/drafts/${draftId}/compare?other=${other}`, a),
  });
}

export function useInputs(draftId: string | null, weekday: number, enabled: boolean) {
  const a = useAuth();
  return useQuery({
    queryKey: ['template-ws', 'inputs', draftId, weekday],
    enabled: enabled && !!draftId,
    queryFn: () => fetcher<InputsView>(`${BASE}/drafts/${draftId}/inputs?weekday=${weekday}`, a),
  });
}

export function useCommitPreview(draftId: string | null, enabled: boolean) {
  const a = useAuth();
  return useQuery({
    queryKey: ['template-ws', 'commit-preview', draftId],
    enabled: enabled && !!draftId,
    gcTime: 0,
    queryFn: () => fetcher<CommitPreview>(`${BASE}/drafts/${draftId}/commit-preview`, a),
  });
}

export function useCommit(draftId: string | null) {
  const a = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (expectedVersion: number) =>
      fetcher(`${BASE}/drafts/${draftId}/commit`, {
        ...a,
        method: 'POST',
        body: JSON.stringify({ expected_version: expectedVersion, confirm: true }),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['template-ws'] });
      void qc.invalidateQueries({ queryKey: ['patient-fixed-visits'] });
    },
  });
}

export function useWeekPreview(draftId: string | null, isoYear: number, isoWeek: number, enabled: boolean) {
  const a = useAuth();
  return useQuery({
    queryKey: ['template-ws', 'week-preview', draftId, isoYear, isoWeek],
    enabled: enabled && !!draftId,
    gcTime: 0,
    retry: false,
    queryFn: () =>
      fetcher<WeekPreview>(
        `${BASE}/drafts/${draftId}/week-preview?iso_year=${isoYear}&iso_week=${isoWeek}`,
        a,
      ),
  });
}

export function useWeekApply(draftId: string | null) {
  const a = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { isoYear: number; isoWeek: number; weekHash: string; expectedVersion: number }) =>
      fetcher(`${BASE}/drafts/${draftId}/week-apply`, {
        ...a,
        method: 'POST',
        body: JSON.stringify({
          iso_year: v.isoYear,
          iso_week: v.isoWeek,
          week_hash: v.weekHash,
          expected_version: v.expectedVersion,
          confirm: true,
        }),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['template-ws'] });
      void qc.invalidateQueries({ queryKey: ['visits'] });
      void qc.invalidateQueries({ queryKey: ['courses'] });
      void qc.invalidateQueries({ queryKey: ['op-log-state'] });
    },
  });
}
