'use client';

import { useQuery } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';

export const AGENT_FEATURE_KEY = 'rakusuke_agent';

interface FlagRow {
  feature_key?: string;
  enabled_at?: string | null;
}

/**
 * 機能の旗 `rakusuke_agent`(設計書 §4-5)。既存の GET /api/v1/office-feature-flags を使う。
 * どれかの拠点で有効(enabled_at が入っている)なら true。取れない・エラーは false(出さない側に倒す)。
 * `enabled` が false の間は問い合わせない(一般職員の画面で余計な通信をしない)。
 */
export function useAgentFlag(enabled: boolean): boolean {
  const { data: session, status } = useSession();
  const q = useQuery<FlagRow[], Error>({
    queryKey: ['office-feature-flags', AGENT_FEATURE_KEY],
    enabled: enabled && status === 'authenticated',
    retry: false,
    staleTime: 5 * 60_000,
    queryFn: () =>
      fetcher<FlagRow[]>(`/api/v1/office-feature-flags?feature_key=${AGENT_FEATURE_KEY}`, {
        accessToken: session?.accessToken ?? null,
        refreshToken: session?.refreshToken ?? null,
      }),
  });
  if (!enabled) return false;
  return (q.data ?? []).some((f) => f.feature_key === AGENT_FEATURE_KEY && !!f.enabled_at);
}
