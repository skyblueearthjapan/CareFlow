/**
 * TanStack Query hook for /api/v1/allocate/run — Phase 4-10 (W2-A).
 *
 * Triggers the allocation engine for a single ISO week. Admin/manager only;
 * the backend additionally rate-limits to 3/min/IP.
 *
 * The response shape mirrors backend `app/schemas/allocation.py`
 * (`AllocateResponse`); we keep it as a permissive zod schema to avoid
 * tight-coupling FE to the engine output while it iterates (W1-G full mapping).
 */
'use client';

import {
  useMutation,
  useQueryClient,
  type UseMutationResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { z } from 'zod';

import { fetcher } from '@/lib/api/fetcher';

export const allocateSummarySchema = z.object({
  total: z.number().int(),
  assigned: z.number().int(),
  unassigned: z.number().int(),
  mapping_phase: z.enum(['minimal', 'full']),
});

export const assignmentItemSchema = z.object({
  visit_id: z.string().default(''),
  date_str: z.string().default(''),
  weekday: z.string().default(''),
  staff_id: z.string().default(''),
  staff_name: z.string().default(''),
  pid: z.string().default(''),
  pname: z.string().default(''),
  area: z.string().default(''),
  start_min: z.number().int().nullable().optional(),
  end_min: z.number().int().nullable().optional(),
  service_min: z.number().int().default(60),
  time_type: z.string().default(''),
  earliest_min: z.number().int().nullable().optional(),
  latest_min: z.number().int().nullable().optional(),
  note: z.string().default(''),
  is_event: z.boolean().default(false),
  is_coupled: z.boolean().default(false),
  movement_km: z.number().nullable().optional(),
});

export const allocateResponseSchema = z.object({
  week_start: z.string(),
  summary: allocateSummarySchema,
  assignments: z.array(assignmentItemSchema),
});

export type AllocateSummary = z.infer<typeof allocateSummarySchema>;
export type AssignmentItem = z.infer<typeof assignmentItemSchema>;
export type AllocateResponse = z.infer<typeof allocateResponseSchema>;

/** POST /api/v1/allocate/run — runs the engine for `week_start` (Monday). */
export function useRunAllocate(): UseMutationResult<AllocateResponse, Error, string> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<AllocateResponse, Error, string>({
    mutationFn: async (week_start) => {
      const raw = await fetcher<unknown>('/api/v1/allocate/run', {
        method: 'POST',
        body: JSON.stringify({ week_start }),
        accessToken,
        refreshToken,
      });
      return allocateResponseSchema.parse(raw);
    },
    onSuccess: () => {
      // The engine writes assignments back to visits; refresh the list view.
      void qc.invalidateQueries({ queryKey: ['visits'] });
    },
  });
}
