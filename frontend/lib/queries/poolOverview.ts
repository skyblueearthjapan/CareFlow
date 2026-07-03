'use client';

/**
 * pool-overview v2 mutation hook (Stage P-2).
 *
 * POST /api/v1/schedule/v2/pool-overview — プール患者の俯瞰一覧（患者ごとの最良候補）を計算する。
 *
 * 設計:
 *   - **自動発火なし**。明示ボタン押下でのみ実行する (`useMutation` で on-demand)。
 *   - 上限 50 件ガード: patient_ids > 50 の場合は呼出側で先頭 50 件に切り、toast で通知する。
 *   - 寛容パース: レスポンスは `poolOverviewResponseSchema` (items 欠落→[] 等) でパース。
 *   - BE が並行実装中のため 422 / 5xx が返っても呼出側の onError toast で処理する。
 */
import { useMutation, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  poolOverviewRequestSchema,
  poolOverviewResponseSchema,
  type PoolOverviewRequest,
  type PoolOverviewResponse,
} from '@/lib/schemas/v2/poolOverview';

const POOL_OVERVIEW_PATH = '/api/v1/schedule/v2/pool-overview';

/**
 * POST /api/v1/schedule/v2/pool-overview のミューテーション hook。
 *
 * 明示ボタン押下でのみ実行 (`mutate()` / `mutateAsync()` を呼ぶこと)。
 * 自動発火は行わない。
 */
export function usePoolOverviewMutation(): UseMutationResult<
  PoolOverviewResponse,
  Error,
  PoolOverviewRequest
> {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<PoolOverviewResponse, Error, PoolOverviewRequest>({
    mutationFn: async (raw) => {
      const payload = poolOverviewRequestSchema.parse(raw);
      const result = await fetcher<unknown>(POOL_OVERVIEW_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return poolOverviewResponseSchema.parse(result);
    },
  });
}
