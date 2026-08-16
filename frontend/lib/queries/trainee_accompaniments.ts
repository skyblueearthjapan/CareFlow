'use client';

/**
 * TanStack Query hooks for 同行 (accompaniment).
 *
 * API 契約 (`general-accompaniment-design.md` §3-8 で一般化パスへ移行。旧
 * `/trainee-accompaniments` 系は BE 側の互換エイリアスとして残るが FE は使わない):
 *   GET  /api/v1/accompaniments?iso_year=&iso_week=[&trainee_staff_id=]
 *   PUT  /api/v1/accompaniments                         (週単位の一括置換)
 *   GET  /api/v1/accompaniment-defaults?trainee_staff_id=
 *   PUT  /api/v1/accompaniment-defaults                 (全置換)
 *   GET  /api/v1/accompaniments/course-guard?trainee_staff_id=
 *   DELETE /api/v1/accompaniments/future?trainee_staff_id=
 *
 * RBAC: GET=全ロール / PUT=admin・manager (staff は閲覧のみ)。
 * PUT の 422 は 2 系統:
 *   - 時間重複 `{ detail: { code:'accompaniment_overlap', conflicts:[...] } }`
 *     (旧形 `{ detail: { message, overlaps } }` も互換で読む)
 *   - NG/性別 `{ detail: { code:'constraint_confirmation_required', warnings:[...] } }`
 *     → 確認ダイアログ → `acknowledge_constraint_warnings:true` で再送。
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
  type UseMutationResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  traineeAccompanimentsResponseSchema,
  type TraineeAccompanimentDefaultRead,
  type TraineeAccompanimentDefaultsPut,
  type TraineeAccompanimentFutureDeleteResponse,
  type TraineeAccompanimentItem,
  type TraineeAccompanimentsPut,
  type TraineeCourseGuardResponse,
} from '@/lib/schemas/trainee_accompaniment';

const BASE = '/api/v1/accompaniments';
const DEFAULTS_BASE = '/api/v1/accompaniment-defaults';

// ---------------------------------------------------------------------------
// Auth helper (mirrors pattern in lib/queries/staff.ts)
// ---------------------------------------------------------------------------

function useAuthTokens() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    isAuthenticated: status === 'authenticated',
  };
}

// ---------------------------------------------------------------------------
// Cache key factories
// ---------------------------------------------------------------------------

export const traineeAccompanimentsKey = (
  isoYear: number,
  isoWeek: number,
  traineeStaffId?: string | null,
) => ['trainee-accompaniments', isoYear, isoWeek, traineeStaffId ?? '__all__'] as const;

export const traineeAccompanimentDefaultsKey = (traineeStaffId: string | null | undefined) =>
  ['trainee-accompaniment-defaults', traineeStaffId ?? '__none__'] as const;

export const traineeCourseGuardKey = (traineeStaffId: string | null | undefined) =>
  ['trainee-course-guard', traineeStaffId ?? '__none__'] as const;

// ---------------------------------------------------------------------------
// GET /trainee-accompaniments?iso_year=&iso_week=[&trainee_staff_id=]
// ---------------------------------------------------------------------------

export interface UseTraineeAccompanimentsParams {
  isoYear: number;
  isoWeek: number;
  /** 省略時は週の全新人の同行リンクを返す (常時表示 §7.2 用)。 */
  traineeStaffId?: string | null;
  enabled?: boolean;
}

export function useTraineeAccompaniments({
  isoYear,
  isoWeek,
  traineeStaffId,
  enabled = true,
}: UseTraineeAccompanimentsParams) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();

  return useQuery<TraineeAccompanimentItem[], Error>({
    queryKey: traineeAccompanimentsKey(isoYear, isoWeek, traineeStaffId),
    queryFn: async () => {
      const qs = new URLSearchParams({
        iso_year: String(isoYear),
        iso_week: String(isoWeek),
      });
      if (traineeStaffId) qs.set('trainee_staff_id', traineeStaffId);
      const raw = await fetcher<unknown>(`${BASE}?${qs.toString()}`, {
        accessToken,
        refreshToken,
      });
      return traineeAccompanimentsResponseSchema.parse(raw).items;
    },
    enabled: isAuthenticated && enabled,
  });
}

// ---------------------------------------------------------------------------
// PUT /trainee-accompaniments  (週単位の一括置換・確定操作)
// ---------------------------------------------------------------------------

type UpdateTraineeAccompanimentsOptions = UseMutationOptions<
  TraineeAccompanimentItem[],
  Error,
  TraineeAccompanimentsPut
>;

