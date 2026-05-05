/**
 * TanStack Query hooks for the staff override endpoints (休み / 時間変更).
 *
 *   GET    /api/v1/staff/{staff_id}/overrides?from=YYYY-MM-DD&to=YYYY-MM-DD
 *   POST   /api/v1/staff/{staff_id}/overrides
 *   PATCH  /api/v1/staff/{staff_id}/overrides/{id}
 *   DELETE /api/v1/staff/{staff_id}/overrides/{id}
 *
 * Cache key: ['staff-overrides', staffId, { from, to }]
 * Mutations invalidate every range under ['staff-overrides', staffId].
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
  OverrideCreate,
  OverrideRead,
  OverrideUpdate,
} from '@/lib/schemas/staff-overrides';

const STAFF_BASE = '/api/v1/staff';

export interface OverrideRange {
  from: string; // YYYY-MM-DD
  to: string; // YYYY-MM-DD
}

export const staffOverridesKey = (
  staffId: string,
  range?: OverrideRange,
) => ['staff-overrides', staffId, range ?? null] as const;

export const staffOverridesScopeKey = (staffId: string) =>
  ['staff-overrides', staffId] as const;

function useAuthTokens() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    isAuthenticated: status === 'authenticated',
  };
}

export function useStaffOverrides(
  staffId: string | null | undefined,
  range?: OverrideRange,
) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();
  const normalizedId = staffId ?? '__none__';

  return useQuery<OverrideRead[]>({
    queryKey: staffOverridesKey(normalizedId, range),
    queryFn: () => {
      if (!staffId) throw new Error('staff id is required');
      const qs = range
        ? `?from=${encodeURIComponent(range.from)}&to=${encodeURIComponent(range.to)}`
        : '';
      return fetcher<OverrideRead[]>(
        `${STAFF_BASE}/${staffId}/overrides${qs}`,
        { accessToken, refreshToken },
      );
    },
    enabled: isAuthenticated && !!staffId,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

type CreateOptions = UseMutationOptions<OverrideRead, Error, OverrideCreate>;

export function useCreateOverride(
  staffId: string,
  options: CreateOptions = {},
) {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<OverrideRead, Error, OverrideCreate>({
    mutationFn: (payload) =>
      fetcher<OverrideRead>(`${STAFF_BASE}/${staffId}/overrides`, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: staffOverridesScopeKey(staffId) });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

interface UpdateVariables {
  id: string;
  payload: OverrideUpdate;
}

type UpdateOptions = UseMutationOptions<OverrideRead, Error, UpdateVariables>;

export function useUpdateOverride(
  staffId: string,
  options: UpdateOptions = {},
) {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<OverrideRead, Error, UpdateVariables>({
    mutationFn: ({ id, payload }) =>
      fetcher<OverrideRead>(`${STAFF_BASE}/${staffId}/overrides/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: staffOverridesScopeKey(staffId) });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

type DeleteOptions = UseMutationOptions<void, Error, string>;

export function useDeleteOverride(
  staffId: string,
  options: DeleteOptions = {},
) {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await fetcher<void>(`${STAFF_BASE}/${staffId}/overrides/${id}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: staffOverridesScopeKey(staffId) });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}
