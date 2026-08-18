/**
 * TanStack Query hooks for the staff shift-confirmation endpoints
 * (月次出勤カレンダー確定・staff-shift-confirmation-design.md §2-a).
 *
 *   GET  /api/v1/staff/{staff_id}/shift-confirmations?from=&to=   (admin or 本人)
 *   POST /api/v1/staff/{staff_id}/shift-confirmations             (admin)
 *
 * Cache key: ['staff-shift-confirmations', staffId, { from, to }]
 * POST は staffId 配下の全レンジを invalidate する (staff-overrides と同じ流儀)。
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import type {
  ShiftConfirmationCreate,
  ShiftConfirmationRead,
} from '@/lib/schemas/staff-shift-confirmation';

const STAFF_BASE = '/api/v1/staff';

export interface ConfirmationRange {
  from: string; // YYYY-MM-DD
  to: string; // YYYY-MM-DD
}

export const shiftConfirmationsKey = (staffId: string, range?: ConfirmationRange) =>
  ['staff-shift-confirmations', staffId, range ?? null] as const;

export const shiftConfirmationsScopeKey = (staffId: string) =>
  ['staff-shift-confirmations', staffId] as const;

function useAuthTokens() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    isAuthenticated: status === 'authenticated',
  };
}

export function useShiftConfirmations(
  staffId: string | null | undefined,
  range?: ConfirmationRange,
) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();
  const normalizedId = staffId ?? '__none__';

  return useQuery<ShiftConfirmationRead[]>({
    queryKey: shiftConfirmationsKey(normalizedId, range),
    queryFn: () => {
      if (!staffId) throw new Error('staff id is required');
      const qs = range
        ? `?from=${encodeURIComponent(range.from)}&to=${encodeURIComponent(range.to)}`
        : '';
      return fetcher<ShiftConfirmationRead[]>(
        `${STAFF_BASE}/${staffId}/shift-confirmations${qs}`,
        { accessToken, refreshToken },
      );
    },
    enabled: isAuthenticated && !!staffId,
  });
}

type ConfirmOptions = UseMutationOptions<ShiftConfirmationRead, Error, ShiftConfirmationCreate>;

/** 月次確定 (admin)。成功時にスタッフ本人へ通知される (BE 側)。再確定 = 再通知。 */
export function useConfirmShiftMonth(staffId: string, options: ConfirmOptions = {}) {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<ShiftConfirmationRead, Error, ShiftConfirmationCreate>({
    mutationFn: (payload) =>
      fetcher<ShiftConfirmationRead>(`${STAFF_BASE}/${staffId}/shift-confirmations`, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: shiftConfirmationsScopeKey(staffId) });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}