export function useUpdateTraineeAccompaniments(
  options: UpdateTraineeAccompanimentsOptions = {},
): UseMutationResult<TraineeAccompanimentItem[], Error, TraineeAccompanimentsPut> {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<TraineeAccompanimentItem[], Error, TraineeAccompanimentsPut>({
    mutationFn: async (payload) => {
      const raw = await fetcher<unknown>(BASE, {
        method: 'PUT',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      // 成功時レスポンスは GET と同形。
      return traineeAccompanimentsResponseSchema.parse(raw).items;
    },
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      // その新人ぶん + 週全体 (常時表示) の両方を invalidate。
      void qc.invalidateQueries({ queryKey: ['trainee-accompaniments'] });
      void qc.invalidateQueries({
        queryKey: traineeAccompanimentDefaultsKey(variables.trainee_staff_id),
      });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

// ---------------------------------------------------------------------------
// GET /trainee-accompaniment-defaults?trainee_staff_id=
// ---------------------------------------------------------------------------

export function useTraineeAccompanimentDefaults(traineeStaffId: string | null | undefined) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();

  return useQuery<TraineeAccompanimentDefaultRead[], Error>({
    queryKey: traineeAccompanimentDefaultsKey(traineeStaffId),
    queryFn: () => {
      if (!traineeStaffId) throw new Error('trainee_staff_id is required');
      const qs = new URLSearchParams({ trainee_staff_id: traineeStaffId });
      return fetcher<TraineeAccompanimentDefaultRead[]>(`${DEFAULTS_BASE}?${qs.toString()}`, {
        accessToken,
        refreshToken,
      });
    },
    enabled: isAuthenticated && !!traineeStaffId,
  });
}

// ---------------------------------------------------------------------------
// PUT /trainee-accompaniment-defaults  (全置換)
// ---------------------------------------------------------------------------

type UpdateDefaultsOptions = UseMutationOptions<
  TraineeAccompanimentDefaultRead[],
  Error,
  TraineeAccompanimentDefaultsPut
>;

export function useUpdateTraineeAccompanimentDefaults(
  options: UpdateDefaultsOptions = {},
): UseMutationResult<TraineeAccompanimentDefaultRead[], Error, TraineeAccompanimentDefaultsPut> {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<TraineeAccompanimentDefaultRead[], Error, TraineeAccompanimentDefaultsPut>({
    mutationFn: (payload) =>
      fetcher<TraineeAccompanimentDefaultRead[]>(DEFAULTS_BASE, {
        method: 'PUT',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({
        queryKey: traineeAccompanimentDefaultsKey(variables.trainee_staff_id),
      });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

// ---------------------------------------------------------------------------
// GET /trainee-accompaniments/course-guard?trainee_staff_id=  (§8-4 警告用)
// ---------------------------------------------------------------------------

/**
 * 新人が「今週以降のコース担当」に残っているかを返す。
 * is_trainee を ON にする際の警告表示に使う (自動解除はしない・警告主義)。
 */
export function useTraineeCourseGuard(traineeStaffId: string | null | undefined, enabled = true) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();

  return useQuery<TraineeCourseGuardResponse, Error>({
    queryKey: traineeCourseGuardKey(traineeStaffId),
    queryFn: () => {
      if (!traineeStaffId) throw new Error('trainee_staff_id is required');
      const qs = new URLSearchParams({ trainee_staff_id: traineeStaffId });
      return fetcher<TraineeCourseGuardResponse>(`${BASE}/course-guard?${qs.toString()}`, {
        accessToken,
        refreshToken,
      });
    },
    enabled: isAuthenticated && enabled && !!traineeStaffId,
  });
}

// ---------------------------------------------------------------------------
// DELETE /trainee-accompaniments/future?trainee_staff_id=  (§7.5)
// ---------------------------------------------------------------------------

type DeleteFutureOptions = UseMutationOptions<
  TraineeAccompanimentFutureDeleteResponse,
  Error,
  void
>;

/**
 * is_trainee OFF の確認ダイアログから呼ぶ。今週以降の同行リンク + 毎週の既定を
 * 一括削除する (過去週は実績履歴として残す・冪等)。
 */
export function useDeleteTraineeAccompanimentsFuture(
  traineeStaffId: string,
  options: DeleteFutureOptions = {},
): UseMutationResult<TraineeAccompanimentFutureDeleteResponse, Error, void> {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<TraineeAccompanimentFutureDeleteResponse, Error, void>({
    mutationFn: () => {
      const qs = new URLSearchParams({ trainee_staff_id: traineeStaffId });
      return fetcher<TraineeAccompanimentFutureDeleteResponse>(`${BASE}/future?${qs.toString()}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: ['trainee-accompaniments'] });
      void qc.invalidateQueries({
        queryKey: traineeAccompanimentDefaultsKey(traineeStaffId),
      });
      void qc.invalidateQueries({ queryKey: traineeCourseGuardKey(traineeStaffId) });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}
