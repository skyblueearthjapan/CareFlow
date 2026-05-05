/**
 * Self-scoped TanStack Query hooks for the mobile experience (Wave 2-C).
 *
 * Privacy: every list call passes `?staff_id={session.user.staffId}` so the
 * server narrows visits to "mine" before returning. The backend enforces this
 * for the `staff` role automatically; for admin/manager the explicit query
 * param is what keeps the mobile screens to "my visits only" without pulling
 * everyone's PHI to the client. There is no client-side fallback filter — if
 * the staffId is missing the query stays disabled.
 *
 * Endpoints touched:
 *   GET  /api/v1/visits?staff_id={id}      list (server-filtered to "mine")
 *   GET  /api/v1/staff/{id}                detail (no shifts in current schema)
 *   POST /api/v1/visits/{id}/checkin       NOT YET IMPLEMENTED on the backend
 *   POST /api/v1/visits/{id}/checkout      NOT YET IMPLEMENTED on the backend
 *
 * The check-in / check-out mutations call the (planned) endpoints best-effort.
 * Until the server lands, callers are expected to fall back to localStorage on
 * 404/405/501/network errors (see `m/today/[visitId]/page.tsx`). Wave 2-D will
 * remove the local fallback once the routes ship.
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
import type { StaffRead, StaffShift } from '@/lib/schemas/staff';

/** Visit row returned by `GET /api/v1/visits`. */
export interface MyVisit {
  id: string;
  patient_id: string;
  primary_staff_id: string | null;
  secondary_staff_id: string | null;
  mentor_staff_id: string | null;
  visit_date: string;
  start_time: string;
  end_time: string;
  type: string;
  status: string;
  source: string;
  note: string | null;
  patient_name: string | null;
  staff_name: string | null;
}

const ME_KEY = ['me'] as const;

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    staffId: session?.user?.staffId ?? null,
  };
}

export interface UseMyVisitsParams {
  /** ISO date `YYYY-MM-DD` to filter to a single day. */
  date?: string;
  /** Monday `YYYY-MM-DD` of the target ISO week. */
  weekStart?: string;
  /**
   * Inclusive lower bound forwarded to the backend as `?week_start=`. When
   * omitted, falls back to the value derived from `date` / `weekStart`.
   */
  weekStartFilter?: string;
  /**
   * Inclusive upper bound forwarded to the backend as `?week_end=`. When
   * omitted, falls back to the value derived from `date` / `weekStart`.
   */
  weekEndFilter?: string;
}

/**
 * Add `days` to a `YYYY-MM-DD` string, returning another `YYYY-MM-DD`.
 *
 * Built from local-date components only — never calls `toISOString()`, which
 * would shift in non-UTC timezones / across DST and produce off-by-one dates
 * around midnight (the original symptom of M6).
 */
export function addDays(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(y ?? 1970, (m ?? 1) - 1, (d ?? 1));
  dt.setDate(dt.getDate() + days);
  const yyyy = dt.getFullYear();
  const mm = String(dt.getMonth() + 1).padStart(2, '0');
  const dd = String(dt.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

/** GET /api/v1/visits → my visits, optionally filtered by date or week. */
export function useMyVisits(
  params: UseMyVisitsParams = {},
): UseQueryResult<MyVisit[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken, staffId } = authPair(session);

  // Resolve server-side `week_start` / `week_end` (inclusive) so the backend
  // narrows the result set instead of relying on a client-side slice. When a
  // single `date` is provided we collapse the range to that one day; when a
  // `weekStart` is provided we expand to a 7-day window. Explicit
  // `weekStartFilter` / `weekEndFilter` always win.
  const serverWeekStart =
    params.weekStartFilter ?? params.date ?? params.weekStart ?? null;
  const serverWeekEnd =
    params.weekEndFilter ??
    (params.date
      ? params.date
      : params.weekStart
        ? addDays(params.weekStart, 6) // inclusive end (Monday + 6 = Sunday)
        : null);

  return useQuery<MyVisit[], Error>({
    queryKey: [
      ...ME_KEY,
      'visits',
      {
        staffId,
        date: params.date,
        weekStart: params.weekStart,
        weekStartFilter: serverWeekStart,
        weekEndFilter: serverWeekEnd,
      },
    ],
    enabled: status === 'authenticated' && !!staffId,
    queryFn: async () => {
      // Always pin the request to the caller's staff_id. This is what keeps
      // admin/manager from accidentally pulling the whole org's visits to the
      // mobile bundle — server enforces visibility, client only paints.
      const qs = new URLSearchParams({
        staff_id: staffId ?? '',
        // Backend caps `limit` at 500 (see `backend/app/api/v1/visits.py`).
        // A week-bounded request fits comfortably within that ceiling.
        limit: '500',
        offset: '0',
      });
      if (serverWeekStart) {
        qs.set('week_start', serverWeekStart);
      }
      if (serverWeekEnd) {
        qs.set('week_end', serverWeekEnd);
      }
      const mine = await fetcher<MyVisit[]>(
        `/api/v1/visits?${qs.toString()}`,
        { accessToken, refreshToken },
      );
      // Belt-and-braces client-side narrowing in case the server returns a
      // wider window than we asked for (older deployments, caching, etc.).
      if (params.date) {
        return mine.filter((v) => v.visit_date === params.date);
      }
      if (params.weekStart) {
        const end = addDays(params.weekStart, 7); // exclusive
        return mine.filter(
          (v) => v.visit_date >= params.weekStart! && v.visit_date < end,
        );
      }
      return mine;
    },
  });
}

/** GET /api/v1/visits/{id} — single visit detail (auth-scoped on the server). */
export function useMyVisit(
  visitId: string | null | undefined,
): UseQueryResult<MyVisit, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<MyVisit, Error>({
    queryKey: [...ME_KEY, 'visit', visitId ?? '__none__'],
    enabled: status === 'authenticated' && !!visitId,
    queryFn: () => {
      if (!visitId) throw new Error('visitId is required');
      return fetcher<MyVisit>(`/api/v1/visits/${visitId}`, {
        accessToken,
        refreshToken,
      });
    },
  });
}

