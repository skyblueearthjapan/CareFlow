'use client';

/**
 * TanStack Query hooks for /api/v1/acceptance-calendar — W15-FE (Wave 15 Phase 3).
 *
 * Endpoints (W15-BE1):
 *   GET /api/v1/acceptance-calendar?office_id=...   全エントリ取得
 *   PUT /api/v1/acceptance-calendar                  bulk upsert (admin/manager)
 *
 * 受入目安レイヤー (○/△/×) を統合スケジュール画面のセル背景に適用する。
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
import type {
  AcceptanceCalendarBulkUpsert,
  AcceptanceCalendarRead,
} from '@/lib/schemas/v2/acceptance';

const ACCEPTANCE_PATH = '/api/v1/acceptance-calendar';
const ACCEPTANCE_KEY = ['acceptance-calendar'] as const;

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

export interface UseAcceptanceCalendarParams {
  office_id?: string | null;
  enabled?: boolean;
}

/**
 * GET /api/v1/acceptance-calendar?office_id=...
 * office_id 必須。空の場合 disabled。
 */
export function useAcceptanceCalendar(
  params: UseAcceptanceCalendarParams,
): UseQueryResult<AcceptanceCalendarRead[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const officeId = params.office_id ?? null;

  return useQuery<AcceptanceCalendarRead[], Error>({
    queryKey: [...ACCEPTANCE_KEY, 'list', officeId ?? '__none__'],
    enabled:
      status === 'authenticated' &&
      Boolean(officeId) &&
      (params.enabled === undefined || params.enabled),
    queryFn: () =>
      fetcher<AcceptanceCalendarRead[]>(
        `${ACCEPTANCE_PATH}?office_id=${encodeURIComponent(officeId!)}`,
        { accessToken, refreshToken },
      ),
  });
}

export function useUpsertAcceptanceCalendar(): UseMutationResult<
  AcceptanceCalendarRead[],
  Error,
  AcceptanceCalendarBulkUpsert
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<AcceptanceCalendarRead[], Error, AcceptanceCalendarBulkUpsert>({
    mutationFn: (payload) =>
      fetcher<AcceptanceCalendarRead[]>(ACCEPTANCE_PATH, {
        method: 'PUT',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ACCEPTANCE_KEY });
    },
  });
}
