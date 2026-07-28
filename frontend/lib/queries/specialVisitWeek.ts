'use client';

/**
 * 特別訪問週間 (Special Visit Week) の TanStack Query hooks.
 *
 * 設計書: `docs/plans/special-visit-week-design.md` §4 (API 契約) / §6 (FE)。
 * 書き方の流儀は `lib/queries/staff-events.ts` を踏襲 (fetcher + useSession の
 * accessToken/refreshToken ペア + onSuccess で invalidate)。
 *
 * RBAC: すべて admin / manager (BE が require_role で担保)。
 *
 * invalidate 方針:
 *   - 期間/マーク系 → calendar + periods + pool
 *   - place (配置) → 上記に加えて visits / courses / field-board (盤面が変わるため)
 */
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
  specialPoolTicketListSchema,
  specialVisitCalendarSchema,
  specialVisitMarkCreateSchema,
  specialVisitMarkSchema,
  specialVisitPeriodCreateSchema,
  specialVisitPeriodListSchema,
  specialVisitPeriodSchema,
  specialVisitPeriodUpdateSchema,
  specialVisitPlaceResponseSchema,
  specialVisitPlaceSchema,
  type SpecialPoolTicket,
  type SpecialVisitCalendar,
  type SpecialVisitDisplace,
  type SpecialVisitMark,
  type SpecialVisitMarkCreate,
  type SpecialVisitPeriod,
  type SpecialVisitPeriodCreate,
  type SpecialVisitPeriodUpdate,
  type SpecialVisitPlace,
  type SpecialVisitPlaceResponse,
} from '@/lib/schemas/specialVisitWeek';

const PERIODS_PATH = '/api/v1/special-visit-periods';
const MARKS_PATH = '/api/v1/special-visit-marks';

/** 期間一覧 / カレンダーの共通ルートキー. */
export const SPECIAL_VISIT_KEY = ['special-visit'] as const;
export const SPECIAL_VISIT_PERIODS_KEY = [...SPECIAL_VISIT_KEY, 'periods'] as const;
export const SPECIAL_VISIT_CALENDAR_KEY = [...SPECIAL_VISIT_KEY, 'calendar'] as const;
export const SPECIAL_VISIT_POOL_KEY = [...SPECIAL_VISIT_KEY, 'pool'] as const;

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/** 期間・マークを触ったら必ず呼ぶ (カレンダー / 期間一覧 / プールを最新化). */
function invalidateSpecialVisit(qc: ReturnType<typeof useQueryClient>): void {
  void qc.invalidateQueries({ queryKey: SPECIAL_VISIT_KEY });
}

/** 盤面 (visits / courses / field-board) まで波及する操作用. */
function invalidateBoard(qc: ReturnType<typeof useQueryClient>): void {
  void qc.invalidateQueries({ queryKey: ['visits'] });
  void qc.invalidateQueries({ queryKey: ['courses'] });
  void qc.invalidateQueries({ queryKey: ['field-board'] });
}

// ---------------------------------------------------------------------------
// 期間 (periods)
// ---------------------------------------------------------------------------

/** GET /special-visit-periods?patient_id=…&include_inactive= */
export function useSpecialVisitPeriods(
  patientId: string | null | undefined,
  includeInactive = false,
): UseQueryResult<SpecialVisitPeriod[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<SpecialVisitPeriod[], Error>({
    queryKey: [...SPECIAL_VISIT_PERIODS_KEY, patientId ?? '__none__', includeInactive],
    enabled: status === 'authenticated' && !!patientId,
    queryFn: async () => {
      if (!patientId) throw new Error('patient id is required');
      const qs = new URLSearchParams({ patient_id: patientId });
      if (includeInactive) qs.set('include_inactive', 'true');
      const raw = await fetcher<unknown>(`${PERIODS_PATH}?${qs.toString()}`, {
        accessToken,
        refreshToken,
      });
      return specialVisitPeriodListSchema.parse(raw);
    },
  });
}

