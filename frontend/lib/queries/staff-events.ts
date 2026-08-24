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
import { cockpitEventReadSchema, type CockpitEventRead } from '@/lib/schemas/v2/cockpit';

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

/**
 * 片側だけの期間指定 (「過去」タブ = to のみ)。BE は from/to をそれぞれ
 * 独立した任意パラメータとして扱う。
 */
export type PartialDateRange = Partial<DateRange>;

/**
 * 一覧の絞り込みパラメータ (staff-event-history-design.md §2 Phase 1)。
 * **絞り込みは BE で行う** — limit 200 の窓を FE 側で削ると「過去」が
 * 取り切れないため。値が空/既定のキーは URL にもクエリキーにも載せない。
 */
export interface StaffEventFilters {
  /** title / note の部分一致 (ILIKE)。 */
  q?: string;
  /** 出所の完全一致。 */
  source?: 'manual' | 'kaipoke' | 'fixed' | null;
  /** event_type の完全一致 ('training' = 研修のみ)。 */
  type?: 'event' | 'training' | null;
  /** starts_at の並び順。既定 'asc'。 */
  order?: 'asc' | 'desc';
  /** 定例 (source='fixed' + 固定イベント既定のタイトル) を除外する。 */
  hideRegular?: boolean;
}

/**
 * 空値を落として安定した形に正規化する。無指定と「空文字/false 指定」が
 * 同一のクエリキーになるので、絞り込み解除時に余計な再取得が起きない。
 */
function normalizeFilters(filters?: StaffEventFilters): Record<string, string> {
  const out: Record<string, string> = {};
  if (!filters) return out;
  const q = filters.q?.trim();
  if (q) out.q = q;
  if (filters.source) out.source = filters.source;
  if (filters.type) out.type = filters.type;
  if (filters.order === 'desc') out.order = 'desc';
  if (filters.hideRegular) out.hide_regular = 'true';
  return out;
}

/**
 * GET .../events の URL を組み立てる (テストから直接検証できるよう export)。
 * 絞り込みは全てここでクエリパラメータに載る = **BE 側で絞る** の実装点。
 */
export function buildListUrl(
  staffId: string,
  range?: PartialDateRange,
  filters?: StaffEventFilters,
): string {
  const qs = new URLSearchParams();
  if (range?.from) qs.set('from', range.from);
  if (range?.to) qs.set('to', range.to);
  for (const [k, v] of Object.entries(normalizeFilters(filters))) qs.set(k, v);
  const suffix = qs.toString();
  return suffix ? `${staffEventsBase(staffId)}?${suffix}` : staffEventsBase(staffId);
}

/** GET .../events — list (optionally filtered by date range / search / source). */
export function useStaffEvents(
  staffId: string | null | undefined,
  range?: PartialDateRange,
  filters?: StaffEventFilters,
): UseQueryResult<EventRead[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const normalizedId = staffId ?? '__none__';
  const filterKey = normalizeFilters(filters);

  return useQuery<EventRead[], Error>({
    queryKey: [...STAFF_EVENTS_KEY, normalizedId, range ?? null, filterKey],
    enabled: status === 'authenticated' && !!staffId,
    queryFn: () => {
      if (!staffId) throw new Error('staff id is required');
      return fetcher<EventRead[]>(buildListUrl(staffId, range, filters), {
        accessToken,
        refreshToken,
      });
    },
  });
}

/**
 * 週イベントの寛容パース。1 行でも壊れていると週の盤面が丸ごと消えるのは
 * 割に合わないため、**不正な行だけ捨てて warn** し、残りを返す
 * (BE の項目追加/欠落に耐える)。配列でない応答は空配列扱い。
 */
export function parseWeekEvents(raw: unknown, staffId?: string): CockpitEventRead[] {
  if (!Array.isArray(raw)) {
    console.warn('[staff-events] 応答が配列ではありません', { staffId });
    return [];
  }
  const out: CockpitEventRead[] = [];
  for (const row of raw) {
    const parsed = cockpitEventReadSchema.safeParse(row);
    if (parsed.success) {
      out.push(parsed.data);
    } else {
      console.warn('[staff-events] 読めないイベント行を無視しました', {
        staffId,
        issues: parsed.error.issues,
      });
    }
  }
  return out;
}

/**
 * Wave 27 Phase B-1: 週単位で複数 staff の events を並列バッチ取得する hook.
 *
 * 戻り値は `staffId` の順番に対応した CockpitEventRead[][] (空配列でフォールバック)。
 * `staffEventsByStaff` Map に変換して担当 dropdown / セル警告で利用する。
 *
 * 週空間 Phase E (2026-08-22): `cockpitEventReadSchema` で parse し、
 * `cancelled_at`(今週だけ外す) / `source`('fixed'=固定イベント) / `external_id`
 * を型に載せる。BE が未対応の項目は既定値で埋まるので旧環境でも壊れない。
 */
export function useWeekStaffEvents(
  staffIds: string[],
  weekStart: Date,
  weekEnd: Date,
): { data: CockpitEventRead[][]; isLoading: boolean } {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  const fromStr = weekStart.toISOString().slice(0, 10);
  const toStr = weekEnd.toISOString().slice(0, 10);

  const results = useQueries({
    queries: staffIds.map((id) => ({
      queryKey: [...STAFF_EVENTS_KEY, id, fromStr, toStr] as const,
      enabled: status === 'authenticated' && !!id,
      queryFn: async () => {
        const raw = await fetcher<unknown[]>(buildListUrl(id, { from: fromStr, to: toStr }), {
          accessToken,
          refreshToken,
        });
        return parseWeekEvents(raw, id);
      },
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
export function buildStaffEventsMap<T extends EventRead>(
  staffIds: string[],
  events: T[][],
): Map<string, T[]> {
  const m = new Map<string, T[]>();
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
