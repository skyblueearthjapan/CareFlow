'use client';

/**
 * TanStack Query hook for POST /api/v1/schedule/place-and-fix — W15-FE (Wave 15 Phase 3).
 *
 * BE 側 (Phase 2 C-1) で並列実装中。FE は型契約を Zod で先行ミラーした上で
 * 仮実装としてこの hook を提供する。
 *
 * 想定:
 *   - プールの患者カードをセル (course_template_id × weekday × HH:MM) にドロップ
 *   - 1 トランザクションで Visit 新規作成 + 固定枠化 (PatientFixedVisit upsert)
 *   - 既存の `useFixOrPattern` (mode=this_week_only / pattern_change) を **置換** する
 *
 * onSuccess で関連キャッシュ (visits / patients / courses / patient_fixed_visits) を
 * 全部 invalidate する。
 */
import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import type { PlaceAndFixRequest, PlaceAndFixResponse } from '@/lib/schemas/v2/place_and_fix';

const PLACE_AND_FIX_PATH = '/api/v1/schedule/place-and-fix';

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

export function usePlaceAndFix(): UseMutationResult<
  PlaceAndFixResponse,
  Error,
  PlaceAndFixRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PlaceAndFixResponse, Error, PlaceAndFixRequest>({
    mutationFn: (payload) =>
      fetcher<PlaceAndFixResponse>(PLACE_AND_FIX_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['visits'] });
      void qc.invalidateQueries({ queryKey: ['patients'] });
      void qc.invalidateQueries({ queryKey: ['courses'] });
      void qc.invalidateQueries({ queryKey: ['patient-fixed-visits'] });
    },
  });
}
