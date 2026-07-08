/**
 * TanStack Query hooks for staff events (研修日 / イベント).
 *
 * Endpoints (F5-Backend contract):
 *   GET    /api/v1/staff/{staff_id}/events?from=YYYY-MM-DD&to=YYYY-MM-DD
 *   POST   /api/v1/staff/{staff_id}/events
 *   PATCH  /api/v1/staff/{staff_id}/events/{event_id}
 *   DELETE /api/v1/staff/{staff_id}/events/{event_id}
 *
 * Pattern follows `lib/queries/staff.ts` for auth/cache invalidation, and
 * `lib/queries/visits.ts` for the optimistic-update flavour (snapshot →
 * mutate → rollback on error → invalidate on settle).
 */
'use client';

import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  eventCreateSchema,
  eventUpdateSchema,
  type EventCreate,
  type EventRead,
  type EventUpdate,
} from '@/lib/schemas/staff-events';

const STAFF_EVENTS_KEY = ['staff', 'events'] as const;

function staffEventsBase(staffId: string) {
  return `/api/v1/staff/${staffId}/events`;
}

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

export interface DateRange {
  from: string; // YYYY-MM-DD inclusive
  to: string; // YYYY-MM-DD inclusive
}

function buildListUrl(staffId: string, range?: DateRange): string {
  if (!range) return staffEventsBase(staffId);
  const qs = new URLSearchParams();
  qs.set('from', range.from);
  qs.set('to', range.to);
  return `${staffEventsBase(staffId)}?${qs.toString()}`;
}

/** GET .../events — list (optionally filtered by date range). */
export function useStaffEvents(
  staffId: string | null | undefined,
  range?: DateRange,
): UseQueryResult<EventRead[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const normalizedId = staffId ?? '__none__';

  return useQuery<EventRead[], Error>({
    queryKey: [...STAFF_EVENTS_KEY, normalizedId, range ?? null],
    enabled: status === 'authenticated' && !!staffId,
    queryFn: () => {
      if (!staffId) throw new Error('staff id is required');
      return fetcher<EventRead[]>(buildListUrl(staffId, range), {
        accessToken,
        refreshToken,
      });
    },
  });
}

/**
 * Wave 27 Phase B-1: 週単位で複数 staff の events を並列バッチ取得する hook.
 *
 * 戻り値は `staffId` の順番に対応した EventRead[][] (空配列でフォールバック)。
 * `staffEventsByStaff` Map に変換して担当 dropdown / セル警告で利用する。
 */
export function useWeekStaffEvents(
  staffIds: string[],
  weekStart: Date,
  weekEnd: Date,
): { data: EventRead[][]; isLoading: boolean } {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  const fromStr = weekStart.toISOString().slice(0, 10);
  const toStr = weekEnd.toISOString().slice(0, 10);

  const results = useQueries({
    queries: staffIds.map((id) => ({
      queryKey: [...STAFF_EVENTS_KEY, id, fromStr, toStr] as const,
      enabled: status === 'authenticated' && !!id,
      queryFn: () =>
        fetcher<EventRead[]>(buildListUrl(id, { from: fromStr, to: toStr }), {
          accessToken,
          refreshToken,
        }),
    })),
  });

  const data = results.map((r) => r.data ?? []);
  const isLoading = results.some((r) => r.isLoading);

  return { data, isLoading };
}

/**
 * Wave 27 Phase B-1 helper: `useWeekStaffEvents` の結果を `staffId → EventRead[]` の
 * Map に変換する。
 */
export function buildStaffEventsMap(
  staffIds: string[],
  events: EventRead[][],
): Map<string, EventRead[]> {
  const m = new Map<string, EventRead[]>();
  staffIds.forEach((id, i) => {
    m.set(id, events[i] ?? []);
  });
  return m;
}

/** POST .../events — create. */
export function useCreateEvent(staffId: string): UseMutationResult<EventRead, Error, EventCreate> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<EventRead, Error, EventCreate>({
    mutationFn: async (values) => {
      const parsed = eventCreateSchema.parse(values);
      return fetcher<EventRead>(staffEventsBase(staffId), {
        method: 'POST',
        body: JSON.stringify(parsed),
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: [...STAFF_EVENTS_KEY, staffId],
      });
    },
  });
}

/**
 * D-1: 任意スタッフ宛のイベント作成 (staffId を variables で受ける)。
 * タイムラインの「スタッフの打合せ追加」が選択スタッフ (他コース担当・管理職含む)
 * ごとに呼ぶ。useCreateEvent はフックが staffId 固定のため複数選択に使えない。
 * 成功時は staff/events 全体を invalidate (useUpdateEventForDrag と同方針 —
 * 週タイムラインの帯が即時更新される)。
 */
