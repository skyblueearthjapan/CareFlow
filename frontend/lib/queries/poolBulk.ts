'use client';

/**
 * プール一括投入 (W-2) mutation hooks.
 *
 *   POST /api/v1/schedule/v2/pool-bulk-simulate (read-only プレビュー)
 *   POST /api/v1/schedule/v2/pool-bulk-apply    (1 TX 一括適用)
 *
 * BE 仕様: `docs/plans/pool-bulk-insert-design.md` §4 (契約は
 * `lib/schemas/v2/poolBulk.ts` と 1:1)。RBAC: admin / manager (BE 側で 403 担保)。
 *
 * simulate は POST だが read-only の計算トリガーなので useMutation で表現する
 * (ダイアログを開くたびに最新状態で再計算する。キャッシュ再利用はしない)。
 * apply 成功後は週スケジュール (visits / courses) ・患者一覧 (プール判定) ・
 * 固定枠 (patient-fixed-visits) ・操作ログ状態を invalidate して画面へ反映する。
 * pool-overview は on-demand mutation のため invalidate 対象クエリを持たない
 * (次回ボタン押下で最新状態を再計算する)。
 */
import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import { OP_LOG_STATE_KEY } from '@/lib/queries/opLog';
import {
  poolBulkApplyResponseSchema,
  poolBulkSimulateResponseSchema,
  type PoolBulkApplyRequest,
  type PoolBulkApplyResponse,
  type PoolBulkSimulateRequest,
  type PoolBulkSimulateResponse,
} from '@/lib/schemas/v2/poolBulk';

const POOL_BULK_BASE = '/api/v1/schedule/v2';

/** POST /api/v1/schedule/v2/pool-bulk-simulate (read-only). */
export function usePoolBulkSimulateMutation(): UseMutationResult<
  PoolBulkSimulateResponse,
  Error,
  PoolBulkSimulateRequest
> {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<PoolBulkSimulateResponse, Error, PoolBulkSimulateRequest>({
    mutationFn: async (body) => {
      const raw = await fetcher<unknown>(`${POOL_BULK_BASE}/pool-bulk-simulate`, {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
      return poolBulkSimulateResponseSchema.parse(raw);
    },
  });
}

/**
 * POST /api/v1/schedule/v2/pool-bulk-apply (1 TX 一括適用).
 * simulate 結果の placements を全件 PFV へ登録 (pattern_and_week 固定)。成功後は
 * 週スケジュール・プール・固定枠・操作ログ状態のキャッシュを invalidate する。
 */
export function usePoolBulkApplyMutation(): UseMutationResult<
  PoolBulkApplyResponse,
  Error,
  PoolBulkApplyRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<PoolBulkApplyResponse, Error, PoolBulkApplyRequest>({
    mutationFn: async (body) => {
      const raw = await fetcher<unknown>(`${POOL_BULK_BASE}/pool-bulk-apply`, {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
      return poolBulkApplyResponseSchema.parse(raw);
    },
    onSuccess: (_data, variables) => {
      // 週スケジュール (実 visit / コース) — 一括投入で当週が再生成される。
      void qc.invalidateQueries({ queryKey: ['visits'] });
      void qc.invalidateQueries({ queryKey: ['courses'] });
      // 患者一覧 (プール判定 / shortage 再計算)。
      void qc.invalidateQueries({ queryKey: ['patients'] });
      // 固定枠 (pattern_and_week で PFV が変わる)。
      void qc.invalidateQueries({ queryKey: ['patient-fixed-visits'] });
      // 操作ログ状態 (undoable=false だが監査サマリが記録される)。
      void qc.invalidateQueries({
        queryKey: [OP_LOG_STATE_KEY, variables.iso_year, variables.iso_week],
      });
    },
  });
}
