'use client';

/**
 * useScopeOptimizationSimulate / useScopeOptimizationApply — 範囲最適化の hooks.
 *
 *   POST /api/v1/schedule/v2/scope-optimization/simulate (read-only)
 *   POST /api/v1/schedule/v2/scope-optimization/apply    (W2: 先頭N手を 1 TX 適用)
 *
 * BE 仕様: `docs/plans/scope-optimization-design.md` §4 (契約は
 * `lib/schemas/v2/scopeOptimization.ts` と 1:1)。
 * RBAC: admin / manager のみ (BE 側で 403 担保)。
 *
 * simulate は POST だが read-only の計算トリガーなので useMutation で表現する
 * (ボタン押下ごとに最新状態で再計算する。キャッシュ再利用はしない)。
 * apply 成功後は固定枠・visits・改善提案のキャッシュを invalidate する
 * (apply-swap と同じ方針)。
 */
import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import { IMPROVEMENT_SUGGESTIONS_KEY } from '@/lib/queries/improvementSuggestions';
import {
  scopeOptimizationApplyResponseSchema,
  scopeOptimizationSimulateResponseSchema,
  type ScopeOptimizationApplyRequest,
  type ScopeOptimizationApplyResponse,
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

/**
 * POST /api/v1/schedule/v2/scope-optimization/apply (W2).
 * simulate 結果の先頭 N 手を 1 TX で PFV へ適用する。成功後は影響患者の固定枠・
 * 週次 visits・改善提案キャッシュを invalidate して画面へ反映する。
 */
export function useScopeOptimizationApply(): UseMutationResult<
  ScopeOptimizationApplyResponse,
  Error,
  ScopeOptimizationApplyRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<ScopeOptimizationApplyResponse, Error, ScopeOptimizationApplyRequest>({
    mutationFn: async (body) => {
      const raw = await fetcher<unknown>(`${SCOPE_OPTIMIZATION_PATH}/apply`, {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
      return scopeOptimizationApplyResponseSchema.parse(raw);
    },
    onSuccess: (_data, variables) => {
      // 影響患者 (手順の視点主 + swap 相手) の固定枠キャッシュを失効.
      const pids = new Set<string>();
      for (const s of variables.steps) {
        pids.add(s.patient_id);
        const cp = s.suggestion.swap_counterpart;
        if (cp) pids.add(cp.patient_id);
      }
      for (const pid of pids) {
        void qc.invalidateQueries({ queryKey: ['patients', pid, 'fixed-visits'] });
      }
      void qc.invalidateQueries({ queryKey: ['visits'] });
      void qc.invalidateQueries({ queryKey: [IMPROVEMENT_SUGGESTIONS_KEY] });
    },
  });
}
