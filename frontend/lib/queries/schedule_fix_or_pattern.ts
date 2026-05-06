'use client';

/**
 * TanStack Query hook for POST /api/v1/schedule/fix-or-pattern (W9-FE2 Phase 4).
 *
 * 設計仕様書 `docs/plans/v2-api-contracts.md` §8.6 に準拠。
 *
 * D&D 後のダイアログから呼ばれ、以下のいずれかの挙動を実行する:
 *   - mode=this_week_only : 当該週の visit のみ変更 (固定枠は不変)
 *   - mode=pattern_change : PatientFixedVisit を上書き (翌週以降も反映)
 *
 * onSuccess で visits / patient_fixed_visits / courses を invalidate する。
 */
import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import type {
  FixOrPatternRequest,
  FixOrPatternResponse,
} from '@/lib/schemas/v2/schedule_fix_or_pattern';

const FIX_OR_PATTERN_PATH = '/api/v1/schedule/fix-or-pattern';

function authPair(session: ReturnType<typeof useSession>['data']): {
  accessToken: string | null;
  refreshToken: string | null;
} {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/**
 * POST /api/v1/schedule/fix-or-pattern
 *
 * 成功時に invalidate するキー:
 *   - ['visits']
 *   - ['patients', *, 'fixed-visits', *]
 *   - ['courses']
 */
export function useFixOrPattern(): UseMutationResult<
  FixOrPatternResponse,
  Error,
  FixOrPatternRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<FixOrPatternResponse, Error, FixOrPatternRequest>({
    mutationFn: async (payload) =>
      fetcher<FixOrPatternResponse>(FIX_OR_PATTERN_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      // visit スケジュールが変更されたので関連するキャッシュを無効化する。
      void qc.invalidateQueries({ queryKey: ['visits'] });
      // 固定枠が変わった可能性があるので、全患者分の fixed-visits を無効化する。
      void qc.invalidateQueries({ queryKey: ['patients'] });
      // コース提案もリセットされる場合があるので invalidate する。
      void qc.invalidateQueries({ queryKey: ['courses'] });
    },
  });
}
