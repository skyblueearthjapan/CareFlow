/**
 * TanStack Query hooks for the Patient master.
 *
 * Endpoints (backend `app/api/v1/patients.py`)
 * ────────────────────────────────────────────
 *   GET    /api/v1/patients?limit&offset
 *   GET    /api/v1/patients/{id}
 *   POST   /api/v1/patients
 *   PATCH  /api/v1/patients/{id}
 *   DELETE /api/v1/patients/{id}
 *
 * NOTE: backend list takes `limit/offset` (not `page`) and has no
 * server-side `search`. We accept `page/search` here for caller ergonomics
 * and apply search client-side after fetch. When the backend grows search
 * support, swap `clientFilter` for a query param.
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
  patientCreateSchema,
  patientUpdateSchema,
  type PatientCreate,
  type PatientFormValues,
  type PatientRead,
  type PatientUpdate,
} from '@/lib/schemas/patient';

export interface PatientsListParams {
  /** 1-indexed UI page. */
  page?: number;
  /** Items per page (default 20). */
  limit?: number;
  /** Free-text filter (matched against name/kana/code, client-side). */
  search?: string;
  /** When true, include soft-deleted rows (currently filtered server-side). */
  includeDeleted?: boolean;
  /** Filter by insurance label. */
  insurance?: string;
}

export interface PatientsListResult {
  items: PatientRead[];
  total: number;
  page: number;
  limit: number;
}

const PATIENTS_KEY = ['patients'] as const;

function authPair(session: ReturnType<typeof useSession>['data']): {
  accessToken: string | null;
  refreshToken: string | null;
} {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/**
 * Strip Wave-2-only form fields from the payload before hitting the backend
 * (`weekly_pattern`, `special_week` columns don't exist yet).
 * TODO(Wave 2): drop this once backend gains the columns.
 */
function stripWave2(payload: Record<string, unknown>): Record<string, unknown> {
  const { weekly_pattern: _wp, special_week: _sw, ...rest } = payload;
  // Drop undefined keys so PATCH semantics aren't polluted.
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(rest)) {
    if (v !== undefined) out[k] = v;
  }
  return out;
}

/** GET /api/v1/patients — list (with client-side search/pagination wrapper). */
export function usePatients(
  params: PatientsListParams = {},
): UseQueryResult<PatientsListResult, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  const page = Math.max(1, params.page ?? 1);
  const limit = Math.max(1, params.limit ?? 20);
  const search = params.search?.trim().toLowerCase() ?? '';
  const insurance = params.insurance ?? '';

  return useQuery<PatientsListResult, Error>({
    queryKey: [...PATIENTS_KEY, { page, limit, search, insurance }],
    enabled: status === 'authenticated',
    queryFn: async () => {
      // Fetch a generous window so the client-side search across pages
      // remains usable until backend search lands. Capped at the backend
      // hard limit (500) to keep payloads bounded.
      const all = await fetcher<PatientRead[]>(
        `/api/v1/patients?limit=500&offset=0`,
        { accessToken, refreshToken },
      );
      const filtered = all.filter((p) => {
        if (insurance && p.insurance !== insurance) return false;
        if (!search) return true;
        const hay = `${p.name ?? ''} ${p.kana ?? ''} ${p.code ?? ''}`.toLowerCase();
        return hay.includes(search);
      });
      const start = (page - 1) * limit;
      const slice = filtered.slice(start, start + limit);
      return {
        items: slice,
        total: filtered.length,
        page,
        limit,
      };
    },
  });
}

/** GET /api/v1/patients/{id} — single record. */
export function usePatient(
  id: string | null | undefined,
): UseQueryResult<PatientRead, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<PatientRead, Error>({
    queryKey: [...PATIENTS_KEY, id],
    enabled: status === 'authenticated' && !!id,
    queryFn: () =>
      fetcher<PatientRead>(`/api/v1/patients/${id}`, { accessToken, refreshToken }),
  });
}

/** POST /api/v1/patients — create. */
export function useCreatePatient(): UseMutationResult<
  PatientRead,
  Error,
  PatientFormValues
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PatientRead, Error, PatientFormValues>({
    mutationFn: async (values) => {
      const parsed: PatientCreate = patientCreateSchema.parse(values);
      const body = stripWave2(parsed as unknown as Record<string, unknown>);
      return fetcher<PatientRead>('/api/v1/patients', {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PATIENTS_KEY });
    },
  });
}

/** PATCH /api/v1/patients/{id} — update. */
export function useUpdatePatient(
  id: string,
): UseMutationResult<PatientRead, Error, PatientFormValues> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PatientRead, Error, PatientFormValues>({
    mutationFn: async (values) => {
      const parsed: PatientUpdate = patientUpdateSchema.parse(values);
      const body = stripWave2(parsed as unknown as Record<string, unknown>);
      return fetcher<PatientRead>(`/api/v1/patients/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
    },
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: PATIENTS_KEY });
      qc.setQueryData([...PATIENTS_KEY, id], data);
    },
  });
}

/** DELETE /api/v1/patients/{id} — soft-delete (admin only). */
export function useDeletePatient(): UseMutationResult<void, Error, string> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await fetcher<void>(`/api/v1/patients/${id}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PATIENTS_KEY });
    },
  });
}
