'use client';

/**
 * TanStack Query hooks for 新人同行 (trainee accompaniment).
 *
 * 設計書 §6 の API 契約:
 *   GET  /api/v1/trainee-accompaniments?iso_year=&iso_week=[&trainee_staff_id=]
 *   PUT  /api/v1/trainee-accompaniments                         (週単位の一括置換)
 *   GET  /api/v1/trainee-accompaniment-defaults?trainee_staff_id=
 *   PUT  /api/v1/trainee-accompaniment-defaults                 (全置換)
 *
 * RBAC: GET=全ロール / PUT=admin・manager (staff は閲覧のみ)。
 * PUT の 422 (時間重複) は ApiError.body に `{ detail: { message, overlaps } }` を持つ。
 * 呼び出し側は `parseOverlapDetail(err.body)` で取り出して同じ警告 UI に流す (§7.1 二重防御)。
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
  type TraineeAccompanimentItem,
  type TraineeAccompanimentsPut,
} from '@/lib/schemas/trainee_accompaniment';

const BASE = '/api/v1/trainee-accompaniments';
const DEFAULTS_BASE = '/api/v1/trainee-accompaniment-defaults';

// ---------------------------------------------------------------------------
// Auth helper (mirrors pattern in lib/queries/staff_companion.ts)
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
) =>
  ['trainee-accompaniments', isoYear, isoWeek, traineeStaffId ?? '__all__'] as const;

export const traineeAccompanimentDefaultsKey = (traineeStaffId: string | null | undefined) =>
  ['trainee-accompaniment-defaults', traineeStaffId ?? '__none__'] as const;

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
): UseMutationResult<
  TraineeAccompanimentDefaultRead[],
  Error,
  TraineeAccompanimentDefaultsPut
> {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<
    TraineeAccompanimentDefaultRead[],
    Error,
    TraineeAccompanimentDefaultsPut
  >({
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
