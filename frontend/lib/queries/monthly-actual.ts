/**
 * TanStack Query hooks for 月の実績 (訪問記録の「月の実績」タブ・`/api/v1/monthly-actual/*`・管理者)。
 *
 * エンドポイント (backend/app/api/v1/monthly_actual.py):
 *   GET    /{month}               → 判定の一覧と件数 (新しい実績があれば BE が判定し直す)
 *   POST   /{month}/refresh       → カイポケの実績を取り直して判定し直す (ジョブ・202)
 *   PATCH  /rows/{row_id}         → 反映する／しない
 *   POST   /{month}/apply         → 選んだ行を RPA へ (dry_run 既定 true・ジョブ・202)
 *   GET    /{month}/apply/status  → 反映の進み具合と行ごとの結果
 *   POST   /{month}/schedule      → 夜に流す予約 (既定は今夜 22:00 JST)
 *   GET    /{month}/export.xlsx   → 月の一覧 (4 シート)
 *
 * ジョブは応答の後に動くので、画面は状態を数秒おきに読む (Cloudflare の 100 秒の制限を避ける)。
 * 409 (ほかのカイポケの処理が動いている・反映の途中) と 422 (過去の月だけ・送る行が無い) は
 * BE の `detail` をそのまま出す。キャッシュのキーは ['monthly-actual', ...]。
 */
'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';
import {
  jobAcceptedSchema,
  parseMonthlyActualApplyStatus,
  parseMonthlyActualList,
  rawRowSchema,
  toMonthlyActualRow,
  type MonthlyActualApplyStatus,
  type MonthlyActualJobAccepted,
  type MonthlyActualList,
  type MonthlyActualRow,
} from '@/lib/schemas/monthly-actual';

export const MONTHLY_ACTUAL_PATH = '/api/v1/monthly-actual';
/** ジョブの状態を読み直す間隔。 */
export const MONTHLY_ACTUAL_POLL_MS = 5_000;

export const monthlyActualKey = (month: string) => ['monthly-actual', 'month', month] as const;
export const monthlyActualApplyStatusKey = (month: string) =>
  ['monthly-actual', 'apply-status', month] as const;

/** 月の一覧の Excel の道 (Bearer が要るので、取るときは `useMonthlyActualExport` を使う)。 */
export function monthlyActualExportUrl(month: string): string {
  return `${MONTHLY_ACTUAL_PATH}/${month}/export.xlsx`;
}

function useTokens() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    ready: status === 'authenticated',
  };
}

/** 一覧を読み直す間隔: 取り直し・反映が動いている間だけ読む。 */
export function monthlyActualPollMs(data: MonthlyActualList | undefined): number | false {
  if (!data) return false;
  return data.refreshing || data.job?.running ? MONTHLY_ACTUAL_POLL_MS : false;
}

/** GET /{month} — 判定の一覧と件数。取り直し・反映の間は数秒おきに読む。 */
export function useMonthlyActual(month: string, options: { enabled?: boolean } = {}) {
  const { accessToken, refreshToken, ready } = useTokens();
  return useQuery<MonthlyActualList>({
    queryKey: monthlyActualKey(month),
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${MONTHLY_ACTUAL_PATH}/${month}`, {
        accessToken,
        refreshToken,
      });
      return parseMonthlyActualList(raw);
    },
    enabled: ready && !!month && (options.enabled ?? true),
    refetchInterval: (q) => monthlyActualPollMs(q.state.data),
  });
}

/** POST /{month}/refresh — カイポケの実績を読み取り直す (ジョブ)。 */
export function useRefreshMonthlyActual() {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<MonthlyActualJobAccepted, Error, string>({
    mutationFn: async (month) => {
      const raw = await fetcher<unknown>(`${MONTHLY_ACTUAL_PATH}/${month}/refresh`, {
        method: 'POST',
        accessToken,
        refreshToken,
      });
      return jobAcceptedSchema.parse(raw);
    },
    onSettled: (_d, _e, month) => qc.invalidateQueries({ queryKey: monthlyActualKey(month) }),
  });
}

export interface UpdateMonthlyActualRowVariables {
  rowId: string;
  /** 反映する (true)／しない (false)。 */
  apply: boolean;
}

/** PATCH /rows/{row_id} — 反映する／しない。 */
export function useUpdateMonthlyActualRow() {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<MonthlyActualRow, Error, UpdateMonthlyActualRowVariables>({
    mutationFn: async ({ rowId, apply }) => {
      const raw = await fetcher<unknown>(`${MONTHLY_ACTUAL_PATH}/rows/${rowId}`, {
        method: 'PATCH',
        body: JSON.stringify({ selected: apply }),
        accessToken,
        refreshToken,
      });
      return toMonthlyActualRow(rawRowSchema.parse(raw));
    },
    // 一覧の行をその場で差し替える (650 行の読み直しを待たない)。件数は次の読み直しで合う。
    onSuccess: (row) => {
      qc.setQueriesData<MonthlyActualList>({ queryKey: ['monthly-actual', 'month'] }, (old) =>
        old ? { ...old, rows: old.rows.map((r) => (r.id === row.id ? row : r)) } : old,
      );
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ['monthly-actual', 'month'] }),
  });
}

export interface ApplyMonthlyActualVariables {
  month: string;
  /** true = 試しに流す (登録の直前まで進めて閉じる)。 */
  dryRun: boolean;
  /** 送る行 (省略 = 選んだ行すべて)。 */
  rowIds?: string[];
  /** 前に失敗した行だけ流し直す。 */
  onlyFailed?: boolean;
}

/** POST /{month}/apply — 選んだ行の実績の時刻をカイポケへ書き換える (ジョブ)。 */
export function useApplyMonthlyActual() {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<MonthlyActualJobAccepted, Error, ApplyMonthlyActualVariables>({
    mutationFn: async ({ month, dryRun, rowIds, onlyFailed }) => {
      const raw = await fetcher<unknown>(`${MONTHLY_ACTUAL_PATH}/${month}/apply`, {
        method: 'POST',
        body: JSON.stringify({
          dry_run: dryRun,
          ...(rowIds ? { row_ids: rowIds } : {}),
          ...(onlyFailed ? { only_failed: true } : {}),
        }),
        accessToken,
        refreshToken,
      });
      return jobAcceptedSchema.parse(raw);
    },
    onSettled: (_d, _e, { month }) =>
      Promise.all([
        qc.invalidateQueries({ queryKey: monthlyActualKey(month) }),
        qc.invalidateQueries({ queryKey: monthlyActualApplyStatusKey(month) }),
      ]),
  });
}

/**
 * GET /{month}/apply/status — 反映の進み具合と行ごとの結果。動いている間は数秒おきに読む
 * (BE が RPA の結果を取りに行って行に書く)。終わったら一覧も読み直す。
 */
export function useMonthlyActualApplyStatus(month: string, options: { enabled?: boolean } = {}) {
  const { accessToken, refreshToken, ready } = useTokens();
  const qc = useQueryClient();
  return useQuery<MonthlyActualApplyStatus>({
    queryKey: monthlyActualApplyStatusKey(month),
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${MONTHLY_ACTUAL_PATH}/${month}/apply/status`, {
        accessToken,
        refreshToken,
      });
      const status = parseMonthlyActualApplyStatus(raw);
      if (!status.running) void qc.invalidateQueries({ queryKey: monthlyActualKey(month) });
      return status;
    },
    enabled: ready && !!month && (options.enabled ?? true),
    refetchInterval: (q) => {
      if (q.state.status === 'error') return false;
      return q.state.data?.running ? MONTHLY_ACTUAL_POLL_MS : false;
    },
    retry: 2,
  });
}

