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
  scheduleHealthResponseSchema,
  type ScheduleHealthResponse,
} from '@/lib/schemas/v2/scheduleHealth';

const SCHEDULE_HEALTH_PATH = '/api/v1/schedule/v2/schedule-health';

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
