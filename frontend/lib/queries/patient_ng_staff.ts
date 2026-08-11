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
import { IMPROVEMENT_SUGGESTIONS_KEY } from '@/lib/queries/improvementSuggestions';
import {
  patientNgStaffListSchema,
  staffNgPatientListSchema,
  type PatientNgStaffRead,
  type StaffNgPatientRead,
} from '@/lib/schemas/patient_ng_staff';

// ─── Query key factory ───────────────────────────────────────────────────────

export const NG_STAFF_KEY = (patientId: string) => ['patients', patientId, 'ng-staff'] as const;

/** 逆引き (スタッフ詳細サマリ)。`STAFF_KEY` prefix にぶら下げる。 */
export const STAFF_NG_PATIENTS_KEY = (staffId: string) =>
  ['staff', staffId, 'ng-patients'] as const;

/**
 * NG 指定の変更で古くなる **提案系** キャッシュをまとめて失効させる。
 *
 * 2026-08-11 の欠陥: upsert/delete が当該患者の ng-staff キーしか無効化しておらず、
 *   - 改善提案 (staleTime 5 分) が NG 追加前の結果を返し続ける
 *   - プール投入提案 (diff-add) / 現場ボードも同様
 *   - 患者一覧の `ng_staff_count` バッジが増減しない
 *   - スタッフ詳細の逆引き (ng-patients) が更新されない
 * ため「NG を足したのに警告が出ない」ように見えていた。
 *
 * NOTE: propose-slots (PoolCandidateList の候補一覧) / unblock / scope-optimization は
 *   いずれも **mutation** (キャッシュを持たない on-demand 実行) のため無効化対象なし。
 *   再実行すれば必ず最新の NG 指定で計算される。
 */
function invalidateNgStaffDependents(
  qc: ReturnType<typeof useQueryClient>,
  vars: { patientId: string; staffId: string },
): void {
  void qc.invalidateQueries({ queryKey: NG_STAFF_KEY(vars.patientId) });
  // 逆引き (スタッフ詳細の「このスタッフを NG 指定している患者」).
  void qc.invalidateQueries({ queryKey: STAFF_NG_PATIENTS_KEY(vars.staffId) });
  // 患者一覧・詳細 (ng_staff_count バッジ = プールカードの ⛔ 表示元).
  void qc.invalidateQueries({ queryKey: ['patients'] });
  // 改善提案 (staleTime 5 分。ここを外さないと NG 追加が反映されない).
  void qc.invalidateQueries({ queryKey: [IMPROVEMENT_SUGGESTIONS_KEY] });
  // プール投入提案 (差分追加) と現場ボードの候補.
  void qc.invalidateQueries({ queryKey: ['diff-add'] });
  void qc.invalidateQueries({ queryKey: ['field-board'] });
}

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

// ─── GET /api/v1/staff/{staff_id}/ng-patients (逆引き) ───────────────────────

/**
 * 「このスタッフを NG 指定している患者」一覧 (閲覧専用・全ロール可)。
 * スタッフ詳細のサマリカードで使う (設計書 §8-2 Phase 2)。
 */
export function useStaffNgPatients(
  staffId: string | null | undefined,
): UseQueryResult<StaffNgPatientRead[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<StaffNgPatientRead[], Error>({
    queryKey: STAFF_NG_PATIENTS_KEY(staffId ?? ''),
    enabled: status === 'authenticated' && !!staffId,
    queryFn: async () => {
      const raw = await fetcher<unknown>(`/api/v1/staff/${staffId}/ng-patients`, {
        accessToken,
        refreshToken,
      });
      return staffNgPatientListSchema.parse(raw);
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
      invalidateNgStaffDependents(qc, vars);
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
      invalidateNgStaffDependents(qc, vars);
    },
  });
}