/** POST /special-visit-periods — 期間を作成する (active 重複は BE が 422). */
export function useCreateSpecialVisitPeriod(): UseMutationResult<
  SpecialVisitPeriod,
  Error,
  SpecialVisitPeriodCreate
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<SpecialVisitPeriod, Error, SpecialVisitPeriodCreate>({
    mutationFn: async (values) => {
      const parsed = specialVisitPeriodCreateSchema.parse(values);
      const raw = await fetcher<unknown>(PERIODS_PATH, {
        method: 'POST',
        body: JSON.stringify(parsed),
        accessToken,
        refreshToken,
      });
      return specialVisitPeriodSchema.parse(raw);
    },
    onSuccess: () => invalidateSpecialVisit(qc),
  });
}

/** PATCH /special-visit-periods/{id} — 目標変更 / 延長 / 終了. */
export function useUpdateSpecialVisitPeriod(): UseMutationResult<
  SpecialVisitPeriod,
  Error,
  { periodId: string; payload: SpecialVisitPeriodUpdate }
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<
    SpecialVisitPeriod,
    Error,
    { periodId: string; payload: SpecialVisitPeriodUpdate }
  >({
    mutationFn: async ({ periodId, payload }) => {
      const parsed = specialVisitPeriodUpdateSchema.parse(payload);
      const raw = await fetcher<unknown>(`${PERIODS_PATH}/${periodId}`, {
        method: 'PATCH',
        body: JSON.stringify(parsed),
        accessToken,
        refreshToken,
      });
      return specialVisitPeriodSchema.parse(raw);
    },
    onSuccess: () => invalidateSpecialVisit(qc),
  });
}

// ---------------------------------------------------------------------------
// カレンダー
// ---------------------------------------------------------------------------

/** GET /special-visit-periods/{id}/calendar — 期間内の全 ISO 週 × 月〜土. */
export function useSpecialVisitCalendar(
  periodId: string | null | undefined,
): UseQueryResult<SpecialVisitCalendar, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<SpecialVisitCalendar, Error>({
    queryKey: [...SPECIAL_VISIT_CALENDAR_KEY, periodId ?? '__none__'],
    enabled: status === 'authenticated' && !!periodId,
    queryFn: async () => {
      if (!periodId) throw new Error('period id is required');
      const raw = await fetcher<unknown>(`${PERIODS_PATH}/${periodId}/calendar`, {
        accessToken,
        refreshToken,
      });
      return specialVisitCalendarSchema.parse(raw);
    },
  });
}

// ---------------------------------------------------------------------------
// マーク (○追加 / 取消 / 退避 / 復元)
// ---------------------------------------------------------------------------

/** POST /special-visit-periods/{id}/marks — ○を 1 セル追加する (kind='extra'). */
export function useCreateSpecialVisitMark(): UseMutationResult<
  SpecialVisitMark,
  Error,
  { periodId: string; payload: SpecialVisitMarkCreate }
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<
    SpecialVisitMark,
    Error,
    { periodId: string; payload: SpecialVisitMarkCreate }
  >({
    mutationFn: async ({ periodId, payload }) => {
      const parsed = specialVisitMarkCreateSchema.parse(payload);
      const raw = await fetcher<unknown>(`${PERIODS_PATH}/${periodId}/marks`, {
        method: 'POST',
        body: JSON.stringify(parsed),
        accessToken,
        refreshToken,
      });
      return specialVisitMarkSchema.parse(raw);
    },
    onSuccess: () => invalidateSpecialVisit(qc),
  });
}

/**
 * DELETE /special-visit-marks/{id} — ○の取消。
 * 配置済み (status='placed') は `force=true` で配置先訪問も soft-delete される。
 */
export function useDeleteSpecialVisitMark(): UseMutationResult<
  void,
  Error,
  { markId: string; force?: boolean }
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<void, Error, { markId: string; force?: boolean }>({
    mutationFn: async ({ markId, force }) => {
      const url = force ? `${MARKS_PATH}/${markId}?force=true` : `${MARKS_PATH}/${markId}`;
      await fetcher<void>(url, { method: 'DELETE', accessToken, refreshToken });
    },
    onSuccess: () => {
      invalidateSpecialVisit(qc);
      invalidateBoard(qc);
    },
  });
}

/**
 * POST /special-visit-periods/{id}/displace — その日の固定訪問をプールへ退避する。
 * 生成済み週は訪問が soft-delete されるため盤面も invalidate する。
 */
