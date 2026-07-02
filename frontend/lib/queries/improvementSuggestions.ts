'use client';

/**
 * useImprovementSuggestions / useDismissSuggestion — 改善提案 (P2-C) の hooks.
 *
 *   GET  /api/v1/schedule/v2/improvement-suggestions?patient_id&iso_year&iso_week
 *   POST /api/v1/schedule/v2/improvement-suggestions/dismiss
 *
 * BE 仕様: `docs/plans/p2-improvement-mvp-design.md` §2.3 (契約凍結済).
 * RBAC: admin / manager のみ (BE 側で 403 担保).
 *
 * 集計クエリ (PFV 全走査 + 限界コスト) なので staleTime を 5 分取り、同一患者・週の
 * 再 fetch を避ける. dismiss 成功後は改善提案キャッシュを invalidate してカードを消す.
 * API 未デプロイ環境では GET が 404/500 になり得るが、呼出側 (Section) が
 * `isError` をエラーテキストに落として dialog 全体は生かす。
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  applySwapResponseSchema,
  improvementDismissResponseSchema,
  improvementSuggestionsResponseSchema,
  type ApplySwapRequest,
  type ApplySwapResponse,
  type ImprovementDismissRequest,
  type ImprovementDismissResponse,
  type ImprovementSuggestionsResponse,
} from '@/lib/schemas/v2/improvementSuggestion';

const IMPROVEMENT_PATH = '/api/v1/schedule/v2/improvement-suggestions';

/** 改善提案クエリキーの prefix (dismiss 後の invalidate 対象). */
export const IMPROVEMENT_SUGGESTIONS_KEY = 'improvement-suggestions';

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/** GET /api/v1/schedule/v2/improvement-suggestions. */
export function useImprovementSuggestions(
  patientId: string | null | undefined,
  isoYear: number,
  isoWeek: number,
  enabled = true,
): UseQueryResult<ImprovementSuggestionsResponse, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  const qs = new URLSearchParams();
  qs.set('patient_id', patientId ?? '');
  qs.set('iso_year', String(isoYear));
  qs.set('iso_week', String(isoWeek));

  return useQuery<ImprovementSuggestionsResponse, Error>({
    queryKey: [IMPROVEMENT_SUGGESTIONS_KEY, patientId ?? null, isoYear, isoWeek],
    enabled: status === 'authenticated' && enabled && !!patientId,
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${IMPROVEMENT_PATH}?${qs.toString()}`, {
        accessToken,
        refreshToken,
      });
      return improvementSuggestionsResponseSchema.parse(raw);
    },
  });
}

/**
 * POST /api/v1/schedule/v2/improvement-suggestions/dismiss.
 * 成功後に改善提案キャッシュを invalidate してカードを消す。
 */
export function useDismissSuggestion(): UseMutationResult<
  ImprovementDismissResponse,
  Error,
  ImprovementDismissRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<ImprovementDismissResponse, Error, ImprovementDismissRequest>({
    mutationFn: async (body) => {
      const raw = await fetcher<unknown>(`${IMPROVEMENT_PATH}/dismiss`, {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
      return improvementDismissResponseSchema.parse(raw);
    },
    onSuccess: (_data, variables) => {
      void qc.invalidateQueries({ queryKey: [IMPROVEMENT_SUGGESTIONS_KEY] });
      // promote_movability=true は BE が PFV.movability を更新するため、
      // 固定枠パネル側のキャッシュも失効させる (レビューM1)。
      if (variables.promote_movability) {
        void qc.invalidateQueries({
          queryKey: ['patients', variables.patient_id, 'fixed-visits'],
        });
      }
    },
  });
}

/**
 * POST /api/v1/schedule/v2/improvement-suggestions/apply-swap (P3-②).
 * 2 患者の固定枠を 1 TX で入れ替える。成功後は改善提案キャッシュ・両患者の固定枠・
 * 週次 visits を invalidate して親機テーブルへ即時反映する。
 */
export function useApplySwap(): UseMutationResult<ApplySwapResponse, Error, ApplySwapRequest> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<ApplySwapResponse, Error, ApplySwapRequest>({
    mutationFn: async (body) => {
      const raw = await fetcher<unknown>(`${IMPROVEMENT_PATH}/apply-swap`, {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
      return applySwapResponseSchema.parse(raw);
    },
    onSuccess: (_data, variables) => {
      void qc.invalidateQueries({ queryKey: [IMPROVEMENT_SUGGESTIONS_KEY] });
      // 両患者の固定枠 (mode 問わず prefix 一致で失効).
      void qc.invalidateQueries({
        queryKey: ['patients', variables.patient_a_id, 'fixed-visits'],
      });
      void qc.invalidateQueries({
        queryKey: ['patients', variables.patient_b_id, 'fixed-visits'],
      });
      // 親機テーブルの実 visit (入れ替えで週次が変わる).
      void qc.invalidateQueries({ queryKey: ['visits'] });
    },
  });
}
