/**
 * TanStack Query hooks for the staff master.
 *
 * Pattern mirrors `backend/app/api/v1/staff.py`:
 *   GET    /api/v1/staff           list
 *   GET    /api/v1/staff/:id       detail
 *   POST   /api/v1/staff           create   (admin/manager)
 *   PATCH  /api/v1/staff/:id       update   (admin/manager)
 *   DELETE /api/v1/staff/:id       soft-delete (admin)
 *
 * The backend already supports limit/offset paging; client-side search and
 * filtering happen on the page (small dataset, < 500 rows expected for the
 * pilot deployment).
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
  StaffCreate,
  StaffRead,
  StaffUpdate,
} from '@/lib/schemas/staff';

const STAFF_BASE = '/api/v1/staff';

/** Shared cache key prefix; helps useStaffList invalidations match every variant. */
const staffKey = ['staff'] as const;

interface UseStaffListParams {
  limit?: number;
  offset?: number;
}

function useAuthTokens() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    userId: session?.user?.id ?? null,
    isAuthenticated: status === 'authenticated',
  };
}

export function useStaffList(params: UseStaffListParams = {}) {
  const { limit = 100, offset = 0 } = params;
  const { accessToken, refreshToken, userId, isAuthenticated } = useAuthTokens();

  return useQuery<StaffRead[]>({
    queryKey: [...staffKey, 'list', { limit, offset, userId }],
    queryFn: () =>
      fetcher<StaffRead[]>(
        `${STAFF_BASE}?limit=${limit}&offset=${offset}`,
        { accessToken, refreshToken },
      ),
    enabled: isAuthenticated,
  });
}

export function useStaff(staffId: string | null | undefined) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();

  return useQuery<StaffRead>({
    queryKey: [...staffKey, 'detail', staffId],
    queryFn: () =>
      fetcher<StaffRead>(`${STAFF_BASE}/${staffId}`, {
        accessToken,
        refreshToken,
      }),
    enabled: isAuthenticated && !!staffId,
  });
}

type CreateOptions = UseMutationOptions<StaffRead, Error, StaffCreate>;

export function useCreateStaff(options: CreateOptions = {}) {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<StaffRead, Error, StaffCreate>({
    mutationFn: (payload) =>
      fetcher<StaffRead>(STAFF_BASE, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: staffKey });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

interface UpdateVariables {
  id: string;
  payload: StaffUpdate;
}

type UpdateOptions = UseMutationOptions<StaffRead, Error, UpdateVariables>;

export function useUpdateStaff(options: UpdateOptions = {}) {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<StaffRead, Error, UpdateVariables>({
    mutationFn: ({ id, payload }) =>
      fetcher<StaffRead>(`${STAFF_BASE}/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: staffKey });
      void qc.invalidateQueries({ queryKey: [...staffKey, 'detail', variables.id] });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

type DeleteOptions = UseMutationOptions<void, Error, string>;

export function useDeleteStaff(options: DeleteOptions = {}) {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await fetcher<void>(`${STAFF_BASE}/${id}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: staffKey });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}
