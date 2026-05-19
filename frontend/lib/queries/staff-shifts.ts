/**
 * TanStack Query hooks for the staff weekly-shift endpoints.
 *
 *   GET /api/v1/staff/{staff_id}/shifts        -> ShiftsResponse
 *   PUT /api/v1/staff/{staff_id}/shifts        -> ShiftsResponse  (bulk; 7 rows)
 *
 * Cache key: ['staff-shifts', staffId]
 * Mirrors `lib/queries/staff.ts` patterns.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import { type ShiftsResponse, type StaffShiftItem } from '@/lib/schemas/staff-shifts';

const STAFF_BASE = '/api/v1/staff';

export const staffShiftsKey = (staffId: string) => ['staff-shifts', staffId] as const;

function useAuthTokens() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    isAuthenticated: status === 'authenticated',
  };
}

export function useStaffShifts(staffId: string | null | undefined) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();
  const normalizedId = staffId ?? '__none__';

  return useQuery<ShiftsResponse>({
    queryKey: staffShiftsKey(normalizedId),
    queryFn: () => {
      if (!staffId) throw new Error('staff id is required');
      return fetcher<ShiftsResponse>(`${STAFF_BASE}/${staffId}/shifts`, {
        accessToken,
        refreshToken,
      });
    },
    enabled: isAuthenticated && !!staffId,
  });
}

interface UpdateShiftsContext {
  previous: ShiftsResponse | undefined;
}

type UpdateShiftsOptions = UseMutationOptions<
  ShiftsResponse,
  Error,
  StaffShiftItem[],
  UpdateShiftsContext
>;

/**
 * Bulk PUT — 7 weekday rows in one round-trip. Uses an optimistic update
 * so the dialog feels instant; on error we roll back to the snapshot.
 */
export function useUpdateStaffShifts(staffId: string, options: UpdateShiftsOptions = {}) {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();
  const key = staffShiftsKey(staffId);

  return useMutation<ShiftsResponse, Error, StaffShiftItem[], UpdateShiftsContext>({
    mutationFn: (shifts) =>
      fetcher<ShiftsResponse>(`${STAFF_BASE}/${staffId}/shifts`, {
        method: 'PUT',
        body: JSON.stringify({ shifts }),
        accessToken,
        refreshToken,
      }),
    onMutate: async (shifts) => {
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<ShiftsResponse>(key);
      qc.setQueryData<ShiftsResponse>(key, { shifts });
      return { previous };
    },
    onError: (err, vars, context, mutationCtx) => {
      if (context?.previous) {
        qc.setQueryData(key, context.previous);
      }
      options.onError?.(err, vars, context, mutationCtx);
    },
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: key });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}
