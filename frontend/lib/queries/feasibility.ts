'use client';
/**
 * 実現性チェック (移動・重なり・バッファ・同住所ルール) — read-only API のフック。
 *
 * GET /api/v1/schedule/v2/feasibility-report?iso_year&iso_week[&office_id]
 * 応答には印刷用の自己完結 HTML (`html`) が同梱される。FE はそれを新しいタブに書き出す。
 */
import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
    mutationFn: (p) =>
      fetcher<FeasibilityReport>(feasibilityReportPath(p), { accessToken, refreshToken }),
  });
}

// ---------------------------------------------------------------------------
// カードの印・帯 (設計 docs/plans/schedule-feasibility-alerts-design-2026-11-01.md 第 1 段)
//
// GET /api/v1/schedule/v2/feasibility-marks?date_from&date_to[&office_id] (admin・7 日まで)。
// 赤 (hard) と黄 (soft) だけが返る (参考は返らない)。印は保存せず毎回計算される。
// ---------------------------------------------------------------------------

export type FeasibilityTier = 'hard' | 'soft';

/** 帯・一覧・取り込み結果に出す指摘 1 件。理由の文は backend で作る。 */
export interface FeasibilityAlertItem {
  tier: FeasibilityTier;
  kind: string;
  day: string; // YYYY-MM-DD
  at: string; // HH:MM ('' = 日単位)
  staff: string;
  patientName: string;
  reason: string;
  visitIds: string[];
}

/** カード 1 枚に付ける印 1 件。 */
export interface FeasibilityMarkEntry {
  tier: FeasibilityTier;
  kind: string;
  reason: string;
}

export interface FeasibilityMarksResponse {
  dateFrom: string;
  dateTo: string;
  hardCount: number;
  softCount: number;
  unassignedCount: number;
  items: FeasibilityAlertItem[];
  marks: Record<string, FeasibilityMarkEntry[]>;
}

/** 取り込みで新しく出た無理・注意 (smart-inbound-apply の応答の feasibility)。 */
export interface ImportFeasibility {
  ok: boolean;
  hardCount: number;
  softCount: number;
  unassignedCount: number;
  items: FeasibilityAlertItem[];
  message: string | null;
}

export interface FeasibilityMarksParams {
  dateFrom: string; // YYYY-MM-DD
  dateTo: string; // YYYY-MM-DD
  officeId?: string | null;
  /** 管理者だけ取る (印は管理者だけに出す・PO 決定 2026-11-01)。 */
  enabled?: boolean;
}

export function feasibilityMarksPath(p: FeasibilityMarksParams): string {
  const qs = new URLSearchParams({ date_from: p.dateFrom, date_to: p.dateTo });
  if (p.officeId) qs.set('office_id', p.officeId);
  return `/api/v1/schedule/v2/feasibility-marks?${qs.toString()}`;
}

/** 印の query キーの頭 (取り込みの後などに、これで読み直す)。 */
export const FEASIBILITY_MARKS_KEY = ['schedule', 'feasibility-marks'] as const;

/**
 * 期間の印をまとめて 1 回で取る (スケジュール = 1 週・訪問モニター = 1 日)。
 * キーは ['schedule', …] の下に置く: 予定を動かしたときの一括失効 (invalidateScheduleAll)
 * で一緒に読み直される。予定を変える操作 (react-query の mutation) が成功したときも
 * 読み直す (個別の操作は ['visits'] などだけを失効させることが多いため)。
 * ほかの人の変更に追いつくため、5 分ごとにも読み直す。
 */
export function useFeasibilityMarks(p: FeasibilityMarksParams) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const enabled = (p.enabled ?? true) && status === 'authenticated';
  const qc = useQueryClient();
  useEffect(() => {
    if (!enabled) return undefined;
    return qc.getMutationCache().subscribe((event) => {
      if (event.type === 'updated' && event.action.type === 'success') {
        void qc.invalidateQueries({ queryKey: FEASIBILITY_MARKS_KEY });
      }
    });
  }, [qc, enabled]);
  return useQuery<FeasibilityMarksResponse, Error>({
    queryKey: [
      ...FEASIBILITY_MARKS_KEY,
      { dateFrom: p.dateFrom, dateTo: p.dateTo, officeId: p.officeId ?? null },
    ],
    enabled,
    staleTime: 30_000,
    refetchInterval: 5 * 60_000,
    refetchIntervalInBackground: false,
    queryFn: () =>
      fetcher<FeasibilityMarksResponse>(feasibilityMarksPath(p), { accessToken, refreshToken }),
  });
}
