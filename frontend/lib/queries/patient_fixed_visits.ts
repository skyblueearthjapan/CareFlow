/**
 * TanStack Query hooks for Patient Fixed Visits (固定枠) API (W9-FE1 / W9-FE2).
 *
 * Endpoints (backend `app/api/v1/patients/{id}/fixed-visits`)
 * ────────────────────────────────────────────────────────────
 *   GET    /api/v1/patients/{id}/fixed-visits[?mode=normal|special]
 *   PUT    /api/v1/patients/{id}/fixed-visits  body: PatientFixedVisitsBulkPut
 *   DELETE /api/v1/patients/{id}/fixed-visits?mode=normal|special
 *   POST   /api/v1/patients/{id}/fixed-visits/from-week  (Phase 2 endpoint)
 *   POST   /api/v1/patients/fixed-visits/from-week-bulk  (Phase 4 W9-FE2 endpoint)
 *
 * Phase 2 の from-week は W9-BE2 で実装済。本 Phase では GET/PUT/DELETE を使用。
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
import { toast } from 'sonner';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  parsePatientFixedVisitsBulkPutResponse,
  pfvCourseLoadResponseSchema,
  pfvValidateResponseSchema,
  type PfvCourseLoadResponse,
  type PfvValidateResponse,
  type PatientFixedVisitMode,
  type PatientFixedVisitsBulkPut,
  type PatientFixedVisitsBulkPutResponse,
  type PatientFixedVisitWarning,
  type PatientFixedVisitV2Read,
} from '@/lib/schemas/v2/patient_fixed_visit';

// ─── PUT fixed-visits 警告トースト (共有) ─────────────────────────────────────

/**
 * P0-2 Commit 3: PUT /fixed-visits レスポンスの再検証 warnings を toast 表示する共有関数。
 * 採用系 (PoolCandidateList / ProposeNewModal) と手動編集系 (PatientFixedVisitsPanel) で
 * 同一の表示規則を使う。多数の場合は最初の 3 件 + 「他 N 件」に要約する。
 * warnings が空 / undefined のときは何も出さない (成功パスの挙動不変)。
 */
export function toastFixedVisitWarnings(
  warnings: readonly PatientFixedVisitWarning[] | undefined,
): void {
  if (!warnings || warnings.length === 0) return;
  const shown = warnings.slice(0, 3);
  for (const w of shown) toast.warning(w.message);
  if (warnings.length > shown.length) {
    toast.warning(`他 ${warnings.length - shown.length} 件の警告があります`);
  }
}

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

// ─── 案Z (PO 決定 2026-08-09): dry-run 検証 + コース負荷 ─────────────────────

/** 入力中のライブ検査 (保存しない)。PUT と同じ再検証カーネルを dry-run で叩く。 */
export function useValidateFixedVisits(
  patientId: string,
): UseMutationResult<
  PfvValidateResponse,
  Error,
  { mode: PatientFixedVisitMode; items: unknown[] }
> {
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useMutation({
    mutationFn: async (payload) => {
      const raw = await fetcher<unknown>(`/api/v1/patients/${patientId}/fixed-visits/validate`, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return pfvValidateResponseSchema.parse(raw);
    },
  });
}

/** コースセレクトの空き表示用: (曜日×コース) の他患者負荷 + 容量定数。 */
export function useFixedVisitsCourseLoad(
  patientId: string,
  mode: PatientFixedVisitMode,
  opts?: { enabled?: boolean },
): UseQueryResult<PfvCourseLoadResponse, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useQuery({
    queryKey: [...FIXED_VISITS_KEY(patientId, mode), 'course-load'],
    enabled: status === 'authenticated' && !!patientId && (opts?.enabled ?? true),
    queryFn: async () => {
      const raw = await fetcher<unknown>(
        `/api/v1/patients/${patientId}/fixed-visits/course-load?mode=${mode}`,
        { accessToken, refreshToken },
      );
      return pfvCourseLoadResponseSchema.parse(raw);
    },
  });
}

// ─── PUT /api/v1/patients/{id}/fixed-visits ──────────────────────────────────

/**
 * 固定枠を一括上書き (mode + items)。
 * 成功後に関連するクエリを invalidate する。
 */
export function useUpdateFixedVisits(
  patientId: string,
): UseMutationResult<PatientFixedVisitsBulkPutResponse, Error, PatientFixedVisitsBulkPut> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PatientFixedVisitsBulkPutResponse, Error, PatientFixedVisitsBulkPut>({
    // P0-2 Commit 1 で PUT レスポンスが `{items, warnings}` エンベロープ化された。
    // 寛容パースして呼出元 (PatientFixedVisitsPanel handleSave) が warnings を表示できるようにする。
    mutationFn: async (payload) => {
      const raw = await fetcher<unknown>(`/api/v1/patients/${patientId}/fixed-visits`, {
        method: 'PUT',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return parsePatientFixedVisitsBulkPutResponse(raw);
    },
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

// ─── POST /api/v1/patients/fixed-visits/from-week-bulk (Phase 4 W9-FE2) ─────

export interface ApplyFromWeekBulkParams {
  iso_year: number;
  iso_week: number;
  mode?: PatientFixedVisitMode;
}

export interface ApplyFromWeekBulkResponse {
  updated_count: number;
}

/**
 * 全 active 患者の固定枠を、指定週のスケジュールから一括生成する (W9-FE2 endpoint)。
 * 成功時は toast で更新人数を表示する。
 */
export function useApplyFromWeekBulk(): UseMutationResult<
  ApplyFromWeekBulkResponse,
  Error,
  ApplyFromWeekBulkParams
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<ApplyFromWeekBulkResponse, Error, ApplyFromWeekBulkParams>({
    mutationFn: async (params) =>
      fetcher<ApplyFromWeekBulkResponse>('/api/v1/patients/fixed-visits/from-week-bulk', {
        method: 'POST',
        body: JSON.stringify(params),
        accessToken,
        refreshToken,
      }),
    onSuccess: (data) => {
      toast.success(`${data.updated_count} 名の患者の固定枠を更新しました`);
      // 全患者の fixed-visits キャッシュを無効化する。
      void qc.invalidateQueries({ queryKey: ['patients'] });
    },
  });
}
