'use client';

/**
 * TanStack Query hook for GET /api/v1/acceptance-matrix (P2 / FE).
 *
 * 拠点 × 曜日 × 時間帯の受け入れ可否 (○△×) を当週の実 Visit から自動算出して
 * 返す read-only API。auto_status / manual_status / effective_status を含む。
 */
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  acceptanceMatrixResponseSchema,
  type AcceptanceMatrixResponse,
} from '@/lib/schemas/v2/acceptance_matrix';

const ACCEPTANCE_MATRIX_PATH = '/api/v1/acceptance-matrix';
const ACCEPTANCE_MATRIX_KEY = ['acceptance-matrix'] as const;

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

export interface UseAcceptanceMatrixParams {
  isoYear: number;
  isoWeek: number;
  officeId?: string | null;
  serviceMinutes?: number;
  enabled?: boolean;
}

export function useAcceptanceMatrix(
  params: UseAcceptanceMatrixParams,
): UseQueryResult<AcceptanceMatrixResponse, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const { isoYear, isoWeek, officeId, serviceMinutes = 60, enabled } = params;

  return useQuery<AcceptanceMatrixResponse, Error>({
    queryKey: [
      ...ACCEPTANCE_MATRIX_KEY,
      { isoYear, isoWeek, officeId: officeId ?? null, serviceMinutes },
    ],
    enabled: status === 'authenticated' && (enabled === undefined || enabled),
    queryFn: async () => {
      const usp = new URLSearchParams({
        iso_year: String(isoYear),
        iso_week: String(isoWeek),
        service_minutes: String(serviceMinutes),
      });
      if (officeId) usp.set('office_id', officeId);
      const raw = await fetcher<unknown>(`${ACCEPTANCE_MATRIX_PATH}?${usp.toString()}`, {
        accessToken,
        refreshToken,
      });
      return acceptanceMatrixResponseSchema.parse(raw);
    },
  });
}
