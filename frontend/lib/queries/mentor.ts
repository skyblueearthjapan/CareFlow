/**
 * TanStack Query hooks for mentor assignment.
 *
 * Endpoints (F5-Backend contract):
 *   GET /api/v1/staff/{staff_id}/mentor → MentorRead
 *   PUT /api/v1/staff/{staff_id}/mentor  Body={ mentor_staff_id: UUID|null }
 *
 * "Unassign" is the same PUT call with `mentor_staff_id: null` — the API has
 * no separate DELETE verb.
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
import { mentorAssignSchema, type MentorAssign, type MentorRead } from '@/lib/schemas/mentor';

const MENTOR_KEY = ['staff', 'mentor'] as const;

function mentorBase(staffId: string) {
  return `/api/v1/staff/${staffId}/mentor`;
}

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/** GET .../mentor — current mentor for a staff member. */
export function useMentor(staffId: string | null | undefined): UseQueryResult<MentorRead, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const normalizedId = staffId ?? '__none__';

  return useQuery<MentorRead, Error>({
    queryKey: [...MENTOR_KEY, normalizedId],
    enabled: status === 'authenticated' && !!staffId,
    queryFn: () => {
      if (!staffId) throw new Error('staff id is required');
      return fetcher<MentorRead>(mentorBase(staffId), {
        accessToken,
        refreshToken,
      });
    },
  });
}

/**
 * PUT .../mentor — assign or unassign (mentor_staff_id=null).
 * Optimistic update writes through to the cached MentorRead so the detail
 * card reflects the change immediately; reverts on failure.
 */
export function useUpdateMentor(
  staffId: string,
): UseMutationResult<MentorRead, Error, MentorAssign, { previous: MentorRead | undefined }> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<MentorRead, Error, MentorAssign, { previous: MentorRead | undefined }>({
    mutationFn: async (payload) => {
      const parsed = mentorAssignSchema.parse(payload);
      return fetcher<MentorRead>(mentorBase(staffId), {
        method: 'PUT',
        body: JSON.stringify(parsed),
        accessToken,
        refreshToken,
      });
    },
    onMutate: async (payload) => {
      await qc.cancelQueries({ queryKey: [...MENTOR_KEY, staffId] });
      const previous = qc.getQueryData<MentorRead>([...MENTOR_KEY, staffId]);
      // Optimistic: mentor name unknown until refetch — clear it so we don't
      // show a stale name next to the new id.
      qc.setQueryData<MentorRead>([...MENTOR_KEY, staffId], {
        mentor_staff_id: payload.mentor_staff_id,
        mentor_staff_name: payload.mentor_staff_id ? undefined : null,
      });
      return { previous };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.previous !== undefined) {
        qc.setQueryData([...MENTOR_KEY, staffId], ctx.previous);
      }
    },
    onSuccess: (data) => {
      qc.setQueryData([...MENTOR_KEY, staffId], data);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: [...MENTOR_KEY, staffId] });
    },
  });
}
