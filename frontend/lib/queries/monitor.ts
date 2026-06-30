/**
 * TanStack Query hooks for /api/v1/monitor — QR チェックイン Phase 3 (PC 訪問モニター)。
 *
 *   GET /api/v1/monitor?date=&office_id=        → MonitorResponse (60 秒ポーリング)
 *   GET /api/v1/monitor/nearby?lat=&lng=&radius= → NearbyResponse (場所違いの近隣候補)
 *
 * 権限は admin/manager (BE が require_role で担保)。ポーリング間隔は既存の
 * notifications (60s) に倣う。
 */
'use client';

import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  monitorResponseSchema,
  nearbyResponseSchema,
  type MonitorResponse,
  type NearbyResponse,
} from '@/lib/schemas/monitor';

const MONITOR_KEY = ['monitor'] as const;

/** 今日 (JST) の YYYY-MM-DD。 */
function todayJst(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo' }).format(new Date());
}

function authPair(session: ReturnType<typeof useSession>['data']): {
  accessToken: string | null;
  refreshToken: string | null;
} {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

export interface UseMonitorParams {
  /** 対象日 (YYYY-MM-DD)。 */
  date: string;
  /** 拠点 ID (未指定なら全拠点)。 */
  officeId?: string | null;
  /** ポーリング間隔 (ms)。既定 60_000 (60s)。 */
  pollMs?: number;
}

/** GET /api/v1/monitor — 訪問モニター集計 (60 秒ポーリング)。 */
export function useMonitor(params: UseMonitorParams): UseQueryResult<MonitorResponse, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const { date, officeId = null, pollMs = 60_000 } = params;

  // 過去日 (今日以外) を表示しているときはリアルタイム性が不要なのでポーリングを
  // 止める (過去日は実績が確定しており、無駄な refetch を避ける)。
  const isToday = date === todayJst();

  return useQuery<MonitorResponse, Error>({
    queryKey: [...MONITOR_KEY, { date, officeId }],
    enabled: status === 'authenticated' && Boolean(date),
    refetchInterval: isToday ? pollMs : false,
    refetchIntervalInBackground: false,
    queryFn: async () => {
      const qs = new URLSearchParams({ date });
      if (officeId) qs.set('office_id', officeId);
      const raw = await fetcher<unknown>(`/api/v1/monitor?${qs.toString()}`, {
        accessToken,
        refreshToken,
      });
      return monitorResponseSchema.parse(raw);
    },
  });
}

export interface UseNearbyParams {
  lat: number | null | undefined;
  lng: number | null | undefined;
  radiusM?: number;
  limit?: number;
  enabled?: boolean;
}

/**
 * GET /api/v1/monitor/nearby — 指定座標付近の患者宅候補。
 * 場所違い (mismatch) の実 GPS 座標が確定したときだけ有効化する。
 */
export function useNearbyPatients(params: UseNearbyParams): UseQueryResult<NearbyResponse, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const { lat, lng, radiusM = 150, limit = 5, enabled = true } = params;
  const hasCoords = typeof lat === 'number' && typeof lng === 'number';

  return useQuery<NearbyResponse, Error>({
    queryKey: [...MONITOR_KEY, 'nearby', { lat, lng, radiusM, limit }],
    enabled: status === 'authenticated' && enabled && hasCoords,
    queryFn: async () => {
      const qs = new URLSearchParams({
        lat: String(lat),
        lng: String(lng),
        radius_m: String(radiusM),
        limit: String(limit),
      });
      const raw = await fetcher<unknown>(`/api/v1/monitor/nearby?${qs.toString()}`, {
        accessToken,
        refreshToken,
      });
      return nearbyResponseSchema.parse(raw);
    },
  });
}
