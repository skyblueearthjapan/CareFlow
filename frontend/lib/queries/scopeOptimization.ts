'use client';

/**
 * useScopeOptimizationSimulate — 範囲最適化 (scope-optimization W1) の hook.
 *
 *   POST /api/v1/schedule/v2/scope-optimization/simulate (read-only)
 *
 * BE 仕様: `docs/plans/scope-optimization-design.md` §4 (契約は
 * `lib/schemas/v2/scopeOptimization.ts` と 1:1)。
 * RBAC: admin / manager のみ (BE 側で 403 担保)。
 *
 * POST だが read-only の計算トリガーなので useMutation で表現する
 * (ボタン押下ごとに最新状態で再計算する。キャッシュ再利用はしない)。
 */
import { useMutation, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  scopeOptimizationSimulateResponseSchema,
  type ScopeOptimizationSimulateRequest,
  type ScopeOptimizationSimulateResponse,
} from '@/lib/schemas/v2/scopeOptimization';

const SCOPE_OPTIMIZATION_PATH = '/api/v1/schedule/v2/scope-optimization';

/** POST /api/v1/schedule/v2/scope-optimization/simulate. */
export function useScopeOptimizationSimulate(): UseMutationResult<
  ScopeOptimizationSimulateResponse,
  Error,
  ScopeOptimizationSimulateRequest
> {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<ScopeOptimizationSimulateResponse, Error, ScopeOptimizationSimulateRequest>({
    mutationFn: async (body) => {
      const raw = await fetcher<unknown>(`${SCOPE_OPTIMIZATION_PATH}/simulate`, {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
      return scopeOptimizationSimulateResponseSchema.parse(raw);
    },
  });
}
