/**
 * TanStack Query hooks for SpecialWeek (Wave 3-E).
 *
 * Endpoints (backend `app/api/v1/special_weeks.py`)
 * ─────────────────────────────────────────────────
 *   GET    /api/v1/special-weeks?patient_id&status&week_from&week_to
 *   GET    /api/v1/special-weeks/{id}
 *   POST   /api/v1/special-weeks       (header + items)
 *   PATCH  /api/v1/special-weeks/{id}  (header; items fully replaced)
 *   DELETE /api/v1/special-weeks/{id}  (admin only)
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
  specialWeekCreateSchema,
  specialWeekUpdateSchema,
  type SpecialWeekCreate,
  type SpecialWeekFormValues,
  type SpecialWeekRead,
  type SpecialWeekUpdate,
} from '@/lib/schemas/special-week';

export interface SpecialWeeksListParams {
  patient_id?: string;
  status?: string;
  week_from?: string;
  week_to?: string;
  limit?: number;
  offset?: number;
}

const SPECIAL_WEEKS_KEY = ['special-weeks'] as const;

function authPair(session: ReturnType<typeof useSession>['data']): {
  accessToken: string | null;
  refreshToken: string | null;
} {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

function dropUndefined(payload: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(payload)) {
    if (v !== undefined) out[k] = v;
  }
  return out;
}

function buildQueryString(params: SpecialWeeksListParams): string {
  const search = new URLSearchParams();
  if (params.patient_id) search.set('patient_id', params.patient_id);
  if (params.status) search.set('status', params.status);
  if (params.week_from) search.set('week_from', params.week_from);
  if (params.week_to) search.set('week_to', params.week_to);
  search.set('limit', String(params.limit ?? 100));
  search.set('offset', String(params.offset ?? 0));
  return search.toString();
}

/**
 * Convert `SpecialWeekFormValues` (string-typed inputs from react-hook-form)
 * into a payload the create/update zod schemas accept. The patient id on
 * each item is forced to match the header so the form does not need to
 * prompt for it twice.
 */
function prepareCreatePayload(
  values: SpecialWeekFormValues,
): Record<string, unknown> {
  return {
    patient_id: values.patient_id,
    week_start: values.week_start,
    week_end: values.week_end,
    apply_mode: values.apply_mode,
    reason: values.reason || undefined,
    status: values.status,
    items: values.items.map((it) => ({
      patient_id: values.patient_id,
      visit_date: it.visit_date,
      weekday: it.weekday,
      row_label: it.row_label || undefined,
      time_type: it.time_type || '時間帯',
      start_time: it.start_time || undefined,
      end_time: it.end_time || undefined,
      preferred_earliest: it.preferred_earliest || undefined,
      preferred_latest: it.preferred_latest || undefined,
      service_minutes: it.service_minutes,
      required_staff_count: it.required_staff_count,
      individual_change_handling: it.individual_change_handling || undefined,
      note: it.note || undefined,
    })),
  };
}

export function useSpecialWeeks(
  params: SpecialWeeksListParams = {},
): UseQueryResult<SpecialWeekRead[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const qs = buildQueryString(params);

  return useQuery<SpecialWeekRead[], Error>({
    queryKey: [...SPECIAL_WEEKS_KEY, params],
    enabled: status === 'authenticated',
    queryFn: () =>
      fetcher<SpecialWeekRead[]>(`/api/v1/special-weeks?${qs}`, {
        accessToken,
        refreshToken,
      }),
  });
}

export function useSpecialWeek(
  id: string | null | undefined,
): UseQueryResult<SpecialWeekRead, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<SpecialWeekRead, Error>({
    queryKey: [...SPECIAL_WEEKS_KEY, id],
    enabled: status === 'authenticated' && !!id,
    queryFn: () =>
      fetcher<SpecialWeekRead>(`/api/v1/special-weeks/${id}`, {
        accessToken,
        refreshToken,
      }),
  });
}

export function useCreateSpecialWeek(): UseMutationResult<
  SpecialWeekRead,
  Error,
  SpecialWeekFormValues
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<SpecialWeekRead, Error, SpecialWeekFormValues>({
    mutationFn: async (values) => {
      const prepared = prepareCreatePayload(values);
      const parsed: SpecialWeekCreate = specialWeekCreateSchema.parse(prepared);
      const body = dropUndefined(parsed as unknown as Record<string, unknown>);
      return fetcher<SpecialWeekRead>('/api/v1/special-weeks', {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SPECIAL_WEEKS_KEY });
    },
  });
}

export function useUpdateSpecialWeek(
  id: string,
): UseMutationResult<SpecialWeekRead, Error, SpecialWeekFormValues> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<SpecialWeekRead, Error, SpecialWeekFormValues>({
    mutationFn: async (values) => {
      const prepared = prepareCreatePayload(values);
      // Drop patient_id from update payload — backend update schema does
      // not allow mutating the parent patient. Items still carry it.
      const { patient_id: _patient_id, ...rest } = prepared;
      void _patient_id;
      const parsed: SpecialWeekUpdate = specialWeekUpdateSchema.parse(rest);
      const body = dropUndefined(parsed as unknown as Record<string, unknown>);
      return fetcher<SpecialWeekRead>(`/api/v1/special-weeks/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
    },
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: SPECIAL_WEEKS_KEY });
      qc.setQueryData([...SPECIAL_WEEKS_KEY, id], data);
    },
  });
}

export function useDeleteSpecialWeek(): UseMutationResult<void, Error, string> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await fetcher<void>(`/api/v1/special-weeks/${id}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SPECIAL_WEEKS_KEY });
    },
  });
}
