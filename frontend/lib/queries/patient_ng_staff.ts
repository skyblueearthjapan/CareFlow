/**
 * TanStack Query hooks for NG スタッフ (患者 × スタッフ割当禁止) API.
 *
 * Endpoints (設計書 `docs/plans/patient-ng-staff-design.md` §4-1):
 *   GET    /api/v1/patients/{patient_id}/ng-staff              (全ロール閲覧可)
 *   PUT    /api/v1/patients/{patient_id}/ng-staff/{staff_id}   body {note} (admin)
 *   DELETE /api/v1/patients/{patient_id}/ng-staff/{staff_id}   (admin)
 *
 * queryKey は `['patients', patientId, 'ng-staff']`。 PATIENTS_KEY prefix に
 * ぶら下げることで `useUpdatePatient` 等の患者 invalidate に相乗りする
 * (= `patient_fixed_visits.ts` の流儀)。
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
import { patientNgStaffListSchema, type PatientNgStaffRead } from '@/lib/schemas/patient_ng_staff';

// ─── Query key factory ───────────────────────────────────────────────────────

export const NG_STAFF_KEY = (patientId: string) => ['patients', patientId, 'ng-staff'] as const;

// ─── Auth helper ─────────────────────────────────────────────────────────────

function authPair(session: ReturnType<typeof useSession>['data']): {
  accessToken: string | null;
  refreshToken: string | null;
} {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

// ─── GET /api/v1/patients/{patient_id}/ng-staff ──────────────────────────────

export function useNgStaffList(
  patientId: string | null | undefined,
): UseQueryResult<PatientNgStaffRead[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<PatientNgStaffRead[], Error>({
    queryKey: NG_STAFF_KEY(patientId ?? ''),
    enabled: status === 'authenticated' && !!patientId,
    queryFn: async () => {
      const raw = await fetcher<unknown>(`/api/v1/patients/${patientId}/ng-staff`, {
        accessToken,
        refreshToken,
      });
      return patientNgStaffListSchema.parse(raw);
    },
  });
}

// ─── PUT /api/v1/patients/{patient_id}/ng-staff/{staff_id} ───────────────────

export interface UpsertNgStaffVariables {
  patientId: string;
  staffId: string;
  /** 理由メモ (任意)。空文字は null に正規化して送る。 */
  note: string | null;
}

/** NG スタッフを 1 件 upsert する (追加 / メモ更新)。 */
export function useUpsertNgStaff(): UseMutationResult<unknown, Error, UpsertNgStaffVariables> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<unknown, Error, UpsertNgStaffVariables>({
    mutationFn: async ({ patientId, staffId, note }) =>
      fetcher(`/api/v1/patients/${patientId}/ng-staff/${staffId}`, {
        method: 'PUT',
        body: JSON.stringify({ note: note || null }),
        accessToken,
        refreshToken,
      }),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: NG_STAFF_KEY(vars.patientId) });
    },
  });
}

// ─── DELETE /api/v1/patients/{patient_id}/ng-staff/{staff_id} ────────────────

export interface DeleteNgStaffVariables {
  patientId: string;
  staffId: string;
}

/** NG スタッフ指定を 1 件解除する。 */
export function useDeleteNgStaff(): UseMutationResult<unknown, Error, DeleteNgStaffVariables> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<unknown, Error, DeleteNgStaffVariables>({
    mutationFn: async ({ patientId, staffId }) =>
      fetcher(`/api/v1/patients/${patientId}/ng-staff/${staffId}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      }),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: NG_STAFF_KEY(vars.patientId) });
    },
  });
}
