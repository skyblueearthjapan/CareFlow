'use client';

/**
 * TanStack Query hook for GET /api/v1/acceptance-matrix (P2 / FE).
 *
 * 拠点 × 曜日 × 時間帯の受け入れ可否 (○△×) を当週の実 Visit から自動算出して
 * 返す read-only API。auto_status / manual_status / effective_status を含む。
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { toast } from 'sonner';

import { fetcher } from '@/lib/api/fetcher';
import type { AcceptanceStatus } from '@/lib/schemas/v2/acceptance';
import {
  acceptanceMatrixResponseSchema,
  type AcceptanceMatrixResponse,
} from '@/lib/schemas/v2/acceptance_matrix';

const ACCEPTANCE_MATRIX_PATH = '/api/v1/acceptance-matrix';
const ACCEPTANCE_MATRIX_KEY = ['acceptance-matrix'] as const;

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

export interface UseAcceptanceMatrixParams {
  isoYear: number;
  isoWeek: number;
  officeId?: string | null;
  serviceMinutes?: number;
  enabled?: boolean;
}

export function useAcceptanceMatrix(
  params: UseAcceptanceMatrixParams,
): UseQueryResult<AcceptanceMatrixResponse, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const { isoYear, isoWeek, officeId, serviceMinutes = 60, enabled } = params;

  return useQuery<AcceptanceMatrixResponse, Error>({
    queryKey: [
      ...ACCEPTANCE_MATRIX_KEY,
      { isoYear, isoWeek, officeId: officeId ?? null, serviceMinutes },
    ],
    enabled: status === 'authenticated' && (enabled === undefined || enabled),
    queryFn: async () => {
      const usp = new URLSearchParams({
        iso_year: String(isoYear),
        iso_week: String(isoWeek),
        service_minutes: String(serviceMinutes),
      });
      if (officeId) usp.set('office_id', officeId);
      const raw = await fetcher<unknown>(`${ACCEPTANCE_MATRIX_PATH}?${usp.toString()}`, {
        accessToken,
        refreshToken,
      });
      return acceptanceMatrixResponseSchema.parse(raw);
    },
  });
}

const WEEK_OVERRIDE_PATH = `${ACCEPTANCE_MATRIX_PATH}/week-override`;

export interface WeekOverrideKey {
  officeId: string;
  isoYear: number;
  isoWeek: number;
  weekday: number;
  timeSlot: string; // "HH:MM:SS"
}

/** PUT /api/v1/acceptance-matrix/week-override — 週別上書き 1 セルを設定 (admin/manager). */
export function useSetWeekOverride(): UseMutationResult<
  unknown,
  Error,
  WeekOverrideKey & { status: AcceptanceStatus; notes?: string | null }
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useMutation({
    mutationFn: (v: WeekOverrideKey & { status: AcceptanceStatus; notes?: string | null }) =>
      fetcher<unknown>(WEEK_OVERRIDE_PATH, {
        method: 'PUT',
        body: JSON.stringify({
          office_id: v.officeId,
          iso_year: v.isoYear,
          iso_week: v.isoWeek,
          weekday: v.weekday,
          time_slot: v.timeSlot,
          status: v.status,
          notes: v.notes ?? null,
        }),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ACCEPTANCE_MATRIX_KEY });
    },
    onError: (err: Error) => {
      toast.error(`上書きに失敗しました: ${err.message}`);
    },
  });
}

const STANDING_OVERRIDE_PATH = `${ACCEPTANCE_MATRIX_PATH}/standing-override`;

export interface StandingOverrideKey {
  /** null = 全拠点に一括。 */
  officeId: string | null;
  weekday: number;
  timeSlot: string; // "HH:MM:SS"
}

/** PUT /api/v1/acceptance-matrix/standing-override — 常設(毎週)上書きを設定 (admin/manager). */
export function useSetStandingOverride(): UseMutationResult<
  unknown,
  Error,
  StandingOverrideKey & { status: AcceptanceStatus; notes?: string | null }
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useMutation({
    mutationFn: (v: StandingOverrideKey & { status: AcceptanceStatus; notes?: string | null }) =>
      fetcher<unknown>(STANDING_OVERRIDE_PATH, {
        method: 'PUT',
        body: JSON.stringify({
          office_id: v.officeId, // null = 全拠点
          weekday: v.weekday,
          time_slot: v.timeSlot,
          status: v.status,
          notes: v.notes ?? null,
        }),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ACCEPTANCE_MATRIX_KEY });
    },
    onError: (err: Error) => {
      toast.error(`上書きに失敗しました: ${err.message}`);
    },
  });
}

/** DELETE /api/v1/acceptance-matrix/standing-override — 常設(毎週)上書きを解除 (admin/manager). */
export function useClearStandingOverride(): UseMutationResult<null, Error, StandingOverrideKey> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useMutation({
    mutationFn: (v: StandingOverrideKey) => {
      const usp = new URLSearchParams({
        weekday: String(v.weekday),
        time_slot: v.timeSlot,
      });
      if (v.officeId) usp.set('office_id', v.officeId); // 省略=全拠点
      return fetcher<null>(`${STANDING_OVERRIDE_PATH}?${usp.toString()}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ACCEPTANCE_MATRIX_KEY });
    },
    onError: (err: Error) => {
      toast.error(`上書きに失敗しました: ${err.message}`);
    },
  });
}

/** DELETE /api/v1/acceptance-matrix/week-override — 週別上書き 1 セルを解除 (admin/manager). */
export function useClearWeekOverride(): UseMutationResult<unknown, Error, WeekOverrideKey> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useMutation({
    mutationFn: (v: WeekOverrideKey) => {
      const usp = new URLSearchParams({
        office_id: v.officeId,
        iso_year: String(v.isoYear),
        iso_week: String(v.isoWeek),
        weekday: String(v.weekday),
        time_slot: v.timeSlot,
      });
      return fetcher<null>(`${WEEK_OVERRIDE_PATH}?${usp.toString()}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ACCEPTANCE_MATRIX_KEY });
    },
    onError: (err: Error) => {
      toast.error(`上書きに失敗しました: ${err.message}`);
    },
  });
}
