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
 *   POST /api/v1/visits/{id}/checkin       QR チェックイン Phase 1 で稼働済み
 *   POST /api/v1/visits/{id}/checkout      QR チェックイン Phase 1 で稼働済み
 *
 * The check-in / check-out / no-show routes ship in the QR-checkin Phase 1
 * backend. The localStorage fallback in `m/today/[visitId]/page.tsx` is no
 * longer a "route not implemented" stopgap — it is offline insurance for true
 * network failures / 5xx (server unreachable), paired with a pending re-send
 * queue (`lib/checkin-queue.ts`). 404 / 409 are real errors (invalid / wrong
 * QR) and are surfaced to the user instead of being stashed.
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

import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';
import { ADHOC_CHECKIN_PATH } from '@/lib/checkin-flush';
import type { StaffRead, StaffShift } from '@/lib/schemas/staff';

/** Server-side judgement of the position match (QR checkin Phase 1). */
export type CheckinMatchStatus = 'match' | 'review' | 'mismatch' | 'no_gps';

/**
 * Projection of the latest checkin row, attached non-destructively to
 * `VisitRead.latest_checkin` by the backend. `null` when the visit has no
 * checkin yet. Coordinates are intentionally NOT returned by the server
 * (APPI / privacy); only the derived `distance_m` / `match_status`.
 */
export interface LatestCheckin {
  id: string;
  /** 打刻種別。 */
  kind: 'arrival' | 'departure' | 'no_show';
  match_status: CheckinMatchStatus;
  distance_m: number | null;
  accuracy_m: number | null;
  /** Server receive time (ISO 8601). */
  scanned_at: string;
  /** 'qr' | 'manual'. */
  checkin_source: string;
  reason: string | null;
  is_override: boolean;
}

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
  /**
   * Patient geocode (non-destructive add). Used by the mobile QR check-in to
   * preview the distance client-side BEFORE recording. `null` when the patient
   * isn't geocoded (the UI then previews `no_gps` and still allows the POST).
   */
  patient_lat?: number | null;
  patient_lng?: number | null;
  /** R-9: 訪問カードの性別ウォッシュ/📍住所用 (非破壊追加・旧デプロイは undefined)。 */
  patient_sex?: string | null;
  patient_address?: string | null;
  /**
   * Latest checkin projection. Optional / nullable so older deployments that
   * don't yet send it (or list endpoints that omit it) keep type-checking.
   */
  latest_checkin?: LatestCheckin | null;
  /**
   * 同行 (§7.4): この訪問に同行するスタッフ (単数・後方互換)。null = 同行なし。
   * 複数名いる場合は `accompaniments` の先頭 1 名。新規実装は `accompaniments` を
   * 優先し、これは旧デプロイ向けのフォールバックに使う。
   */
  accompaniment?: { staff_id: string; staff_name: string | null } | null;
  /**
   * 同行スタッフ全員 (一般化・確定#5)。担当側は「同行: ◯◯・◯◯」、同行者本人
   * (自分の staff_id を含む) は「同行」バッジを出す。BE `_serialize_visit` が
   * 非破壊追加 (旧デプロイは undefined → accompaniment 単数へフォールバック)。
   */
  accompaniments?:
    | { staff_id: string; staff_name: string | null; kind?: 'trainee' | 'support' | null }[]
    | null;
  /**
   * `visit_staff_assignments` 経由の割当スタッフ全員 (§4.5)。
   *
   * primary/secondary/mentor に載らず**この一覧だけで担当**しているスタッフが居る
   * ため、担当判定 (代行モード) に必要。BE `_serialize_visit` が常に返す (非破壊
   * 追加・旧デプロイは undefined)。担当外の QR capability GET では空配列に落として
   * 返されるので、真の担当外がこれで担当と誤判定されることはない。
   */
  staff_assignments?: { visit_id: string; staff_id: string; assigned_at: string }[] | null;
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
  const dt = new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1);
  dt.setDate(dt.getDate() + days);
  const yyyy = dt.getFullYear();
  const mm = String(dt.getMonth() + 1).padStart(2, '0');
  const dd = String(dt.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

/** GET /api/v1/visits → my visits, optionally filtered by date or week. */
export function useMyVisits(params: UseMyVisitsParams = {}): UseQueryResult<MyVisit[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken, staffId } = authPair(session);

  // Resolve server-side `week_start` / `week_end` (inclusive) so the backend
  // narrows the result set instead of relying on a client-side slice. When a
  // single `date` is provided we collapse the range to that one day; when a
  // `weekStart` is provided we expand to a 7-day window. Explicit
  // `weekStartFilter` / `weekEndFilter` always win.
  const serverWeekStart = params.weekStartFilter ?? params.date ?? params.weekStart ?? null;
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
      const mine = await fetcher<MyVisit[]>(`/api/v1/visits?${qs.toString()}`, {
        accessToken,
        refreshToken,
      });
      // Belt-and-braces client-side narrowing in case the server returns a
      // wider window than we asked for (older deployments, caching, etc.).
      if (params.date) {
        return mine.filter((v) => v.visit_date === params.date);
      }
      if (params.weekStart) {
        const end = addDays(params.weekStart, 7); // exclusive
        return mine.filter((v) => v.visit_date >= params.weekStart! && v.visit_date < end);
      }
      return mine;
    },
  });
}

