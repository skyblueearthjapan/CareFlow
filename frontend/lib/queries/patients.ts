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
  parseSpecialWeekInput,
  patientCreateSchema,
  patientUpdateSchema,
  weeklyPatternToWire,
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
  /** True when the backend list returned exactly the hard cap (500), so the
   * client view may be silently truncated. */
  truncated: boolean;
}

/** Backend hard cap for the list endpoint — keep in sync with the API. */
const PATIENT_LIST_HARD_CAP = 500;

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
 * Drop `undefined` keys so PATCH semantics aren't polluted (only fields
 * the user actually changed go on the wire).
 */
function dropUndefined(payload: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(payload)) {
    if (v !== undefined) out[k] = v;
  }
  return out;
}

/**
 * Pre-process raw form values into a shape that matches the v2 zod schemas
 * (W1-FE1).
 *
 * - `weekly_pattern` / `special_weekly_pattern` は構造化 `WeeklyPattern` 辞書
 *   (`WeeklyPatternEditor` 出力) を `weeklyPatternToWire` で v2 dict に変換
 * - `special_week_enabled` (boolean) と `special_week_active_input`
 *   (テキスト) を `special_week_active: [{iso_year, iso_week}, ...]` に変換
 * - 旧 v1 のみのフィールド (age / ng_time_* / required_staff_count / area /
 *   ng_staff_ids / preferred_staff_ids / specified_type / continuous_request /
 *   special_week) は backend v2 schema が `extra="forbid"` のため、混入させない
 *
 * Clear-vs-unchanged semantics (PATCH only):
 *   `initial` を渡し、特別週が ON→OFF に変わった場合のみ
 *   `special_week_active: []` を明示的に送信して列をクリアする。
 */
function prepareFormPayload(
  values: PatientFormValues,
  initial?: PatientFormValues,
): Record<string, unknown> {
  // 数値フィールドは form values が文字列なので coerce.
  const lat = values.lat === '' ? undefined : Number(values.lat);
  const lng = values.lng === '' ? undefined : Number(values.lng);

  // weekly_pattern: 常に構造化 dict を送る (Create では default、Update では上書き)。
  const weekly_pattern = weeklyPatternToWire(values.weekly_pattern);

  // 特別週: 有効化された場合のみ pattern + active を送る。
  let special_weekly_pattern: Record<string, unknown> | null | undefined;
  let special_week_active: Array<{ iso_year: number; iso_week: number }> | undefined;
  if (values.special_week_enabled) {
    special_weekly_pattern = weeklyPatternToWire(values.special_weekly_pattern);
    special_week_active = parseSpecialWeekInput(values.special_week_active_input);
  } else if (initial && initial.special_week_enabled) {
    // Was enabled, now disabled → 明示クリア
    special_weekly_pattern = null;
    special_week_active = [];
  }

  const out: Record<string, unknown> = {
    code: values.code,
    name: values.name,
    kana: values.kana === '' ? undefined : values.kana,
    sex: values.sex === '' ? undefined : values.sex,
    status: values.status,
    insurance: values.insurance === '' ? undefined : values.insurance,
    address: values.address === '' ? undefined : values.address,
    lat: Number.isFinite(lat) ? lat : undefined,
    lng: Number.isFinite(lng) ? lng : undefined,
    primary_office_id: values.primary_office_id === '' ? undefined : values.primary_office_id,
    sex_restriction: values.sex_restriction === '' ? undefined : values.sex_restriction,
    note: values.note === '' ? undefined : values.note,
    weekly_pattern,
  };
  if (special_weekly_pattern !== undefined) {
    out.special_weekly_pattern = special_weekly_pattern;
  }
  if (special_week_active !== undefined) {
    out.special_week_active = special_week_active;
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
        `/api/v1/patients?limit=${PATIENT_LIST_HARD_CAP}&offset=0`,
        { accessToken, refreshToken },
      );
      const truncated = all.length === PATIENT_LIST_HARD_CAP;
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
        truncated,
      };
    },
  });
}

/** GET /api/v1/patients/{id} — single record. */
export function usePatient(id: string | null | undefined): UseQueryResult<PatientRead, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<PatientRead, Error>({
    queryKey: [...PATIENTS_KEY, id],
    enabled: status === 'authenticated' && !!id,
    queryFn: () => fetcher<PatientRead>(`/api/v1/patients/${id}`, { accessToken, refreshToken }),
  });
}

/** POST /api/v1/patients — create. */
export function useCreatePatient(): UseMutationResult<PatientRead, Error, PatientFormValues> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PatientRead, Error, PatientFormValues>({
    mutationFn: async (values) => {
      const prepared = prepareFormPayload(values);
      const parsed: PatientCreate = patientCreateSchema.parse(prepared);
      const body = dropUndefined(parsed as unknown as Record<string, unknown>);
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

/** PATCH /api/v1/patients/{id} — update.
 *
 * Pass `initial` (the form values derived from the loaded record) so we can
 * tell "user cleared a previously-set JSON field" apart from "field stays
 * unchanged" and emit explicit `null` to clear it server-side.
 */
export function useUpdatePatient(
  id: string,
  initial?: PatientFormValues,
): UseMutationResult<PatientRead, Error, PatientFormValues> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PatientRead, Error, PatientFormValues>({
    mutationFn: async (values) => {
      const prepared = prepareFormPayload(values, initial);
      const parsed: PatientUpdate = patientUpdateSchema.parse(prepared);
      const body = dropUndefined(parsed as unknown as Record<string, unknown>);
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
