'use client';

/**
 * useStaffCompanionAssignments — コース×週単位で補佐スタッフ割当を取得 (W15-FE Phase 5).
 *
 * BE エンドポイント (Phase 1 で実装済み想定):
 *   GET  /api/v1/staff-companion-assignments?course_template_id={id}&iso_year={y}&iso_week={w}
 *   PATCH /api/v1/staff-companion-assignments/{id}   Body: { pair_role: 'primary' | 'support' }
 *
 * BE が未実装の場合は空配列 / stub mutation として動作し、UI は graceful degradation する。
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import type {
  PairRole,
  StaffCompanionAssignmentV2Read,
} from '@/lib/schemas/v2/staff_companion_assignment';

const BASE = '/api/v1/staff-companion-assignments';

// ---------------------------------------------------------------------------
// Auth helper
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
// Cache key factory
// ---------------------------------------------------------------------------

export const staffCompanionAssignmentsKey = (
  courseTemplateId: string | null,
  isoYear: number,
  isoWeek: number,
) =>
  [
    'staff-companion-assignments-by-course',
    courseTemplateId ?? '__none__',
    isoYear,
    isoWeek,
  ] as const;

// ---------------------------------------------------------------------------
// GET /api/v1/staff-companion-assignments?course_template_id=&iso_year=&iso_week=
// ---------------------------------------------------------------------------

export interface UseStaffCompanionAssignmentsParams {
  courseTemplateId: string | null;
  isoYear: number;
  isoWeek: number;
  enabled?: boolean;
}

export function useStaffCompanionAssignments({
  courseTemplateId,
  isoYear,
  isoWeek,
  enabled = true,
}: UseStaffCompanionAssignmentsParams) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();

  return useQuery<StaffCompanionAssignmentV2Read[], Error>({
    queryKey: staffCompanionAssignmentsKey(courseTemplateId, isoYear, isoWeek),
    queryFn: async () => {
      if (!courseTemplateId) return [];
      const qs = new URLSearchParams({
        course_template_id: courseTemplateId,
        iso_year: String(isoYear),
        iso_week: String(isoWeek),
      });
      try {
        return await fetcher<StaffCompanionAssignmentV2Read[]>(`${BASE}?${qs.toString()}`, {
          accessToken,
          refreshToken,
        });
      } catch {
        // BE endpoint may not exist yet — graceful degradation
        return [];
      }
    },
    enabled: isAuthenticated && !!courseTemplateId && enabled,
  });
}

// ---------------------------------------------------------------------------
// PATCH /api/v1/staff-companion-assignments/{id}  { pair_role }
// ---------------------------------------------------------------------------

export interface PatchCompanionAssignmentPairRoleVariables {
  id: string;
  pair_role: PairRole;
  /** For cache invalidation after patch. */
  courseTemplateId: string;
  isoYear: number;
  isoWeek: number;
}

export function usePatchCompanionAssignmentPairRole(): UseMutationResult<
  StaffCompanionAssignmentV2Read,
  Error,
  PatchCompanionAssignmentPairRoleVariables
> {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<
    StaffCompanionAssignmentV2Read,
    Error,
    PatchCompanionAssignmentPairRoleVariables
  >({
    mutationFn: ({ id, pair_role }) =>
      fetcher<StaffCompanionAssignmentV2Read>(`${BASE}/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ pair_role }),
        accessToken,
        refreshToken,
      }),
    onSuccess: (_data, { courseTemplateId, isoYear, isoWeek }) => {
      void qc.invalidateQueries({
        queryKey: staffCompanionAssignmentsKey(courseTemplateId, isoYear, isoWeek),
      });
    },
  });
}
