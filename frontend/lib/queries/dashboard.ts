/**
 * TanStack Query hooks for the dashboard aggregation endpoints.
 *
 * Backend (`backend/app/api/v1/dashboard.py`)
 * ───────────────────────────────────────────
 *   GET /api/v1/dashboard/kpi
 *   GET /api/v1/dashboard/trend?days=N
 *
 * Both endpoints are role-aware on the backend (staff sees only their own
 * primary-staff visits), so the UI does NOT need to filter further.
 */
'use client';

import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';

export interface DashboardKpi {
  today_visits: number;
  today_completed: number;
  today_unassigned: number;
  today_overlapping: number;
  this_week_visits: number;
  /** 0.0 – 1.0; 0.0 when there are no visits this week. */
  this_week_completion_rate: number;
}

export interface DashboardTrendItem {
  /** ISO date (YYYY-MM-DD). */
  date: string;
  total: number;
  completed: number;
  unassigned: number;
}

export interface DashboardTrend {
  items: DashboardTrendItem[];
  days: number;
  start_date: string;
  end_date: string;
}

const DASHBOARD_KEY = ['dashboard'] as const;

function authPair(session: ReturnType<typeof useSession>['data']): {
  accessToken: string | null;
  refreshToken: string | null;
} {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/** GET /api/v1/dashboard/kpi — today + this-week KPI roll-up. */
export function useDashboardKpi(): UseQueryResult<DashboardKpi, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const userId = session?.user?.id ?? null;

  return useQuery<DashboardKpi, Error>({
    queryKey: [...DASHBOARD_KEY, 'kpi', userId],
    enabled: status === 'authenticated',
    // KPI numbers don't need to be live-fresh; refetch every minute.
    refetchInterval: 60_000,
    queryFn: () =>
      fetcher<DashboardKpi>('/api/v1/dashboard/kpi', { accessToken, refreshToken }),
  });
}

/** GET /api/v1/dashboard/trend?days=N — daily trend, oldest → newest. */
export function useDashboardTrend(
  days: number = 7,
): UseQueryResult<DashboardTrend, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const userId = session?.user?.id ?? null;
  const safeDays = Math.max(1, Math.min(90, Math.floor(days)));

  return useQuery<DashboardTrend, Error>({
    queryKey: [...DASHBOARD_KEY, 'trend', safeDays, userId],
    enabled: status === 'authenticated',
    refetchInterval: 60_000,
    queryFn: () =>
      fetcher<DashboardTrend>(`/api/v1/dashboard/trend?days=${safeDays}`, {
        accessToken,
        refreshToken,
      }),
  });
}
