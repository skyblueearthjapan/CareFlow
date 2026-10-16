/**
 * TanStack Query hooks for 登録の位置のずれ (location-drift-align-2026-10-17)。
 *
 * Endpoints (backend `app/api/v1/patient_location.py`)
 * ────────────────────────────────────────────
 *   GET  /api/v1/patients/{id}/location-drift   ずれ (無ければ null)
 *   POST /api/v1/patients/{id}/location/align   サーバーで計算し直して打刻の場所に合わせる
 *   POST /api/v1/patients/{id}/location/revert  直近に合わせた位置を元に戻す
 *
 * admin のみ呼び出せる (BE が 403)。合わせた / 戻した後はモニターと患者を再取得する。
 */
'use client';

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import type { LocationDrift } from '@/lib/schemas/monitor';

/** 合わせた / 戻した後の登録座標。 */
export interface PatientLocation {
  lat: number | null;
  lng: number | null;
}

const LOCATION_DRIFT_KEY = ['patient-location-drift'] as const;

function authPair(session: ReturnType<typeof useSession>['data']): {
  accessToken: string | null;
  refreshToken: string | null;
} {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/** GET /api/v1/patients/{id}/location-drift — ずれ (無ければ null)。 */
export function usePatientLocationDrift(
  id: string | null | undefined,
  options: { enabled?: boolean } = {},
): UseQueryResult<LocationDrift | null, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const gate = options.enabled ?? true;

  return useQuery<LocationDrift | null, Error>({
    queryKey: [...LOCATION_DRIFT_KEY, id],
    enabled: status === 'authenticated' && !!id && gate,
    queryFn: () =>
      fetcher<LocationDrift | null>(`/api/v1/patients/${id}/location-drift`, {
        accessToken,
        refreshToken,
      }),
  });
}

/**
 * POST /location/align・/location/revert。済んだらモニター・患者・ずれを再取得する。
 * どちらも本文なし (座標はサーバーが決める)。
 */
function useLocationMutation(
  id: string,
  action: 'align' | 'revert',
): UseMutationResult<PatientLocation, Error, void> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PatientLocation, Error, void>({
    mutationFn: () =>
      fetcher<PatientLocation>(`/api/v1/patients/${id}/location/${action}`, {
        method: 'POST',
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['monitor'] });
      void qc.invalidateQueries({ queryKey: ['patients'] });
      void qc.invalidateQueries({ queryKey: [...LOCATION_DRIFT_KEY, id] });
    },
  });
}

/** 登録の位置を打刻の場所に合わせる。ずれが無くなっていたら BE が 409。 */
export function useAlignPatientLocation(id: string) {
  return useLocationMutation(id, 'align');
}

/** 直近に合わせた登録の位置を元に戻す。戻せる記録が無ければ BE が 404。 */
export function useRevertPatientLocation(id: string) {
  return useLocationMutation(id, 'revert');
}