export function useDisplaceSpecialVisit(): UseMutationResult<
  SpecialVisitMark,
  Error,
  { periodId: string; payload: SpecialVisitDisplace }
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<SpecialVisitMark, Error, { periodId: string; payload: SpecialVisitDisplace }>({
    mutationFn: async ({ periodId, payload }) => {
      const parsed = specialVisitMarkCreateSchema.parse(payload);
      const raw = await fetcher<unknown>(`${PERIODS_PATH}/${periodId}/displace`, {
        method: 'POST',
        body: JSON.stringify(parsed),
        accessToken,
        refreshToken,
      });
      return specialVisitMarkSchema.parse(raw);
    },
    onSuccess: () => {
      invalidateSpecialVisit(qc);
      invalidateBoard(qc);
    },
  });
}

/**
 * POST /special-visit-marks/{id}/restore — 退避を解除する (「固定どおり」へ戻す)。
 * 配置済み (placed) の解除は `force=true` 必須。force 無しは BE が 409 を返すので、
 * 呼出側は確認ダイアログを出してから force 付きで再実行する。
 */
export function useRestoreSpecialVisitMark(): UseMutationResult<
  SpecialVisitMark | null,
  Error,
  { markId: string; force?: boolean }
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<SpecialVisitMark | null, Error, { markId: string; force?: boolean }>({
    mutationFn: async ({ markId, force }) => {
      const url = force
        ? `${MARKS_PATH}/${markId}/restore?force=true`
        : `${MARKS_PATH}/${markId}/restore`;
      const raw = await fetcher<unknown>(url, { method: 'POST', accessToken, refreshToken });
      const parsed = specialVisitMarkSchema.safeParse(raw);
      return parsed.success ? parsed.data : null;
    },
    onSuccess: () => {
      invalidateSpecialVisit(qc);
      invalidateBoard(qc);
    },
  });
}

// ---------------------------------------------------------------------------
// プール (§6-2 — PoolPanel 側の別担当が使う)
// ---------------------------------------------------------------------------

/** GET /special-visit-marks/pool?iso_year&iso_week&office_id? — 表示中の週のチケット一覧. */
export function useSpecialVisitPool(
  isoYear: number | null | undefined,
  isoWeek: number | null | undefined,
  officeId?: string | null,
): UseQueryResult<SpecialPoolTicket[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const enabled =
    status === 'authenticated' && typeof isoYear === 'number' && typeof isoWeek === 'number';

  return useQuery<SpecialPoolTicket[], Error>({
    queryKey: [...SPECIAL_VISIT_POOL_KEY, isoYear ?? null, isoWeek ?? null, officeId ?? null],
    enabled,
    queryFn: async () => {
      const qs = new URLSearchParams({
        iso_year: String(isoYear),
        iso_week: String(isoWeek),
      });
      if (officeId) qs.set('office_id', officeId);
      const raw = await fetcher<unknown>(`${MARKS_PATH}/pool?${qs.toString()}`, {
        accessToken,
        refreshToken,
      });
      return specialPoolTicketListSchema.parse(raw);
    },
  });
}

/**
 * POST /special-visit-marks/{id}/place — チケットをその週のコースへ配置する。
 * PFV は作らない (この週のみ) が visit 行は増えるので盤面 (visits/board/pool) を
 * すべて invalidate する。
 *
 * payload のコース指定は `course_id` 直指定か `(office_id + course_code)` のどちらか
 * (§6-2 の配置フローは propose-slots 候補から後者を組み立てる)。
 */
export function usePlaceSpecialMark(): UseMutationResult<
  SpecialVisitPlaceResponse,
  Error,
  { markId: string; payload: SpecialVisitPlace }
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<
    SpecialVisitPlaceResponse,
    Error,
    { markId: string; payload: SpecialVisitPlace }
  >({
    mutationFn: async ({ markId, payload }) => {
      const parsed = specialVisitPlaceSchema.parse(payload);
      const raw = await fetcher<unknown>(`${MARKS_PATH}/${markId}/place`, {
        method: 'POST',
        body: JSON.stringify(parsed),
        accessToken,
        refreshToken,
      });
      return specialVisitPlaceResponseSchema.parse(raw);
    },
    onSuccess: () => {
      invalidateSpecialVisit(qc);
      invalidateBoard(qc);
    },
  });
}
