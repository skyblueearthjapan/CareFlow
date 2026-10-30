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
import {
  useMutation,
  useQuery,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  poolOverviewRequestSchema,
  poolOverviewResponseSchema,
  visitFrequencyWeekResponseSchema,
  type PoolOverviewRequest,
  type PoolOverviewResponse,
  type VisitFrequencyWeekResponse,
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

const VISIT_FREQUENCY_WEEK_PATH = '/api/v1/schedule/v2/visit-frequency-week';

/**
 * 訪問頻度の週ごとの判定のキー。`['patients']` の下に置き、患者の保存
 * (PATIENTS_KEY の無効化) で一緒に取り直す。固定訪問の枠の保存でも無効化する。
 */
export const VISIT_FREQUENCY_WEEK_KEY = ['patients', 'visit-frequency-week'] as const;

/**
 * GET /api/v1/schedule/v2/visit-frequency-week?week_start=YYYY-MM-DD (admin・読み取りのみ)。
 *
 * 保留プールが最初の表示から、隔週・月の第何週のお休みの週の方を「足りない」に
 * 数えず、訪問する週の希望回数を枠の曜日数で数えるために使う
 * (biweekly-monthly-design-2026-10-30.md §3-3 / §4)。
 * 失敗 (403 など) のときは今までどおりの数え方にもどるだけにする (retry しない)。
 */
export function useVisitFrequencyWeek(
  weekStart: string | null | undefined,
  opts?: { enabled?: boolean },
): UseQueryResult<VisitFrequencyWeekResponse, Error> {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  return useQuery<VisitFrequencyWeekResponse, Error>({
    queryKey: [...VISIT_FREQUENCY_WEEK_KEY, weekStart ?? '__none__'],
    enabled: status === 'authenticated' && !!weekStart && (opts?.enabled ?? true),
    retry: false,
    queryFn: async () => {
      const qs = new URLSearchParams({ week_start: weekStart ?? '' });
      const raw = await fetcher<unknown>(`${VISIT_FREQUENCY_WEEK_PATH}?${qs.toString()}`, {
        accessToken,
        refreshToken,
      });
      return visitFrequencyWeekResponseSchema.parse(raw);
    },
  });
}
