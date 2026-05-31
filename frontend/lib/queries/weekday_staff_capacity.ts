'use client';

/**
 * useWeekdayStaffCapacity — 週ビューのコース「休」/定員をスタッフ数連動にする
 * ための read-only query hook.
 *
 * GET /api/v1/schedule/v2/weekday-staff-capacity?iso_year=&iso_week=&office_id=
 *
 * レスポンス: (office_id, weekday) ごとの稼働 staff 数
 * (role='staff', trainee 除外, shift/override/応援/営業日 考慮済 =
 * backend ``count_active_staff_per_weekday``). staff_count > 0 のキーのみ含む.
 *
 * 週ビュー (CourseWeekOverview / CourseDayTablePanel) は
 *   effectiveCapacity(tpl, weekday, staffCount, course_codes_max)
 * で A-E コースの開講 (定員6) / 休 (定員0) を判定する (auto_allocator_v2 Stage 4
 * と同一ロジック). M系コースは引き続き静的 capacity_<曜日> を使う.
 *
 * RBAC: admin / manager のみ (BE 側で 403 担保).
 */
import { useMemo } from 'react';
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import { COURSE_CODES_MAX } from '@/lib/schemas/v2/course_template';

const WEEKDAY_STAFF_CAPACITY_PATH = '/api/v1/schedule/v2/weekday-staff-capacity';

/** BE ``WeekdayStaffCapacityItem`` とミラー. */
export interface WeekdayStaffCapacityItem {
  office_id: string;
  /** 0=Mon..6=Sun. */
  weekday: number;
  staff_count: number;
}

/** BE ``WeekdayStaffCapacityResponse`` とミラー. */
export interface WeekdayStaffCapacityResponse {
  items: WeekdayStaffCapacityItem[];
  /** A-E 通常コースの発行上限 (= backend _COURSE_CODES_MAX = 5). */
  course_codes_max: number;
}

export interface WeekdayStaffCapacityParams {
  iso_year: number;
  iso_week: number;
  /** null / undefined なら全拠点. */
  office_id?: string | null;
}

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/**
 * (office_id, weekday) → staff_count を引く軽量ルックアップ + course_codes_max.
 * `staffCountFor(officeId, weekday)` は未存在キーを 0 として返す.
 */
export interface WeekdayStaffCapacityLookup {
  staffCountFor: (officeId: string, weekday: number) => number;
  courseCodesMax: number;
  isLoading: boolean;
}

/** GET /api/v1/schedule/v2/weekday-staff-capacity. */
export function useWeekdayStaffCapacity(
  params: WeekdayStaffCapacityParams,
): UseQueryResult<WeekdayStaffCapacityResponse, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  const qs = new URLSearchParams();
  qs.set('iso_year', String(params.iso_year));
  qs.set('iso_week', String(params.iso_week));
  if (params.office_id) qs.set('office_id', params.office_id);

  return useQuery<WeekdayStaffCapacityResponse, Error>({
    queryKey: [
      'weekday-staff-capacity',
      params.iso_year,
      params.iso_week,
      params.office_id ?? null,
    ],
    enabled: status === 'authenticated',
    queryFn: () =>
      fetcher<WeekdayStaffCapacityResponse>(`${WEEKDAY_STAFF_CAPACITY_PATH}?${qs.toString()}`, {
        accessToken,
        refreshToken,
      }),
  });
}

/**
 * useWeekdayStaffCapacity を (office_id, weekday) → staff_count ルックアップに
 * 変換した便利フック. 週ビューの effectiveCapacity 呼出に直接渡せる.
 */
export function useWeekdayStaffCapacityLookup(
  params: WeekdayStaffCapacityParams,
): WeekdayStaffCapacityLookup {
  const query = useWeekdayStaffCapacity(params);

  return useMemo(() => {
    const data = query.data;
    const m = new Map<string, number>();
    for (const it of data?.items ?? []) {
      m.set(`${it.office_id}:${it.weekday}`, it.staff_count);
    }
    return {
      staffCountFor: (officeId: string, weekday: number) => m.get(`${officeId}:${weekday}`) ?? 0,
      courseCodesMax: data?.course_codes_max ?? COURSE_CODES_MAX,
      isLoading: query.isLoading,
    };
  }, [query.data, query.isLoading]);
}