/**
 * GET /api/v1/visits/{id} — single visit detail (auth-scoped on the server).
 *
 * `qrToken` を渡すと**担当外フォールバック**が効く: 通常の GET が 404 (担当外は
 * 秘匿されたまま = 設計 §1 決定#1) のとき、QR トークンを鍵に取り直す。トークンが
 * その visit の患者に解決できればサーバは 200 を返す (設計 §4-2)。これにより
 * 代行スタッフでも訪問詳細 → 打刻の導線が 1 本で通る。
 *
 * トークンは**ヘッダ `X-QR-Token` で送る**。クエリ (`?qr_token=`) だと URL が
 * アクセスログ・Referer・ブラウザ履歴に残り、現地証明の鍵が漏れる (BEレビュー
 * 指摘)。サーバはクエリも後方互換で受けるが、FE からは送らない。
 */
const QR_TOKEN_HEADER = 'X-QR-Token';
export function useMyVisit(
  visitId: string | null | undefined,
  qrToken?: string | null,
): UseQueryResult<MyVisit, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<MyVisit, Error>({
    queryKey: [...ME_KEY, 'visit', visitId ?? null, qrToken ?? null],
    enabled: status === 'authenticated' && !!visitId,
    queryFn: async () => {
      if (!visitId) throw new Error('visitId is required');
      try {
        return await fetcher<MyVisit>(`/api/v1/visits/${visitId}`, {
          accessToken,
          refreshToken,
        });
      } catch (err) {
        // 担当外 (404) + QR 所持 → トークンを鍵に取り直す。それ以外はそのまま。
        if (!qrToken || !(err instanceof ApiError) || err.status !== 404) throw err;
        return fetcher<MyVisit>(`/api/v1/visits/${visitId}`, {
          accessToken,
          refreshToken,
          headers: { [QR_TOKEN_HEADER]: qrToken },
        });
      }
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

export function useMyShifts(_weekday?: number): UseQueryResult<UseMyShiftsResult, Error> {
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

/** Check-in / check-out payload (QR checkin Phase 2). */
export interface CheckInPayload {
  /**
   * Token extracted from the patient-home QR (`/q/{token}`). Omitted for a
   * manual check-in — the backend then resolves the patient from
   * `visit.patient_id` and records `checkin_source='manual'`.
   */
  qr_token?: string;
  lat?: number;
  lng?: number;
  /** GPS accuracy in metres (best-effort). */
  accuracy?: number;
  /** Reason text (location mismatch / free note). */
  reason?: string;
  /** Force-record despite a mismatch. */
  is_override?: boolean;
  /** Client-side timestamp (ISO 8601). Backend reads it as `device_time`. */
  at: string;
}

/**
 * 予定外訪問の到着 payload (設計 §4-3)。`qr_token` は必須 — 患者を特定する唯一の
 * 鍵であり、担当外の記録に QR を必須とする決定#6 のサーバ側強制点でもある。
 */
export interface AdhocCheckinPayload extends CheckInPayload {
  qr_token: string;
}

/**
 * POST /api/v1/visits/adhoc-checkin — 予定外訪問。
 *
 * サーバが `is_unplanned` visit を生成し、到着打刻まで済ませた `VisitRead` を返す
 * (以後は自分の visit なので、退出は通常の checkout で通る)。同患者×同スタッフ×当日で
 * in_progress の予定外 visit が既にあれば、それを返す (再スキャンで増殖しない)。
 */
export function useAdhocCheckin(): UseMutationResult<MyVisit, Error, AdhocCheckinPayload> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<MyVisit, Error, AdhocCheckinPayload>({
    mutationFn: (payload) =>
      fetcher<MyVisit>(ADHOC_CHECKIN_PATH, {
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

/** No-show payload — reason is required by the backend. */
export interface NoShowPayload {
  reason: string;
  lat?: number;
  lng?: number;
  /**
   * Client-side timestamp (ISO 8601). Backend reads it as `device_time`.
   * Symmetric with the offline re-send queue payload so an online no-show and a
   * re-sent one persist the same device time.
   */
  at?: string;
}

/**
 * POST /api/v1/visits/{id}/checkin
 *
 * Backend (QR checkin Phase 1) records the arrival and returns the updated
 * `VisitRead` with the server-judged `latest_checkin` (distance / match_status).
 * `qr_token` is optional — omit it for a manual check-in. Callers still fall
 * back to a localStorage record on 404/405/501/network errors so a tap
 * survives navigation when the route is unreachable (offline insurance).
 */
export function useCheckIn(visitId: string): UseMutationResult<MyVisit, Error, CheckInPayload> {
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
export function useCheckOut(visitId: string): UseMutationResult<MyVisit, Error, CheckInPayload> {
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

/**
 * POST /api/v1/visits/{id}/no-show — record an un-visited stop with a reason.
 *
 * `reason` is required (the backend returns 422 otherwise). Coordinates are
 * best-effort. Returns the updated `VisitRead` (status stays `planned`; the
 * monitor derives "missing" from time).
 */
export function useNoShow(visitId: string): UseMutationResult<MyVisit, Error, NoShowPayload> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<MyVisit, Error, NoShowPayload>({
    mutationFn: (payload) =>
      fetcher<MyVisit>(`/api/v1/visits/${visitId}/no-show`, {
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
