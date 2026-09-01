'use client';
/**
 * らく助×カイポケ 突合レポート (read-only) のフック。
 *
 * GET /api/v1/integrations/reconcile-report?weekStart=YYYY-MM-DD[&days]
 * 応答には印刷用の自己完結 HTML (`html`) が同梱される。FE はそれを新しいタブに書き出す
 * (FeasibilityCheckButton と同じ方式)。カイポケ側は保存済みスナップショットを使うため
 * RPA は回らず即応答する。
 */
import { useMutation } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';

export interface ReconcileSnapshotInfo {
  month: string;
  fetched_at: string | null;
  row_count: number;
  source_op: string;
}

export interface ReconcileReport {
  week_start: string;
  week_end: string;
  generated_at: string;
  total: number;
  counts: Record<string, number>;
  snapshots: ReconcileSnapshotInfo[];
  html?: string | null;
}

export function useReconcileReport() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken;
  const refreshToken = session?.refreshToken;

  return useMutation<ReconcileReport, Error, { weekStart: string; days?: number }>({
    mutationFn: ({ weekStart, days }) => {
      const qs = new URLSearchParams({ weekStart });
      if (days != null) qs.set('days', String(days));
      return fetcher<ReconcileReport>(`/api/v1/integrations/reconcile-report?${qs.toString()}`, {
        accessToken,
        refreshToken,
      });
    },
  });
}