/**
 * GET /api/v1/staff/{id} — currently does not return `shifts`. Until the
 * backend grows a shifts payload (planned Wave 2 work) this hook returns the
 * staff record on `data.staff` and an empty `shifts` array; consumers that
 * needed shift data should handle the empty fallback. The `weekday` arg is
 * accepted for forward-compat.
 */
export interface UseMyShiftsResult {
  staff: StaffRead | null;
  shifts: StaffShift[];
}

export function useMyShifts(
  _weekday?: number,
): UseQueryResult<UseMyShiftsResult, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken, staffId } = authPair(session);

  return useQuery<UseMyShiftsResult, Error>({
    queryKey: [...ME_KEY, 'shifts', { staffId, weekday: _weekday }],
    enabled: status === 'authenticated' && !!staffId,
    queryFn: async () => {
      // TODO(W2-C+): backend `/api/v1/staff/{id}` doesn't return shifts yet.
      // Fetch the staff record so the call still validates auth + lets the UI
      // surface the staff name (rather than a raw UUID) for "所属事業所".
      if (!staffId) return { staff: null, shifts: [] };
      const staff = await fetcher<StaffRead>(`/api/v1/staff/${staffId}`, {
        accessToken,
        refreshToken,
      });
      return { staff, shifts: [] };
    },
  });
}

/** Geolocation payload for check-in/out. */
export interface CheckInPayload {
  lat?: number;
  lng?: number;
  /** Client-side timestamp (ISO 8601). */
  at: string;
}

/**
 * POST /api/v1/visits/{id}/checkin
 *
 * NOTE: backend endpoint is not yet implemented. The mutation calls the URL
 * anyway; until the route ships, callers fall back to a localStorage
 * permanent record on 404/405/501/network errors so check-in survives page
 * navigation. Phase 5 will replace the local fallback with server sync.
 */
export function useCheckIn(
  visitId: string,
): UseMutationResult<MyVisit, Error, CheckInPayload> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<MyVisit, Error, CheckInPayload>({
    mutationFn: (payload) =>
      fetcher<MyVisit>(`/api/v1/visits/${visitId}/checkin`, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ME_KEY });
    },
  });
}

/** POST /api/v1/visits/{id}/checkout — see useCheckIn note. */
export function useCheckOut(
  visitId: string,
): UseMutationResult<MyVisit, Error, CheckInPayload> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<MyVisit, Error, CheckInPayload>({
    mutationFn: (payload) =>
      fetcher<MyVisit>(`/api/v1/visits/${visitId}/checkout`, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ME_KEY });
    },
  });
}

// ---------------------------------------------------------------------------
// Date helpers reused by mobile pages
// ---------------------------------------------------------------------------

/** Today's date as `YYYY-MM-DD` in the user's timezone. */
export function todayIso(): string {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

/**
 * Monday of the current ISO week as `YYYY-MM-DD` in local TZ.
 *
 * Built from local-date components (no UTC conversion) to avoid the M6
 * Sunday-midnight / DST-skip drift.
 */
export function currentWeekStartIso(): string {
  const d = new Date();
  const dow = d.getDay(); // 0 = Sun
  const offset = dow === 0 ? -6 : 1 - dow; // shift back to Monday
  d.setDate(d.getDate() + offset);
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

/** First day of the current month (`YYYY-MM-DD`). */
export function currentMonthStartIso(): string {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  return `${yyyy}-${mm}-01`;
}

/** First day of the next month (exclusive upper bound). */
export function nextMonthStartIso(): string {
  const d = new Date();
  d.setMonth(d.getMonth() + 1, 1);
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  return `${yyyy}-${mm}-01`;
}
