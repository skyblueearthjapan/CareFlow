'use client';

/**
 * useScheduleHealth — スケジュール健康診断 (Schedule Advisor Phase 1) の
 * read-only query hook.
 *
 * GET /api/v1/schedule/v2/schedule-health?iso_year=&iso_week=&office_id=(任意)
 *
 * 健康診断ダイアログは当週 + 前週の 2 クエリを張り、前週比 delta を出す.
 * 算出は集計クエリなので staleTime を 5 分取り、同一週・拠点の再 fetch を避ける.
 *
 * BE 仕様: ``docs/plans/schedule-advisor-design.md`` §3 Phase 1 (契約凍結済).
 * RBAC: admin / manager のみ (BE 側で 403 担保).
 */
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  scheduleHealthCourseDetailResponseSchema,
  scheduleHealthResponseSchema,
  scheduleHealthTrendResponseSchema,
  type ScheduleHealthCourseDetailResponse,
  type ScheduleHealthResponse,
  type ScheduleHealthTrendResponse,
} from '@/lib/schemas/v2/scheduleHealth';

const SCHEDULE_HEALTH_PATH = '/api/v1/schedule/v2/schedule-health';
const SCHEDULE_HEALTH_TREND_PATH = '/api/v1/schedule/v2/schedule-health/trend';

export interface UseScheduleHealthParams {
  isoYear: number;
  isoWeek: number;
  /** null / undefined なら全拠点. */
  officeId?: string | null;
  /** false で fetch を止める (前週データ不要時など). */
  enabled?: boolean;
}

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/** GET /api/v1/schedule/v2/schedule-health. */
export function useScheduleHealth(
  params: UseScheduleHealthParams,
): UseQueryResult<ScheduleHealthResponse, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const { isoYear, isoWeek, officeId, enabled } = params;

  const qs = new URLSearchParams();
  qs.set('iso_year', String(isoYear));
  qs.set('iso_week', String(isoWeek));
  if (officeId) qs.set('office_id', officeId);

  return useQuery<ScheduleHealthResponse, Error>({
    queryKey: ['schedule-health', isoYear, isoWeek, officeId ?? null],
    enabled: status === 'authenticated' && (enabled ?? true),
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const result = await fetcher<unknown>(`${SCHEDULE_HEALTH_PATH}?${qs.toString()}`, {
        accessToken,
        refreshToken,
      });
      return scheduleHealthResponseSchema.parse(result);
    },
  });
}

export interface UseScheduleHealthTrendParams {
  isoYear: number;
  isoWeek: number;
  /** 遡る週数 (1..12). 既定 8. */
  weeks?: number;
  /** null / undefined なら全拠点. */
  officeId?: string | null;
  /** false で fetch を止める. */
  enabled?: boolean;
}

/**
 * useScheduleHealthTrend — 見直しどきトレンド (Phase 3) の read-only query hook.
 *
 * GET /api/v1/schedule/v2/schedule-health/trend?iso_year=&iso_week=&weeks=&office_id=(任意)
 *
 * 指定週から遡る週次の office 横断合計 (4 指標) を古→新順で返す. 劣化判定は呼び側
 * (バナー) で行う. トレンドは日単位でしか変わらないため staleTime は 30 分と長め.
 * RBAC: admin / manager (BE 側で 403 担保).
 */
export function useScheduleHealthTrend(
  params: UseScheduleHealthTrendParams,
): UseQueryResult<ScheduleHealthTrendResponse, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const { isoYear, isoWeek, weeks, officeId, enabled } = params;
  const weeksParam = weeks ?? 8;

  const qs = new URLSearchParams();
  qs.set('iso_year', String(isoYear));
  qs.set('iso_week', String(isoWeek));
  qs.set('weeks', String(weeksParam));
  if (officeId) qs.set('office_id', officeId);

  return useQuery<ScheduleHealthTrendResponse, Error>({
    queryKey: ['schedule-health-trend', isoYear, isoWeek, weeksParam, officeId ?? null],
    enabled: status === 'authenticated' && (enabled ?? true),
    staleTime: 30 * 60 * 1000,
    queryFn: async () => {
      const result = await fetcher<unknown>(`${SCHEDULE_HEALTH_TREND_PATH}?${qs.toString()}`, {
        accessToken,
        refreshToken,
      });
      return scheduleHealthTrendResponseSchema.parse(result);
    },
  });
}

export interface UseScheduleHealthCourseDetailParams {
  isoYear: number;
  isoWeek: number;
  officeId: string;
  courseCode: string;
  /** false で fetch を止める (ドリルダウンを開いたときだけ取得). */
  enabled?: boolean;
}

/**
 * useScheduleHealthCourseDetail — H1 原因ドリルダウンの read-only query hook.
 *
 * GET /api/v1/schedule/v2/schedule-health/course-detail
 * 「なぜこのコースが重いのか」= 遷移内訳 + 患者別配置コスト (厳密限界コスト)。
 * コース行を展開したときだけ enabled=true で取得する。
 */
export function useScheduleHealthCourseDetail(
  params: UseScheduleHealthCourseDetailParams,
): UseQueryResult<ScheduleHealthCourseDetailResponse, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const { isoYear, isoWeek, officeId, courseCode, enabled } = params;

  const qs = new URLSearchParams();
  qs.set('iso_year', String(isoYear));
  qs.set('iso_week', String(isoWeek));
  qs.set('office_id', officeId);
  qs.set('course_code', courseCode);

  return useQuery<ScheduleHealthCourseDetailResponse, Error>({
    queryKey: ['schedule-health', 'course-detail', isoYear, isoWeek, officeId, courseCode],
    enabled: status === 'authenticated' && (enabled ?? true) && Boolean(officeId && courseCode),
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const result = await fetcher<unknown>(
        `${SCHEDULE_HEALTH_PATH}/course-detail?${qs.toString()}`,
        { accessToken, refreshToken },
      );
      return scheduleHealthCourseDetailResponseSchema.parse(result);
    },
  });
}
