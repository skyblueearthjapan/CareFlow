/**
 * TanStack Query hooks for Phase G-21 endpoints.
 *
 * - 完全固定 PFV (pin) toggle / bulk
 * - 同住所候補 / 紐付け (blocked / preferred / required)
 * - Office feature flag (g21_new_algorithm 等)
 *
 * 成功時は関連クエリ (patients / fixed-visits / courses / visits) を
 * invalidate して下流の UI を再フェッチさせる.
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
import type {
  OfficeFeatureFlag,
  OfficeFeatureFlagUpsert,
  OfficeFeatureKey,
  PatientSameAddressLink,
  PatientSameAddressLinkCreate,
  PfvPinBulkItem,
  SameAddressCandidate,
} from '@/lib/schemas/g21';

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

// ─── Query key factories ─────────────────────────────────────────────────────

export const SAME_ADDRESS_CANDIDATES_KEY = (patientId: string) =>
  ['patients', patientId, 'same-address-candidates'] as const;

export const SAME_ADDRESS_LINKS_KEY = (patientId?: string) =>
  ['patient-same-address-links', patientId ?? 'all'] as const;

export const OFFICE_FEATURE_FLAGS_KEY = (featureKey?: OfficeFeatureKey) =>
  ['office-feature-flags', featureKey ?? 'all'] as const;

// ─── PATCH /api/v1/patients/fixed-visits/{pfv_id}/pin ────────────────────────

/**
 * Phase G-21 T4 reviewer M4: `patientId` を required にする.
 * 当該 PFV の fixed-visits cache を必ず invalidate するため、呼び出し側は
 * 必ず patient_id を渡す責任を負う (= 「invalidate 漏れ → 古いキャッシュで
 * 🔒 状態が反映されない」事故を防ぐ).
 */
export interface TogglePfvPinVariables {
  pfvId: string;
  isPinned: boolean;
  /** 該当患者の fixed-visits cache を refetch するために必須. */
  patientId: string;
}

/**
 * 単一 PFV の is_pinned を切り替える.
 * 成功時に patient の fixed-visits / courses / visits cache を無効化.
 */
export function useTogglePfvPin(): UseMutationResult<unknown, Error, TogglePfvPinVariables> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<unknown, Error, TogglePfvPinVariables>({
    mutationFn: async ({ pfvId, isPinned }) =>
      fetcher(`/api/v1/patients/fixed-visits/${pfvId}/pin`, {
        method: 'PATCH',
        body: JSON.stringify({ is_pinned: isPinned }),
        accessToken,
        refreshToken,
      }),
    onSuccess: (_data, vars) => {
      // 患者単位の fixed-visits cache + 全体の courses/visits を refresh.
      void qc.invalidateQueries({
        queryKey: ['patients', vars.patientId, 'fixed-visits'],
      });
      void qc.invalidateQueries({ queryKey: ['courses'] });
      void qc.invalidateQueries({ queryKey: ['visits'] });
    },
  });
}

// ─── POST /api/v1/patients/fixed-visits/pin/bulk ─────────────────────────────

/**
 * bulk pin/unpin.
 * BE は POST で {pfv_id, is_pinned}[] を受け取る.
 */
export function useBulkPinPfvs(): UseMutationResult<unknown, Error, PfvPinBulkItem[]> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<unknown, Error, PfvPinBulkItem[]>({
    mutationFn: async (items) =>
      fetcher('/api/v1/patients/fixed-visits/pin/bulk', {
        method: 'POST',
        body: JSON.stringify(items),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['patients'] });
      void qc.invalidateQueries({ queryKey: ['courses'] });
      void qc.invalidateQueries({ queryKey: ['visits'] });
    },
  });
}

// ─── GET /api/v1/patients/{patient_id}/same-address-candidates ───────────────

export function useSameAddressCandidates(
  patientId: string | null | undefined,
): UseQueryResult<SameAddressCandidate[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<SameAddressCandidate[], Error>({
    queryKey: SAME_ADDRESS_CANDIDATES_KEY(patientId ?? ''),
    enabled: status === 'authenticated' && !!patientId,
    queryFn: () =>
      fetcher<SameAddressCandidate[]>(`/api/v1/patients/${patientId}/same-address-candidates`, {
        accessToken,
        refreshToken,
      }),
  });
}

// ─── POST /api/v1/patient-same-address-links ─────────────────────────────────

