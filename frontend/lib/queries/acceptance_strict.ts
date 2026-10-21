'use client';

/**
 * 受け入れ枠 (数週続けて空いているか) の読み込みと取り直し。
 *
 * - GET  /api/v1/acceptance-matrix/strict?office_id=…           職員×日×時台の「入れる」(office_id 省略=全拠点)
 * - POST /api/v1/acceptance-matrix/strict/refresh               カイポケから取り直す (admin)
 * - GET  /api/v1/acceptance-matrix/strict/refresh/latest        動いている取り直し
 *
 * キーは ['acceptance-matrix', 'strict', …] にして、毎週の上書きの変更
 * (lib/queries/acceptance_matrix.ts の invalidate) でも読み直されるようにする。
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { useEffect, useRef } from 'react';
import { toast } from 'sonner';

import {
  isJobActive,
  refreshJobSchema,
  strictPayloadSchema,
  type RefreshJob,
  type StrictPayload,
} from '@/lib/acceptanceStrict';
import { fetcher } from '@/lib/api/fetcher';

const STRICT_PATH = '/api/v1/acceptance-matrix/strict';
export const STRICT_KEY = ['acceptance-matrix', 'strict'] as const;
const REFRESH_KEY = ['acceptance-matrix', 'strict-refresh'] as const;

function useAuth() {
  const { data: session, status } = useSession();
  return {
    status,
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/**
 * ``allOffices`` のときは office_id を付けない (全拠点の担い手をまとめて見る)。
 * そうでないときは ``officeId`` が決まるまで読まない。``enabled`` が偽の間も読まない。
 */
export function useStrictAcceptance(
  officeId: string | null,
  {
    planOnly = false,
    allOffices = false,
    enabled = true,
  }: { planOnly?: boolean; allOffices?: boolean; enabled?: boolean } = {},
): UseQueryResult<StrictPayload, Error> {
  const { status, accessToken, refreshToken } = useAuth();
  const target = allOffices ? null : officeId;
  return useQuery<StrictPayload, Error>({
    queryKey: [...STRICT_KEY, { officeId: target, planOnly }],
    enabled: enabled && status === 'authenticated' && (allOffices || !!officeId),
    queryFn: async () => {
      const usp = new URLSearchParams();
      if (target) usp.set('office_id', target);
      if (planOnly) usp.set('plan_only', 'true');
      const raw = await fetcher<unknown>(`${STRICT_PATH}?${usp.toString()}`, {
        accessToken,
        refreshToken,
      });
      return strictPayloadSchema.parse(raw);
    },
  });
}

/** 動いている取り直しのジョブ (動いている間は 3 秒おきに読む)。終わったら表を読み直す。 */
export function useStrictRefreshJob(): UseQueryResult<RefreshJob | null, Error> {
  const { status, accessToken, refreshToken } = useAuth();
  const qc = useQueryClient();
  const query = useQuery<RefreshJob | null, Error>({
    queryKey: [...REFRESH_KEY],
    enabled: status === 'authenticated',
    queryFn: async () => {
      const raw = await fetcher<{ job: unknown }>(`${STRICT_PATH}/refresh/latest`, {
        accessToken,
        refreshToken,
      });
      return raw.job ? refreshJobSchema.parse(raw.job) : null;
    },
    // 残骸 (stale) は動いていないとみなして読みに行かない。
    refetchInterval: (q) => (isJobActive(q.state.data) ? 3000 : false),
  });
  const prev = useRef(false);
  const active = isJobActive(query.data);
  useEffect(() => {
    if (prev.current && !active) {
      void qc.invalidateQueries({ queryKey: STRICT_KEY });
    }
    prev.current = active;
  }, [active, qc]);
  return query;
}

export function useStartStrictRefresh(): UseMutationResult<
  { job_id: string; status: string; total: number },
  Error,
  'recent' | 'all'
> {
  const { accessToken, refreshToken } = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (scope) =>
      fetcher<{ job_id: string; status: string; total: number }>(`${STRICT_PATH}/refresh`, {
        method: 'POST',
        body: JSON.stringify({ scope }),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: REFRESH_KEY });
    },
    onError: (err: Error) => {
      toast.error(`取り直しを始められませんでした: ${err.message}`);
    },
  });
}
