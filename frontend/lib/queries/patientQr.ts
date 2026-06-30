/**
 * TanStack Query hooks for 患者 QR 発行 / 再発行 (Phase 5-1).
 *
 * Endpoints (backend `app/api/v1/patient_qr.py`)
 * ────────────────────────────────────────────
 *   GET  /api/v1/patients/{id}/qr            未発行なら遅延生成し {token, version}
 *   POST /api/v1/patients/{id}/qr/regenerate 旧トークン失効 + version+1
 *
 * admin / manager のみ呼び出せる (BE が 403)。URL は呼び出し側で
 * `${window.location.origin}/q/${token}` を組む。
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

export interface PatientQr {
  token: string;
  version: number;
}

const PATIENT_QR_KEY = ['patient-qr'] as const;

function authPair(session: ReturnType<typeof useSession>['data']): {
  accessToken: string | null;
  refreshToken: string | null;
} {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/** GET /api/v1/patients/{id}/qr — 取得 (未発行なら BE が遅延生成). */
export function usePatientQr(
  id: string | null | undefined,
  options: { enabled?: boolean } = {},
): UseQueryResult<PatientQr, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const gate = options.enabled ?? true;

  return useQuery<PatientQr, Error>({
    queryKey: [...PATIENT_QR_KEY, id],
    enabled: status === 'authenticated' && !!id && gate,
    queryFn: () => fetcher<PatientQr>(`/api/v1/patients/${id}/qr`, { accessToken, refreshToken }),
  });
}

/** POST /api/v1/patients/{id}/qr/regenerate — 再発行 (旧トークン失効 + version+1). */
export function useRegeneratePatientQr(id: string): UseMutationResult<PatientQr, Error, void> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PatientQr, Error, void>({
    mutationFn: () =>
      fetcher<PatientQr>(`/api/v1/patients/${id}/qr/regenerate`, {
        method: 'POST',
        accessToken,
        refreshToken,
      }),
    onSuccess: (data) => {
      qc.setQueryData([...PATIENT_QR_KEY, id], data);
    },
  });
}