export interface ScheduleMonthlyActualVariables {
  month: string;
  /** 流す時刻 (ISO 8601)。省略 = 今夜 22:00 (JST)。 */
  at?: string;
  /** 試しに流す予約にする (既定は本番の反映)。 */
  dryRun?: boolean;
}

/** POST /{month}/schedule — 夜に流す予約。 */
export function useScheduleMonthlyActual() {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<unknown, Error, ScheduleMonthlyActualVariables>({
    mutationFn: async ({ month, at, dryRun }) =>
      fetcher<unknown>(`${MONTHLY_ACTUAL_PATH}/${month}/schedule`, {
        method: 'POST',
        body: JSON.stringify({ ...(at ? { at } : {}), dry_run: dryRun ?? false }),
        accessToken,
        refreshToken,
      }),
    onSettled: (_d, _e, { month }) => qc.invalidateQueries({ queryKey: monthlyActualKey(month) }),
  });
}

/** DELETE /{month}/schedule — 予約の取り消し。 */
export function useCancelMonthlyActualSchedule() {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<unknown, Error, string>({
    mutationFn: async (month) =>
      fetcher<unknown>(`${MONTHLY_ACTUAL_PATH}/${month}/schedule`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      }),
    onSettled: (_d, _e, month) => qc.invalidateQueries({ queryKey: monthlyActualKey(month) }),
  });
}

function resolveBaseUrl(): string {
  if (typeof window !== 'undefined') return '';
  return (
    process.env.BACKEND_API_BASE_URL ??
    process.env.NEXT_PUBLIC_BACKEND_API_BASE_URL ??
    'http://localhost:8000'
  );
}

export interface MonthlyActualExportFile {
  blob: Blob;
  filename: string;
}

/**
 * GET /{month}/export.xlsx — 月の一覧を blob で受ける (Bearer 付きの `fetch`。
 * `fetcher` は本文をテキストで読むのでバイナリには使えない)。
 */
export function useMonthlyActualExport() {
  const { accessToken } = useTokens();
  return useMutation<MonthlyActualExportFile, Error, string>({
    mutationFn: async (month) => {
      const path = monthlyActualExportUrl(month);
      const headers: Record<string, string> = {};
      if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
      const res = await fetch(`${resolveBaseUrl()}${path}`, {
        method: 'GET',
        headers,
        cache: 'no-store',
      });
      if (!res.ok) {
        const text = await res.text();
        let body: unknown = text;
        try {
          body = JSON.parse(text);
        } catch {
          /* テキストのまま */
        }
        throw new ApiError(`API ${res.status} ${res.statusText} (${path})`, res.status, body);
      }
      const [year, mon] = month.split('-');
      return {
        blob: await res.blob(),
        filename: `${Number(year)}年${Number(mon)}月 訪問の実績.xlsx`,
      };
    },
  });
}
