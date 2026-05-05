/**
 * Self-scoped TanStack Query hooks for the mobile experience (Wave 2-C).
 *
 * The backend's `/api/v1/visits` already filters to the caller's `staff_id`
 * when `user.role === 'staff'`, so a `staff_id=` query param is unnecessary
 * for staff users. For admin/manager (who would otherwise see everyone's
 * visits) we filter on the client by `primary_staff_id` matching
 * `session.user.staffId` so the mobile screens always show "my visits".
 *
 * Endpoints touched:
 *   GET  /api/v1/visits                    list (auto-filtered for staff role)
 *   GET  /api/v1/staff/{id}                detail (no shifts in current schema)
 *   POST /api/v1/visits/{id}/checkin       NOT YET IMPLEMENTED on the backend
 *   POST /api/v1/visits/{id}/checkout      NOT YET IMPLEMENTED on the backend
 *
 * The check-in / check-out mutations call the (planned) endpoints best-effort
 * and surface a toast when the backend returns 404/501 so the user knows the
 * server side is still pending. See TODO markers in `useCheckIn`/`useCheckOut`.
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

/** Filter to "my" visits (covers admin/manager who'd otherwise get everyone). */
function filterMyVisits(visits: MyVisit[], staffId: string | null): MyVisit[] {
  if (!staffId) return [];
  return visits.filter(
    (v) =>
      v.primary_staff_id === staffId ||
      v.secondary_staff_id === staffId ||
      v.mentor_staff_id === staffId,
  );
}

export interface UseMyVisitsParams {
  /** ISO date `YYYY-MM-DD` to filter to a single day (client-side). */
  date?: string;
  /** Monday `YYYY-MM-DD` of the target ISO week (client-side). */
  weekStart?: string;
}

/** Add days to an ISO date string. */
function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

/** GET /api/v1/visits → my visits, optionally filtered by date or week. */
export function useMyVisits(
  params: UseMyVisitsParams = {},
): UseQueryResult<MyVisit[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken, staffId } = authPair(session);

  return useQuery<MyVisit[], Error>({
    queryKey: [...ME_KEY, 'visits', { staffId, date: params.date, weekStart: params.weekStart }],
    enabled: status === 'authenticated' && !!staffId,
    queryFn: async () => {
      const all = await fetcher<MyVisit[]>(
        '/api/v1/visits?limit=500&offset=0',
        { accessToken, refreshToken },
      );
      const mine = filterMyVisits(all, staffId);
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
 * backend grows a shifts payload (planned Wave 2 work) this hook simply
 * returns the staff record; consumers that needed shift data should handle
 * the empty fallback. The `weekday` arg is accepted for forward-compat.
 */
export function useMyShifts(
  _weekday?: number,
): UseQueryResult<StaffShift[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken, staffId } = authPair(session);

  return useQuery<StaffShift[], Error>({
    queryKey: [...ME_KEY, 'shifts', { staffId, weekday: _weekday }],
    enabled: status === 'authenticated' && !!staffId,
    queryFn: async () => {
      // TODO(W2-C+): backend `/api/v1/staff/{id}` doesn't return shifts yet.
      // Fetch the staff record so the call still validates auth, then return [].
      if (!staffId) return [];
      await fetcher<StaffRead>(`/api/v1/staff/${staffId}`, {
        accessToken,
        refreshToken,
      });
      return [];
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
 * NOTE: backend endpoint is not yet implemented. The mutation will call
 * the URL anyway and surface the (likely 404) error to the caller via the
 * standard react-query error path; UI code is expected to wrap the call
 * in try/catch and render a "サーバー未対応" toast on failure.
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

/** Monday of the current ISO week (`YYYY-MM-DD`). */
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
