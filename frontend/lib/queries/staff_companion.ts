'use client';

/**
 * TanStack Query hooks for staff companion assignments (W10-FE1).
 *
 * Endpoints (W10-BE1 contract):
 *   GET    /api/v1/staff/{id}/companion-assignments  → StaffCompanionAssignmentV2Read[]
 *   PUT    /api/v1/staff/{id}/companion-assignments  Body=StaffCompanionAssignmentsBulkPut → same
 *   DELETE /api/v1/staff/{id}/companion-assignments  → 204
 *   GET    /api/v1/staff/companion-candidates?exclude_id={id} → StaffRead[]
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
import type { StaffRead } from '@/lib/schemas/staff';
import type {
  StaffCompanionAssignmentV2Read,
  StaffCompanionAssignmentsBulkPut,
} from '@/lib/schemas/v2/staff_companion_assignment';

const STAFF_BASE = '/api/v1/staff';

/** Stable cache key factory. */
export const companionAssignmentsKey = (staffId: string) =>
  ['staff-companion-assignments', staffId] as const;

export const companionCandidatesKey = (excludeId?: string | null) =>
  ['staff-companion-candidates', excludeId ?? '__none__'] as const;

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
// GET /staff/{id}/companion-assignments
// ---------------------------------------------------------------------------

export function useCompanionAssignments(staffId: string | null | undefined) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();
  const normalizedId = staffId ?? '__none__';

  return useQuery<StaffCompanionAssignmentV2Read[], Error>({
    queryKey: companionAssignmentsKey(normalizedId),
    queryFn: () => {
      if (!staffId) throw new Error('staff id is required');
      return fetcher<StaffCompanionAssignmentV2Read[]>(
        `${STAFF_BASE}/${staffId}/companion-assignments`,
        { accessToken, refreshToken },
      );
    },
    enabled: isAuthenticated && !!staffId,
  });
}

// ---------------------------------------------------------------------------
// PUT /staff/{id}/companion-assignments
// ---------------------------------------------------------------------------

type UpdateCompanionAssignmentsOptions = UseMutationOptions<
  StaffCompanionAssignmentV2Read[],
  Error,
  StaffCompanionAssignmentsBulkPut
>;

export function useUpdateCompanionAssignments(
  staffId: string,
  options: UpdateCompanionAssignmentsOptions = {},
): UseMutationResult<StaffCompanionAssignmentV2Read[], Error, StaffCompanionAssignmentsBulkPut> {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();
  const key = companionAssignmentsKey(staffId);

  return useMutation<StaffCompanionAssignmentV2Read[], Error, StaffCompanionAssignmentsBulkPut>({
    mutationFn: (payload) =>
      fetcher<StaffCompanionAssignmentV2Read[]>(`${STAFF_BASE}/${staffId}/companion-assignments`, {
        method: 'PUT',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: key });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

// ---------------------------------------------------------------------------
// DELETE /staff/{id}/companion-assignments
// ---------------------------------------------------------------------------

type DeleteCompanionAssignmentsOptions = UseMutationOptions<void, Error, void>;

export function useDeleteCompanionAssignments(
  staffId: string,
  options: DeleteCompanionAssignmentsOptions = {},
): UseMutationResult<void, Error, void> {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();
  const key = companionAssignmentsKey(staffId);

  return useMutation<void, Error, void>({
    mutationFn: async () => {
      await fetcher<void>(`${STAFF_BASE}/${staffId}/companion-assignments`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: key });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

// ---------------------------------------------------------------------------
// GET /staff/companion-candidates?exclude_id={id}
//
// Returns active staff eligible to act as companions.
// Backend filters: role ∈ {manager, staff} AND status = active.
// excludeId = trainee's own id (cannot be own companion).
// ---------------------------------------------------------------------------

export function useCompanionCandidates(excludeId?: string | null) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();

  const qs = excludeId ? `?exclude_id=${encodeURIComponent(excludeId)}` : '';

  return useQuery<StaffRead[], Error>({
    queryKey: companionCandidatesKey(excludeId),
    queryFn: () =>
      fetcher<StaffRead[]>(`${STAFF_BASE}/companion-candidates${qs}`, {
        accessToken,
        refreshToken,
      }),
    enabled: isAuthenticated,
  });
}