/**
 * 紐付けを upsert する (BE 側で pair_mode を更新 or 新規作成).
 * 成功時に candidates cache (両 patient_id 分) と links cache を無効化.
 */
export function useSetSameAddressLink(): UseMutationResult<
  unknown,
  Error,
  PatientSameAddressLinkCreate
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<unknown, Error, PatientSameAddressLinkCreate>({
    mutationFn: async (payload) =>
      fetcher('/api/v1/patient-same-address-links', {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: SAME_ADDRESS_CANDIDATES_KEY(vars.patient_a_id) });
      void qc.invalidateQueries({ queryKey: SAME_ADDRESS_CANDIDATES_KEY(vars.patient_b_id) });
      // Phase G-21 T4 reviewer H1: prefix match 暗黙挙動に依存させず exact:false を明示.
      // ['patient-same-address-links', '<patientId>' | 'all'] を網羅的に無効化する.
      void qc.invalidateQueries({ queryKey: ['patient-same-address-links'], exact: false });
    },
  });
}

// ─── DELETE /api/v1/patient-same-address-links/{a}/{b} ───────────────────────

export interface DeleteSameAddressLinkVariables {
  patient_a_id: string;
  patient_b_id: string;
}

export function useDeleteSameAddressLink(): UseMutationResult<
  unknown,
  Error,
  DeleteSameAddressLinkVariables
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<unknown, Error, DeleteSameAddressLinkVariables>({
    mutationFn: async ({ patient_a_id, patient_b_id }) =>
      fetcher(`/api/v1/patient-same-address-links/${patient_a_id}/${patient_b_id}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      }),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: SAME_ADDRESS_CANDIDATES_KEY(vars.patient_a_id) });
      void qc.invalidateQueries({ queryKey: SAME_ADDRESS_CANDIDATES_KEY(vars.patient_b_id) });
      // Phase G-21 T4 reviewer H1: prefix match 暗黙挙動に依存させず exact:false を明示.
      void qc.invalidateQueries({ queryKey: ['patient-same-address-links'], exact: false });
    },
  });
}

// ─── GET /api/v1/patient-same-address-links ──────────────────────────────────

export function useSameAddressLinks(
  patientId?: string,
): UseQueryResult<PatientSameAddressLink[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<PatientSameAddressLink[], Error>({
    queryKey: SAME_ADDRESS_LINKS_KEY(patientId),
    enabled: status === 'authenticated',
    queryFn: () => {
      const qs = patientId ? `?patient_id=${encodeURIComponent(patientId)}` : '';
      return fetcher<PatientSameAddressLink[]>(`/api/v1/patient-same-address-links${qs}`, {
        accessToken,
        refreshToken,
      });
    },
  });
}

// ─── GET /api/v1/office-feature-flags ────────────────────────────────────────

export function useOfficeFeatureFlags(
  featureKey?: OfficeFeatureKey,
): UseQueryResult<OfficeFeatureFlag[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<OfficeFeatureFlag[], Error>({
    queryKey: OFFICE_FEATURE_FLAGS_KEY(featureKey),
    enabled: status === 'authenticated',
    queryFn: () => {
      const qs = featureKey ? `?feature_key=${encodeURIComponent(featureKey)}` : '';
      return fetcher<OfficeFeatureFlag[]>(`/api/v1/office-feature-flags${qs}`, {
        accessToken,
        refreshToken,
      });
    },
  });
}

// ─── POST /api/v1/office-feature-flags ───────────────────────────────────────

export function useSetOfficeFeatureFlag(): UseMutationResult<
  unknown,
  Error,
  OfficeFeatureFlagUpsert
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<unknown, Error, OfficeFeatureFlagUpsert>({
    mutationFn: async (payload) =>
      fetcher('/api/v1/office-feature-flags', {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['office-feature-flags'] });
    },
  });
}

// ─── DELETE /api/v1/office-feature-flags/{office_id}/{feature_key} ───────────

export interface DeleteOfficeFeatureFlagVariables {
  office_id: string;
  feature_key: OfficeFeatureKey;
}

export function useDeleteOfficeFeatureFlag(): UseMutationResult<
  unknown,
  Error,
  DeleteOfficeFeatureFlagVariables
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<unknown, Error, DeleteOfficeFeatureFlagVariables>({
    mutationFn: async ({ office_id, feature_key }) =>
      fetcher(`/api/v1/office-feature-flags/${office_id}/${feature_key}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['office-feature-flags'] });
    },
  });
}
