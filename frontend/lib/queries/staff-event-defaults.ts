/**
 * TanStack Query hooks for the staff event-default endpoints
 * (毎週の固定イベント・朝会など — kaipoke-event-two-way-design.md §3-②).
 *
 *   GET    /api/v1/staff/{staff_id}/event-defaults          (admin or 本人)
 *   POST   /api/v1/staff/{staff_id}/event-defaults          (admin)
 *   PATCH  /api/v1/staff/{staff_id}/event-defaults/{id}     (admin)
 *   DELETE /api/v1/staff/{staff_id}/event-defaults/{id}     (admin)
 *
 * 変更は「次の週展開から」効く。展開済み週のイベントは staff-events 側の責務。
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { z } from 'zod';

import { fetcher } from '@/lib/api/fetcher';

const STAFF_BASE = '/api/v1/staff';

export const eventDefaultReadSchema = z.object({
  id: z.string().uuid(),
  staff_id: z.string().uuid(),
  weekday: z.number().int().min(0).max(5),
  weekday_label: z.string(),
  start_time: z.string(),
  end_time: z.string(),
  title: z.string(),
  blocking: z.boolean(),
  note: z.string().nullable().optional(),
});
export type EventDefaultRead = z.infer<typeof eventDefaultReadSchema>;

export const eventDefaultCreateSchema = z.object({
  weekday: z.number().int().min(0).max(5),
  start_time: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/),
  end_time: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/),
  title: z.string().min(1).max(255),
  blocking: z.boolean().optional(),
  note: z.string().max(500).nullable().optional(),
});
export type EventDefaultCreate = z.infer<typeof eventDefaultCreateSchema>;

export const eventDefaultsKey = (staffId: string) => ['staff-event-defaults', staffId] as const;

function useAuthTokens() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    isAuthenticated: status === 'authenticated',
  };
}

export function useStaffEventDefaults(staffId: string | null | undefined) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();
  const normalizedId = staffId ?? '__none__';

  return useQuery<EventDefaultRead[]>({
    queryKey: eventDefaultsKey(normalizedId),
    queryFn: () => {
      if (!staffId) throw new Error('staff id is required');
      return fetcher<EventDefaultRead[]>(`${STAFF_BASE}/${staffId}/event-defaults`, {
        accessToken,
        refreshToken,
      });
    },
    enabled: isAuthenticated && !!staffId,
  });
}

type CreateOptions = UseMutationOptions<EventDefaultRead, Error, EventDefaultCreate>;

export function useCreateEventDefault(staffId: string, options: CreateOptions = {}) {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<EventDefaultRead, Error, EventDefaultCreate>({
    mutationFn: (payload) =>
      fetcher<EventDefaultRead>(`${STAFF_BASE}/${staffId}/event-defaults`, {
        method: 'POST',
        body: JSON.stringify(eventDefaultCreateSchema.parse(payload)),
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: eventDefaultsKey(staffId) });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

type DeleteOptions = UseMutationOptions<void, Error, string>;

export function useDeleteEventDefault(staffId: string, options: DeleteOptions = {}) {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await fetcher<void>(`${STAFF_BASE}/${staffId}/event-defaults/${id}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: eventDefaultsKey(staffId) });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}
