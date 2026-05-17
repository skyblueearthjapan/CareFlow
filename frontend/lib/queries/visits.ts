/**
 * TanStack Query hooks for /api/v1/visits — Phase 4-8 (W2-A).
 *
 * Endpoints (backend `app/api/v1/visits.py`)
 * ──────────────────────────────────────────
 *   GET    /api/v1/visits?limit&offset&week_start&week_end
 *   GET    /api/v1/visits/{id}
 *   POST   /api/v1/visits         (admin/manager)
 *   PATCH  /api/v1/visits/{id}    (admin/manager)
 *   DELETE /api/v1/visits/{id}    (admin)
 *
 * The backend filters visit_date by `week_start..week_end` (inclusive) and
 * orders by `(visit_date, start_time)` so the 500-row hard cap maps to a
 * within-week truncation we can surface in the UI without silent loss.
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
  /** Inclusive ISO date (yyyy-MM-dd). Forwarded to the backend. */
  week_start?: string;
  /** Inclusive ISO date (yyyy-MM-dd). Forwarded to the backend. */
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

function buildListUrl(params: {
  week_start?: string;
  week_end?: string;
  staff_id?: string | null;
  patient_id?: string | null;
}): string {
  const qs = new URLSearchParams();
  qs.set('limit', String(VISIT_LIST_HARD_CAP));
  qs.set('offset', '0');
  if (params.week_start) qs.set('week_start', params.week_start);
  if (params.week_end) qs.set('week_end', params.week_end);
  if (params.staff_id) qs.set('staff_id', params.staff_id);
  if (params.patient_id) qs.set('patient_id', params.patient_id);
  return `${VISITS_BASE}?${qs.toString()}`;
}

/** GET /api/v1/visits — list (week range pushed down to backend). */
export function useVisits(params: UseVisitsParams = {}): UseQueryResult<UseVisitsResult, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  const sessionStaffId = session?.user?.staffId ?? null;
  const role = session?.user?.role ?? 'staff';
  // Staff role users only ever see their own visits — backend already
  // enforces this, but mirroring it client-side keeps the cache key honest.
  const effectiveStaffId =
    role === 'staff' ? (sessionStaffId ?? '__none__') : (params.staff_id ?? null);

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
      // Staff role: the backend already narrows to the caller; we pass the
      // explicit staff_id only for admin/manager. The "__none__" sentinel is
      // never sent over the wire.
      const wireStaffId = role === 'staff' ? null : (effectiveStaffId ?? null);
      // Wave Next 1 H3: patient_id をクライアント側 filter ではなく BE 側で
      // 絞り込む. これにより 500 件 hard cap に達するケースでも当該患者の
      // visit を取りこぼさない. 既存呼び出し (patient_id 未指定) は全件取得の
      // 後方互換動作のまま.
      const items = await fetcher<VisitRead[]>(
        buildListUrl({
          week_start,
          week_end,
          staff_id: wireStaffId,
          patient_id: patient_id ?? null,
        }),
        { accessToken, refreshToken },
      );
      const truncated = items.length === VISIT_LIST_HARD_CAP;
      return { items, truncated };
    },
  });
}

export interface UseUnassignedVisitsParams {
  week_start?: string;
  week_end?: string;
}

/**
 * GET /api/v1/visits — unassigned slice (primary_staff_id is null) for the
 * given week. Staff filters are intentionally not applied because admins/
 * managers need to see every unallocated visit regardless of the row filter.
 */
export function useUnassignedVisits(
  params: UseUnassignedVisitsParams = {},
): UseQueryResult<UseVisitsResult, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const role = session?.user?.role ?? 'staff';
  const sessionStaffId = session?.user?.staffId ?? null;

  const { week_start, week_end } = params;

  return useQuery<UseVisitsResult, Error>({
    queryKey: [...VISITS_KEY, 'unassigned', { week_start, week_end, role, sessionStaffId }],
    enabled: status === 'authenticated',
    queryFn: async () => {
      // Staff w/o staffId would 403; mirror the safe empty state.
      if (role === 'staff' && !sessionStaffId) {
        return { items: [], truncated: false };
      }
      const all = await fetcher<VisitRead[]>(buildListUrl({ week_start, week_end }), {
        accessToken,
        refreshToken,
      });
      const filtered = all.filter((v) => !v.primary_staff_id);
      const truncated = all.length === VISIT_LIST_HARD_CAP;
      return { items: filtered, truncated };
    },
  });
}

/** GET /api/v1/visits/{id} — single record. */
export function useVisit(id: string | null | undefined): UseQueryResult<VisitRead, Error> {
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

/**
 * DELETE /api/v1/visits/{id} — soft-delete (admin / manager).
 *
 * Wave 18 Codex-fix 重大-1: RBAC が admin / manager に拡張された (manager も
 * B-5 配置移動を行うため)。
 *
 * Wave 18 Codex-fix 重大-2: ``cascade_fixed_visit=true`` を指定すると、当該
 * visit の (patient_id, weekday) に紐付く ``patient_fixed_visits`` も同時に
 * 物理削除される。B-5 配置移動 (delete + place-and-fix) で旧曜日の固定枠が
 * 残って翌週以降 Layer 1 が二重展開するバグを防ぐ。プールへの戻し / 純削除
 * では cascade=false (default) のままで、固定枠は保持される。
 */
export interface DeleteVisitVariables {
  id: string;
  /**
   * True: 同 (patient_id, weekday) の patient_fixed_visits も同時削除
   * (B-5 配置移動でのみ true を立てる).
   * デフォルト false (= 固定枠は残す).
   */
  cascadeFixedVisit?: boolean;
}

export function useDeleteVisit(): UseMutationResult<void, Error, DeleteVisitVariables | string> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<void, Error, DeleteVisitVariables | string>({
    mutationFn: async (input) => {
      // 後方互換: 旧呼出 (id を string でそのまま渡す) も受ける.
      const { id, cascadeFixedVisit } =
        typeof input === 'string' ? { id: input, cascadeFixedVisit: false } : input;
      const qs = cascadeFixedVisit ? '?cascade_fixed_visit=true' : '';
      await fetcher<void>(`${VISITS_BASE}/${id}${qs}`, {
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
