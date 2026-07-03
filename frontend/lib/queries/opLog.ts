'use client';

/**
 * useOpLogState / useUndoOpLog / useRedoOpLog / useInvalidateOpLog — Wave U-3 戻る/進む。
 *
 * GET /api/v1/schedule/v2/op-log/state?iso_year&iso_week
 *   → 自分のユーザー×週 の undo/redo スタック状態 (OpLogState)。欠落寛容。
 *
 * POST /api/v1/schedule/v2/op-log/undo { iso_year, iso_week }
 * POST /api/v1/schedule/v2/op-log/redo { iso_year, iso_week }
 *   → 成功 200: レスポンスに新 OpLogState を含む。
 *   → 409: 他ユーザーの変更と衝突 → toast.warning + invalidate で最新を表示。
 *
 * BE 並行実装中。opLogStateSchema の .catch() で欠落フィールドを吸収。
 * RBAC: admin / manager (BE 側で 403 担保)。
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useCallback } from 'react';
import { useSession } from 'next-auth/react';
import { toast } from 'sonner';

import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';
import { opLogStateSchema, type OpLogState } from '@/lib/schemas/v2/opLog';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const OP_LOG_BASE = '/api/v1/schedule/v2/op-log';

/** TanStack Query キー prefix (useOpLogState の queryKey 第 1 要素). */
export const OP_LOG_STATE_KEY = 'op-log-state' as const;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/** undo/redo 後に関連キャッシュ全体を失効させる共通ヘルパー。 */
function invalidateAfterUndoRedo(
  qc: ReturnType<typeof useQueryClient>,
  isoYear: number,
  isoWeek: number,
): void {
  void qc.invalidateQueries({ queryKey: ['visits'] });
  void qc.invalidateQueries({ queryKey: ['patients'] });
  void qc.invalidateQueries({ queryKey: ['courses'] });
  void qc.invalidateQueries({ queryKey: ['patient-fixed-visits'] });
  void qc.invalidateQueries({ queryKey: [OP_LOG_STATE_KEY, isoYear, isoWeek] });
}

// ---------------------------------------------------------------------------
// useOpLogState — GET state
// ---------------------------------------------------------------------------

/**
 * 現在のユーザーの指定週における undo/redo スタック状態を取得する。
 * staleTime を短め (10 秒) に設定し、操作後の invalidate で即座に再取得する。
 */
export function useOpLogState(
  isoYear: number,
  isoWeek: number,
): UseQueryResult<OpLogState, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  const qs = new URLSearchParams();
  qs.set('iso_year', String(isoYear));
  qs.set('iso_week', String(isoWeek));

  return useQuery<OpLogState, Error>({
    queryKey: [OP_LOG_STATE_KEY, isoYear, isoWeek],
    enabled: status === 'authenticated',
    staleTime: 10 * 1000,
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${OP_LOG_BASE}/state?${qs.toString()}`, {
        accessToken,
        refreshToken,
      });
      // U-3 レビュー HIGH: BE レスポンスは { state: {...} } エンベロープ。
      // state を unwrap してからパースする (欠落時はフラット形式へフォールバック)。
      const env = raw as Record<string, unknown> | null;
      return opLogStateSchema.parse(env && typeof env === 'object' && 'state' in env ? env.state : raw);
    },
  });
}

// ---------------------------------------------------------------------------
// useUndoOpLog / useRedoOpLog — POST undo/redo
// ---------------------------------------------------------------------------

export interface OpLogUndoRedoRequest {
  iso_year: number;
  iso_week: number;
}

/** POST /api/v1/schedule/v2/op-log/undo — 直前の操作を取り消す。 */
export function useUndoOpLog(): UseMutationResult<OpLogState, Error, OpLogUndoRedoRequest> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<OpLogState, Error, OpLogUndoRedoRequest>({
    mutationFn: async (body) => {
      const raw = await fetcher<unknown>(`${OP_LOG_BASE}/undo`, {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
      // U-3 レビュー HIGH: BE レスポンスは { state: {...} } エンベロープ。
      // state を unwrap してからパースする (欠落時はフラット形式へフォールバック)。
      const env = raw as Record<string, unknown> | null;
      return opLogStateSchema.parse(env && typeof env === 'object' && 'state' in env ? env.state : raw);
    },
    onSuccess: (_data, vars) => {
      invalidateAfterUndoRedo(qc, vars.iso_year, vars.iso_week);
    },
    onError: (err, vars) => {
      if (err instanceof ApiError && err.status === 409) {
        toast.warning('他の変更があったため戻せません（最新の状態を表示します）');
        invalidateAfterUndoRedo(qc, vars.iso_year, vars.iso_week);
      }
    },
  });
}

/** POST /api/v1/schedule/v2/op-log/redo — 取り消した操作をやり直す。 */
export function useRedoOpLog(): UseMutationResult<OpLogState, Error, OpLogUndoRedoRequest> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<OpLogState, Error, OpLogUndoRedoRequest>({
    mutationFn: async (body) => {
      const raw = await fetcher<unknown>(`${OP_LOG_BASE}/redo`, {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
      // U-3 レビュー HIGH: BE レスポンスは { state: {...} } エンベロープ。
      // state を unwrap してからパースする (欠落時はフラット形式へフォールバック)。
      const env = raw as Record<string, unknown> | null;
      return opLogStateSchema.parse(env && typeof env === 'object' && 'state' in env ? env.state : raw);
    },
    onSuccess: (_data, vars) => {
      invalidateAfterUndoRedo(qc, vars.iso_year, vars.iso_week);
    },
    onError: (err, vars) => {
      if (err instanceof ApiError && err.status === 409) {
        toast.warning('他の変更があったため戻せません（最新の状態を表示します）');
        invalidateAfterUndoRedo(qc, vars.iso_year, vars.iso_week);
      }
    },
  });
}

// ---------------------------------------------------------------------------
// useInvalidateOpLog — op-log state キャッシュの失効ヘルパー hook
// ---------------------------------------------------------------------------

/**
 * `useInvalidateOpLog()` は `(isoYear, isoWeek) => void` の安定した関数を返す。
 * 各 DnD 操作の成功後にこれを呼ぶことで、「↶ 戻る」「↷ 進む」ボタンの
 * 活性状態が即座に更新される。
 *
 * このフックを使うことで CourseDayTablePanel が直接 useQueryClient を
 * 呼ぶ必要がなくなり、テストでのモック化が容易になる。
 */
export function useInvalidateOpLog(): (isoYear: number, isoWeek: number) => void {
  const qc = useQueryClient();
  return useCallback(
    (isoYear: number, isoWeek: number) => {
      void qc.invalidateQueries({ queryKey: [OP_LOG_STATE_KEY, isoYear, isoWeek] });
    },
    [qc],
  );
}
