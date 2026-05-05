/**
 * TanStack Query hooks for staff shift requests (Wave 4-D).
 *
 * Endpoints:
 *   GET   /api/v1/staff/{staff_id}/shift-requests        → ShiftRequestRead[]
 *   POST  /api/v1/staff/{staff_id}/shift-requests        → ShiftRequestRead
 *   PATCH /api/v1/shift-requests/{id}/status             → ShiftRequestRead (admin/manager)
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
import {
  shiftRequestCreateSchema,
  shiftRequestStatusUpdateSchema,
  type ShiftRequestCreate,
  type ShiftRequestRead,
  type ShiftRequestStatusUpdate,
} from '@/lib/schemas/shift-request';

const SHIFT_REQUESTS_KEY = ['shift-requests'] as const;

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/** GET /api/v1/staff/{staff_id}/shift-requests */
export function useShiftRequests(
  staffId: string | null | undefined,
): UseQueryResult<ShiftRequestRead[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<ShiftRequestRead[], Error>({
    queryKey: [...SHIFT_REQUESTS_KEY, 'staff', staffId ?? '__none__'],
    enabled: status === 'authenticated' && !!staffId,
    queryFn: () => {
      if (!staffId) throw new Error('staffId is required');
      return fetcher<ShiftRequestRead[]>(
        `/api/v1/staff/${staffId}/shift-requests`,
        { accessToken, refreshToken },
      );
    },
  });
}

/** POST /api/v1/staff/{staff_id}/shift-requests */
export function useCreateShiftRequest(
  staffId: string,
): UseMutationResult<ShiftRequestRead, Error, ShiftRequestCreate> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<ShiftRequestRead, Error, ShiftRequestCreate>({
    mutationFn: async (values) => {
      const parsed = shiftRequestCreateSchema.parse(values);
      return fetcher<ShiftRequestRead>(
        `/api/v1/staff/${staffId}/shift-requests`,
        {
          method: 'POST',
          body: JSON.stringify(parsed),
          accessToken,
          refreshToken,
        },
      );
    },
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: [...SHIFT_REQUESTS_KEY, 'staff', staffId],
      });
    },
  });
}

interface UpdateStatusVariables {
  requestId: string;
  payload: ShiftRequestStatusUpdate;
}

/** PATCH /api/v1/shift-requests/{request_id}/status (admin/manager). */
export function useUpdateShiftRequestStatus(): UseMutationResult<
  ShiftRequestRead,
  Error,
  UpdateStatusVariables
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<ShiftRequestRead, Error, UpdateStatusVariables>({
    mutationFn: async ({ requestId, payload }) => {
      const parsed = shiftRequestStatusUpdateSchema.parse(payload);
      return fetcher<ShiftRequestRead>(
        `/api/v1/shift-requests/${requestId}/status`,
        {
          method: 'PATCH',
          body: JSON.stringify(parsed),
          accessToken,
          refreshToken,
        },
      );
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SHIFT_REQUESTS_KEY });
    },
  });
}
