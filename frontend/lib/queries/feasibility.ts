'use client';
/**
 * 実現性チェック (移動・重なり・バッファ・同住所ルール) — read-only API のフック。
 *
 * GET /api/v1/schedule/v2/feasibility-report?iso_year&iso_week[&office_id]
 * 応答には印刷用の自己完結 HTML (`html`) が同梱される。FE はそれを新しいタブに書き出す。
 */
import { useMutation } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';

export interface FeasibilityFinding {
  staff: string;
  day: string;
  kind: string;
  severity: 'hard' | 'soft' | 'info';
  at: string;
  to: string;
  from: string;
  gap_min: number | null;
  need_min: number | null;
  km: number | null;
}

export interface FeasibilityReport {
  iso_year: number;
  iso_week: number;
  week_start: string;
  week_end: string;
  generated_at: string;
  visit_count: number;
  event_count: number;
  hard_count: number;
  soft_count: number;
  summary: Record<string, number>;
  assumptions: {
    travel_speed_kmh: number;
    visit_buffer_min: number;
    lunch_duration_min: number;
    lunch_window: string;
    road_factor: number;
    same_address_pair_min_occupancy: number;
  };
  findings: FeasibilityFinding[];
  html: string | null;
}

export interface FeasibilityParams {
  isoYear: number;
  isoWeek: number;
  officeId?: string | null;
}

export function feasibilityReportPath(p: FeasibilityParams): string {
  const qs = new URLSearchParams({ iso_year: String(p.isoYear), iso_week: String(p.isoWeek) });
  if (p.officeId) qs.set('office_id', p.officeId);
  return `/api/v1/schedule/v2/feasibility-report?${qs.toString()}`;
}

/** ボタン押下で都度計算する (キャッシュしない = 直前の盤面編集を必ず反映する)。 */
export function useFeasibilityReport() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  return useMutation<FeasibilityReport, Error, FeasibilityParams>({
    mutationFn: (p) => fetcher<FeasibilityReport>(feasibilityReportPath(p), { accessToken, refreshToken }),
  });
}
