/**
 * TanStack Query hooks for Patient Fixed Visits (固定枠) API (W9-FE1).
 *
 * Endpoints (backend `app/api/v1/patients/{id}/fixed-visits`)
 * ────────────────────────────────────────────────────────────
 *   GET    /api/v1/patients/{id}/fixed-visits[?mode=normal|special]
 *   PUT    /api/v1/patients/{id}/fixed-visits  body: PatientFixedVisitsBulkPut
 *   DELETE /api/v1/patients/{id}/fixed-visits?mode=normal|special
 *   POST   /api/v1/patients/{id}/fixed-visits/from-week  (Phase 2 endpoint)
 *
 * Phase 2 の from-week は W9-BE2 で実装予定。本 Phase では GET/PUT/DELETE を使用。
 * from-week が未デプロイの状態では POST が 404 になる可能性があるため、
 * エラー時は "Phase 2 がデプロイされていません" のメッセージを返す。
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
  PatientFixedVisitMode,
  PatientFixedVisitsBulkPut,
  PatientFixedVisitV2Read,
} from '@/lib/schemas/v2/patient_fixed_visit';

// ─── Query key factory ───────────────────────────────────────────────────────

export const FIXED_VISITS_KEY = (patientId: string, mode?: PatientFixedVisitMode) =>
  ['patients', patientId, 'fixed-visits', mode ?? 'all'] as const;

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

// ─── GET /api/v1/patients/{id}/fixed-visits ──────────────────────────────────

/**
 * 固定枠一覧を取得する。
 * mode を省略するとすべて (normal + special) を返すが、
 * タブ切替ごとに呼ぶ場合は mode を明示することでキャッシュを分離できる。
 */
export function useFixedVisits(
  patientId: string,
  mode?: PatientFixedVisitMode,
): UseQueryResult<PatientFixedVisitV2Read[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  const qs = mode ? `?mode=${mode}` : '';

  return useQuery<PatientFixedVisitV2Read[], Error>({
    queryKey: FIXED_VISITS_KEY(patientId, mode),
    enabled: status === 'authenticated' && !!patientId,
    queryFn: () =>
      fetcher<PatientFixedVisitV2Read[]>(`/api/v1/patients/${patientId}/fixed-visits${qs}`, {
        accessToken,
        refreshToken,
      }),
  });
}

// ─── PUT /api/v1/patients/{id}/fixed-visits ──────────────────────────────────

/**
 * 固定枠を一括上書き (mode + items)。
 * 成功後に関連するクエリを invalidate する。
 */
export function useUpdateFixedVisits(
  patientId: string,
): UseMutationResult<PatientFixedVisitV2Read[], Error, PatientFixedVisitsBulkPut> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PatientFixedVisitV2Read[], Error, PatientFixedVisitsBulkPut>({
    mutationFn: async (payload) =>
      fetcher<PatientFixedVisitV2Read[]>(`/api/v1/patients/${patientId}/fixed-visits`, {
        method: 'PUT',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: (_data, variables) => {
      // 当該モードのキャッシュを無効化
      void qc.invalidateQueries({
        queryKey: FIXED_VISITS_KEY(patientId, variables.mode),
      });
      // mode 省略キャッシュも無効化
      void qc.invalidateQueries({
        queryKey: FIXED_VISITS_KEY(patientId),
      });
    },
  });
}

// ─── DELETE /api/v1/patients/{id}/fixed-visits?mode=... ──────────────────────

/**
 * 指定 mode の固定枠を全削除する。
 */
export function useDeleteFixedVisits(
  patientId: string,
): UseMutationResult<void, Error, PatientFixedVisitMode> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<void, Error, PatientFixedVisitMode>({
    mutationFn: async (mode) => {
      await fetcher<void>(`/api/v1/patients/${patientId}/fixed-visits?mode=${mode}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    onSuccess: (_data, mode) => {
      void qc.invalidateQueries({
        queryKey: FIXED_VISITS_KEY(patientId, mode),
      });
      void qc.invalidateQueries({
        queryKey: FIXED_VISITS_KEY(patientId),
      });
    },
  });
}

// ─── POST /api/v1/patients/{id}/fixed-visits/from-week (Phase 2) ─────────────

export interface ApplyFromWeekParams {
  iso_year: number;
  iso_week: number;
  mode?: PatientFixedVisitMode;
}

/**
 * 直近の確定スケジュールから固定枠を生成する (Phase 2 W9-BE2 endpoint)。
 * Phase 2 がデプロイされていない場合は 404 になる可能性があるため、
 * エラーメッセージを "Phase 2 がデプロイされていません" に変換して投げる。
 */
export function useApplyFromWeek(
  patientId: string,
): UseMutationResult<PatientFixedVisitV2Read[], Error, ApplyFromWeekParams> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PatientFixedVisitV2Read[], Error, ApplyFromWeekParams>({
    mutationFn: async (params) => {
      try {
        return await fetcher<PatientFixedVisitV2Read[]>(
          `/api/v1/patients/${patientId}/fixed-visits/from-week`,
          {
            method: 'POST',
            body: JSON.stringify(params),
            accessToken,
            refreshToken,
          },
        );
      } catch (e) {
        // 404 = Phase 2 未デプロイ
        if (e instanceof Error && e.message.includes('404')) {
          throw new Error(
            'Phase 2 がデプロイされていません。from-week 機能は W9-BE2 のデプロイ後に利用できます。',
          );
        }
        throw e;
      }
    },
    onSuccess: (_data, variables) => {
      const mode = variables.mode;
      if (mode) {
        void qc.invalidateQueries({ queryKey: FIXED_VISITS_KEY(patientId, mode) });
      }
      void qc.invalidateQueries({ queryKey: FIXED_VISITS_KEY(patientId) });
    },
  });
}
