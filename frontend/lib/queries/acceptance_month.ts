'use client';

/**
 * 受け入れ枠「月で見る」の読み込みと A4 (印刷用 HTML)。
 *
 * - GET /api/v1/acceptance-matrix/month?month=YYYY-MM&office_id=…&staff=…&mode=…&lunch=…
 *   office_id 省略=全拠点・staff 省略=既定の担い手 (職員だけ)・空=だれもいない。
 * - 同じ URL に include_html=true を付けると、A4 横の自己完結 HTML (`html`) が付く。
 */
import {
  keepPreviousData,
  useMutation,
  useQuery,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import type { LeaveMode } from '@/lib/acceptanceStrict';
import { monthPayloadSchema, type MonthPayload } from '@/lib/acceptanceMonth';
import { fetcher } from '@/lib/api/fetcher';

const MONTH_PATH = '/api/v1/acceptance-matrix/month';
export const MONTH_KEY = ['acceptance-matrix', 'month'] as const;

export interface MonthParams {
  month: string;
  officeId: string | null;
  /** null = 既定の担い手 (サーバーが決める)。 */
  staffIds: string[] | null;
  mode: LeaveMode;
  lunch: boolean;
}

export function monthPath(p: MonthParams, includeHtml = false): string {
  const usp = new URLSearchParams({ month: p.month, mode: p.mode, lunch: String(p.lunch) });
  if (p.officeId) usp.set('office_id', p.officeId);
  if (p.staffIds !== null) usp.set('staff', p.staffIds.join(','));
  if (includeHtml) usp.set('include_html', 'true');
  return `${MONTH_PATH}?${usp.toString()}`;
}

function useAuth() {
  const { data: session, status } = useSession();
  return {
    status,
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

export function useMonthAcceptance(
  params: MonthParams,
  { enabled = true }: { enabled?: boolean } = {},
): UseQueryResult<MonthPayload, Error> {
  const { status, accessToken, refreshToken } = useAuth();
  return useQuery<MonthPayload, Error>({
    queryKey: [...MONTH_KEY, params],
    enabled: enabled && status === 'authenticated',
    // 切り替えの間も前の表を出しておく (表が消えてちらつかないように)。
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const raw = await fetcher<unknown>(monthPath(params), { accessToken, refreshToken });
      return monthPayloadSchema.parse(raw);
    },
  });
}

/** A4 横の HTML を取り直す (押した時点の条件で)。 */
export function useMonthPrint() {
  const { accessToken, refreshToken } = useAuth();
  return useMutation<string, Error, MonthParams>({
    mutationFn: async (params) => {
      const raw = await fetcher<unknown>(monthPath(params, true), { accessToken, refreshToken });
      const html = monthPayloadSchema.parse(raw).html;
      if (!html) throw new Error('印刷用のページを作れませんでした');
      return html;
    },
  });
}