export function useCreateEventForStaff(): UseMutationResult<
  EventRead,
  Error,
  { staffId: string; payload: EventCreate }
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<EventRead, Error, { staffId: string; payload: EventCreate }>({
    mutationFn: async ({ staffId, payload }) => {
      const parsed = eventCreateSchema.parse(payload);
      return fetcher<EventRead>(staffEventsBase(staffId), {
        method: 'POST',
        body: JSON.stringify(parsed),
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: STAFF_EVENTS_KEY });
    },
  });
}

interface UpdateEventVariables {
  eventId: string;
  payload: EventUpdate;
}

/**
 * PATCH .../events/{event_id} — update with optimistic write-through to the
 * cached list. On error we revert and let the invalidate-on-settle re-fetch
 * authoritatively.
 */
export function useUpdateEvent(
  staffId: string,
): UseMutationResult<
  EventRead,
  Error,
  UpdateEventVariables,
  { previous: Array<[readonly unknown[], EventRead[] | undefined]> }
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<
    EventRead,
    Error,
    UpdateEventVariables,
    { previous: Array<[readonly unknown[], EventRead[] | undefined]> }
  >({
    mutationFn: async ({ eventId, payload }) => {
      const parsed = eventUpdateSchema.parse(payload);
      return fetcher<EventRead>(`${staffEventsBase(staffId)}/${eventId}`, {
        method: 'PATCH',
        body: JSON.stringify(parsed),
        accessToken,
        refreshToken,
      });
    },
    onMutate: async ({ eventId, payload }) => {
      await qc.cancelQueries({ queryKey: [...STAFF_EVENTS_KEY, staffId] });
      const snapshots = qc.getQueriesData<EventRead[]>({
        queryKey: [...STAFF_EVENTS_KEY, staffId],
      });
      for (const [key, list] of snapshots) {
        if (!list) continue;
        qc.setQueryData<EventRead[]>(
          key,
          list.map((e) => (e.id === eventId ? { ...e, ...payload } : e)),
        );
      }
      return { previous: snapshots };
    },
    onError: (_err, _vars, ctx) => {
      if (!ctx) return;
      for (const [key, value] of ctx.previous) {
        qc.setQueryData(key, value);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({
        queryKey: [...STAFF_EVENTS_KEY, staffId],
      });
    },
  });
}

/**
 * Wave 39: D&D 用の「動的 staffId 指定」mutation hook.
 *
 * 既存の `useUpdateEvent(staffId)` は staffId をフックの引数に固定するため、
 * 複数 staff の event を一括ハンドルする D&D 経由の呼び出しに不向き。
 * 本フックは `staffId` を mutate variables 側で渡し、move (= staff 付け替え)
 * を含む任意の event 更新を扱えるようにする。
 *
 * Optimistic update は staffId が変わる D&D move のとき rollback 復元の
 * 整合性が取りにくい (= 元 staff から消える + 新 staff に出現する 2 段階)
 * ため簡略化し、success / settled で `staff/events` 全体を invalidate する。
 */
export function useUpdateEventForDrag(): UseMutationResult<
  EventRead,
  Error,
  { staffId: string; eventId: string; payload: EventUpdate }
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<EventRead, Error, { staffId: string; eventId: string; payload: EventUpdate }>({
    mutationFn: async ({ staffId, eventId, payload }) => {
      const parsed = eventUpdateSchema.parse(payload);
      return fetcher<EventRead>(`${staffEventsBase(staffId)}/${eventId}`, {
        method: 'PATCH',
        body: JSON.stringify(parsed),
        accessToken,
        refreshToken,
      });
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: STAFF_EVENTS_KEY });
    },
  });
}

/** DELETE .../events/{event_id} — soft-removal (optimistic). */
export function useDeleteEvent(
  staffId: string,
): UseMutationResult<
  void,
  Error,
  string,
  { previous: Array<[readonly unknown[], EventRead[] | undefined]> }
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<
    void,
    Error,
    string,
    { previous: Array<[readonly unknown[], EventRead[] | undefined]> }
  >({
    mutationFn: async (eventId) => {
      await fetcher<void>(`${staffEventsBase(staffId)}/${eventId}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    onMutate: async (eventId) => {
      await qc.cancelQueries({ queryKey: [...STAFF_EVENTS_KEY, staffId] });
      const snapshots = qc.getQueriesData<EventRead[]>({
        queryKey: [...STAFF_EVENTS_KEY, staffId],
      });
      for (const [key, list] of snapshots) {
        if (!list) continue;
        qc.setQueryData<EventRead[]>(
          key,
          list.filter((e) => e.id !== eventId),
        );
      }
      return { previous: snapshots };
    },
    onError: (_err, _vars, ctx) => {
      if (!ctx) return;
      for (const [key, value] of ctx.previous) {
        qc.setQueryData(key, value);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({
        queryKey: [...STAFF_EVENTS_KEY, staffId],
      });
    },
  });
}
