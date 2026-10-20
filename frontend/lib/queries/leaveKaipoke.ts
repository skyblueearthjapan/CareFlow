/**
 * TanStack Query hooks for 休みのカイポケとの照合と送信 (`/api/v1/leave/compare/*`・`/outbound/*`・管理者)。
 *
 * ジョブは応答の後に動くので、画面は状態を数秒おきに読む (Cloudflare の 100 秒の制限を避ける)。
 * キャッシュのキーは ['leave', 'kaipoke', ...] (休みを書いたときの ['leave'] の読み直しに含まれる)。
 */
'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import { invalidateAfterLeave } from '@/lib/queries/leave';
import {
  compareApplyResultSchema,
  compareJobStatusSchema,
  compareLatestSchema,
  compareStartSchema,
  isJobActive,
  outboundJobStatusSchema,
  outboundPreviewSchema,
  outboundStartSchema,
  type CompareApplyItem,
  type CompareApplyResult,
  type CompareJobStatus,
  type CompareLatest,
  type CompareStart,
  type OutboundJobStatus,
  type OutboundPreview,
  type OutboundStart,
} from '@/lib/schemas/leaveKaipoke';

const BASE = '/api/v1/leave';
/** ジョブの状態を読み直す間隔。 */
export const LEAVE_JOB_POLL_MS = 3_000;

export const compareLatestKey = (month: string) => ['leave', 'kaipoke', 'latest', month] as const;
export const outboundPreviewKey = (month: string) =>
  ['leave', 'kaipoke', 'preview', month] as const;

function useTokens() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    ready: status === 'authenticated',
  };
}

/** GET /compare/latest — その月の最新の照合と、動いているジョブ。 */
export function useCompareLatest(month: string, enabled = true) {
  const { accessToken, refreshToken, ready } = useTokens();
  return useQuery<CompareLatest>({
    queryKey: compareLatestKey(month),
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/compare/latest?month=${month}`, {
        accessToken,
        refreshToken,
      });
      return compareLatestSchema.parse(raw);
    },
    enabled: ready && enabled,
  });
}

/** GET /compare/{job_id} — 終わるまで数秒おきに読む。 */
export function useCompareJob(jobId: string | null) {
  const { accessToken, refreshToken, ready } = useTokens();
  return useQuery<CompareJobStatus>({
    queryKey: ['leave', 'kaipoke', 'compare-job', jobId],
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/compare/${jobId}`, {
        accessToken,
        refreshToken,
      });
      return compareJobStatusSchema.parse(raw);
    },
    enabled: ready && !!jobId,
    refetchInterval: (q) => {
      const st = q.state.data?.state;
      return !st || st === 'fetching' || st === 'comparing' ? LEAVE_JOB_POLL_MS : false;
    },
  });
}

/** POST /compare/start — カイポケと比べる (取り直しのジョブ)。409 は文言をそのまま出す。 */
export function useStartCompare() {
  const { accessToken, refreshToken } = useTokens();
  return useMutation<CompareStart, Error, string>({
    mutationFn: async (month) => {
      const raw = await fetcher<unknown>(`${BASE}/compare/start`, {
        method: 'POST',
        body: JSON.stringify({ month }),
        accessToken,
        refreshToken,
      });
      return compareStartSchema.parse(raw);
    },
  });
}

/** POST /compare/apply — 差分を合わせる (1 件でも、まとめてでも)。 */
export function useApplyCompare() {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<CompareApplyResult, Error, CompareApplyItem[]>({
    mutationFn: async (items) => {
      const raw = await fetcher<unknown>(`${BASE}/compare/apply`, {
        method: 'POST',
        body: JSON.stringify({ items }),
        accessToken,
        refreshToken,
      });
      return compareApplyResultSchema.parse(raw);
    },
    onSettled: () => void invalidateAfterLeave(qc),
  });
}

/** GET /outbound/preview — 送る前の一覧。 */
export function useOutboundPreview(month: string, enabled = true) {
  const { accessToken, refreshToken, ready } = useTokens();
  return useQuery<OutboundPreview>({
    queryKey: outboundPreviewKey(month),
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/outbound/preview?month=${month}`, {
        accessToken,
        refreshToken,
      });
      return outboundPreviewSchema.parse(raw);
    },
    enabled: ready && enabled,
  });
}

/** POST /outbound/start — 未送信の休みをカイポケへ送る (ジョブ)。 */
export function useStartOutbound() {
  const { accessToken, refreshToken } = useTokens();
  return useMutation<OutboundStart, Error, string>({
    mutationFn: async (month) => {
      const raw = await fetcher<unknown>(`${BASE}/outbound/start`, {
        method: 'POST',
        body: JSON.stringify({ month }),
        accessToken,
        refreshToken,
      });
      return outboundStartSchema.parse(raw);
    },
  });
}

/** GET /outbound/{job_id} — 終わるまで数秒おきに読む。 */
export function useOutboundJob(jobId: string | null) {
  const { accessToken, refreshToken, ready } = useTokens();
  return useQuery<OutboundJobStatus>({
    queryKey: ['leave', 'kaipoke', 'outbound-job', jobId],
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/outbound/${jobId}`, {
        accessToken,
        refreshToken,
      });
      return outboundJobStatusSchema.parse(raw);
    },
    enabled: ready && !!jobId,
    refetchInterval: (q) =>
      !q.state.data || isJobActive(q.state.data.job) ? LEAVE_JOB_POLL_MS : false,
  });
}
