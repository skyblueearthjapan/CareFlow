/**
 * TanStack Query hooks for 患者 QR 発行 / 再発行 (Phase 5-1).
 *
 * Endpoints (backend `app/api/v1/patient_qr.py`)
 * ────────────────────────────────────────────
 *   GET  /api/v1/patients/{id}/qr            未発行なら遅延生成し {token, version}
 *   GET  /api/v1/patients/{id}/qr/status     「QRの管理」の表示 (版・発行日・最後に読めた日・猶予)
 *   POST /api/v1/patients/{id}/qr/regenerate 旧トークン失効 + version+1 (本文 {reason})
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

/**
 * 再発行の理由 (設計 qr-manage-reissue-guard-2026-10-14 §2)。
 * damaged = 汚れた・読めない (古いシールは 7 日間使える) /
 * lost = なくした・misuse = 他人に使われた恐れ (古いシールはすぐ使えなくなる)。
 */
export type QrRegenerateReason = 'damaged' | 'lost' | 'misuse';

/** GET /qr/status の応答。日時は ISO 8601 (tz 付き)。 */
export interface PatientQrStatus {
  version: number;
  /** 直近の再発行の時刻 (null = 初回発行のまま)。 */
  issued_at: string | null;
  /** QR で記録された打刻の最新 1 件。 */
  last_scanned_at: string | null;
  last_scanned_staff_name: string | null;
  /** 猶予中の古い QR (第何版がいつまで使えるか)。 */
  grace: { version: number; valid_until: string }[];
}

const PATIENT_QR_KEY = ['patient-qr'] as const;
const PATIENT_QR_STATUS_KEY = ['patient-qr-status'] as const;

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

/** GET /api/v1/patients/{id}/qr/status — 「QRの管理」の表示. */
export function usePatientQrStatus(
  id: string | null | undefined,
  options: { enabled?: boolean } = {},
): UseQueryResult<PatientQrStatus, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const gate = options.enabled ?? true;

  return useQuery<PatientQrStatus, Error>({
    queryKey: [...PATIENT_QR_STATUS_KEY, id],
    enabled: status === 'authenticated' && !!id && gate,
    queryFn: () =>
      fetcher<PatientQrStatus>(`/api/v1/patients/${id}/qr/status`, {
        accessToken,
        refreshToken,
      }),
  });
}

/**
 * POST /api/v1/patients/{id}/qr/regenerate — 再発行 (旧トークン失効 + version+1).
 *
 * 理由を渡すと本文 `{reason}` を送る (damaged は古いシールに 7 日の猶予)。
 * 理由なしは本文なし = 古いシールは即時に無効 (従来どおり)。
 */
export function useRegeneratePatientQr(
  id: string,
): UseMutationResult<PatientQr, Error, QrRegenerateReason | void> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PatientQr, Error, QrRegenerateReason | void>({
    mutationFn: (reason) =>
      fetcher<PatientQr>(`/api/v1/patients/${id}/qr/regenerate`, {
        method: 'POST',
        accessToken,
        refreshToken,
        ...(reason ? { body: JSON.stringify({ reason }) } : {}),
      }),
    onSuccess: (data) => {
      qc.setQueryData([...PATIENT_QR_KEY, id], data);
      void qc.invalidateQueries({ queryKey: [...PATIENT_QR_STATUS_KEY, id] });
    },
  });
}
