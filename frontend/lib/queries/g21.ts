/**
 * TanStack Query hooks for Phase G-21 endpoints.
 *
 * - 同住所候補 / 紐付け (blocked / preferred / required)
 * - Office feature flag (rakusuke_agent 等)
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
import { IMPROVEMENT_SUGGESTIONS_KEY } from '@/lib/queries/improvementSuggestions';
import type {
  OfficeFeatureFlag,
  OfficeFeatureFlagUpsert,
  OfficeFeatureKey,
  PatientSameAddressLink,
  PatientSameAddressLinkCreate,
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

/**
 * 同住所リンクの変更で古くなる **提案系** キャッシュ (NG スタッフ側と同一の欠陥).
 * ペア (同時刻・別コース) の成立可否が変わるため、改善提案 / プール投入提案 /
 * 現場ボードは必ず再計算させる。propose-slots・unblock は mutation なので対象外。
 */
function invalidateSameAddressDependents(qc: ReturnType<typeof useQueryClient>): void {
  void qc.invalidateQueries({ queryKey: [IMPROVEMENT_SUGGESTIONS_KEY] });
  void qc.invalidateQueries({ queryKey: ['diff-add'] });
  void qc.invalidateQueries({ queryKey: ['field-board'] });
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
      invalidateSameAddressDependents(qc);
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
      invalidateSameAddressDependents(qc);
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
