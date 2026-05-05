/**
 * TanStack Query hooks for /api/v1/visits — Phase 4-8 (W2-A).
 *
 * Endpoints (backend `app/api/v1/visits.py`)
 * ──────────────────────────────────────────
 *   GET    /api/v1/visits?limit&offset
 *   GET    /api/v1/visits/{id}
 *   POST   /api/v1/visits         (admin/manager)
 *   PATCH  /api/v1/visits/{id}    (admin/manager)
 *   DELETE /api/v1/visits/{id}    (admin)
 *
 * The backend currently has no `week_start` / `staff_id` / `patient_id`
 * server-side filters (see backend `list_visits`). We fetch a generous
 * window and filter client-side; once backend grows query params, swap
 * the client filter for URL params.
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
  visitCreateSchema,
  visitUpdateSchema,
  type VisitCreate,
  type VisitRead,
  type VisitUpdate,
} from '@/lib/schemas/visit';

const VISITS_KEY = ['visits'] as const;
const VISITS_BASE = '/api/v1/visits';
/** Backend hard cap (`Query(le=500)`); keep in sync. */
const VISIT_LIST_HARD_CAP = 500;

export interface UseVisitsParams {
  /** Inclusive ISO date (yyyy-MM-dd). Filters client-side until backend supports it. */
  week_start?: string;
  /** Inclusive ISO date (yyyy-MM-dd). */
  week_end?: string;
  staff_id?: string;
  patient_id?: string;
}

export interface UseVisitsResult {
  items: VisitRead[];
  /** True if the backend page hit the hard cap so the week may be incomplete. */
  truncated: boolean;
}

function authPair(session: ReturnType<typeof useSession>['data']) {
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

/** GET /api/v1/visits — list (with client-side date/staff/patient filter). */
export function useVisits(
  params: UseVisitsParams = {},
): UseQueryResult<UseVisitsResult, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  const sessionStaffId = session?.user?.staffId ?? null;
  const role = session?.user?.role ?? 'staff';
  // Staff role users only ever see their own visits — backend already
  // enforces this, but mirroring it client-side keeps the cache key honest.
  const effectiveStaffId =
    role === 'staff' ? sessionStaffId ?? '__none__' : params.staff_id ?? null;

  const { week_start, week_end, patient_id } = params;

  return useQuery<UseVisitsResult, Error>({
    queryKey: [
      ...VISITS_KEY,
      'list',
      { week_start, week_end, staff_id: effectiveStaffId, patient_id },
    ],
    enabled: status === 'authenticated',
    queryFn: async () => {
      // Staff with no staffId would 403 in the engine; short-circuit.
      if (role === 'staff' && !sessionStaffId) {
        return { items: [], truncated: false };
      }
      const all = await fetcher<VisitRead[]>(
        `${VISITS_BASE}?limit=${VISIT_LIST_HARD_CAP}&offset=0`,
        { accessToken, refreshToken },
      );
      const truncated = all.length === VISIT_LIST_HARD_CAP;
      const filtered = all.filter((v) => {
        if (week_start && v.visit_date < week_start) return false;
        if (week_end && v.visit_date > week_end) return false;
        if (effectiveStaffId) {
          const matches =
            v.primary_staff_id === effectiveStaffId ||
            v.secondary_staff_id === effectiveStaffId ||
            v.mentor_staff_id === effectiveStaffId;
          if (!matches) return false;
        }
        if (patient_id && v.patient_id !== patient_id) return false;
        return true;
      });
      return { items: filtered, truncated };
    },
  });
}

/** GET /api/v1/visits/{id} — single record. */
export function useVisit(
  id: string | null | undefined,
): UseQueryResult<VisitRead, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const normalizedId = id ?? '__none__';

  return useQuery<VisitRead, Error>({
    queryKey: [...VISITS_KEY, 'detail', normalizedId],
    enabled: status === 'authenticated' && !!id,
    queryFn: () => {
      if (!id) throw new Error('visit id is required');
      return fetcher<VisitRead>(`${VISITS_BASE}/${id}`, {
        accessToken,
        refreshToken,
      });
    },
  });
}

/** POST /api/v1/visits — create. */
export function useCreateVisit(): UseMutationResult<VisitRead, Error, VisitCreate> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<VisitRead, Error, VisitCreate>({
    mutationFn: async (values) => {
      const parsed = visitCreateSchema.parse(values);
      const body = dropUndefined(parsed as unknown as Record<string, unknown>);
      return fetcher<VisitRead>(VISITS_BASE, {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: VISITS_KEY });
    },
  });
}

interface UpdateVariables {
  id: string;
  payload: VisitUpdate;
}

/** PATCH /api/v1/visits/{id} — update. */
export function useUpdateVisit(): UseMutationResult<VisitRead, Error, UpdateVariables> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<VisitRead, Error, UpdateVariables>({
    mutationFn: async ({ id, payload }) => {
      const parsed = visitUpdateSchema.parse(payload);
      const body = dropUndefined(parsed as unknown as Record<string, unknown>);
      return fetcher<VisitRead>(`${VISITS_BASE}/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
    },
    onSuccess: (data, variables) => {
      void qc.invalidateQueries({ queryKey: VISITS_KEY });
      qc.setQueryData([...VISITS_KEY, 'detail', variables.id], data);
    },
  });
}

/** DELETE /api/v1/visits/{id} — soft-delete (admin only). */
export function useDeleteVisit(): UseMutationResult<void, Error, string> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await fetcher<void>(`${VISITS_BASE}/${id}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: VISITS_KEY });
    },
  });
}
