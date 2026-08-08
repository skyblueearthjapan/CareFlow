/**
 * 週のピン (青ピン) の TanStack Query hook — PO 決定 2026-08-08.
 * 仕様: docs/plans/pin-and-movability-spec.md
 *
 * 赤ピン (`useTogglePfvPin`) との違い:
 *   - 赤ピンは **型** (固定訪問スケジュール) に対するもの。毎週効く。
 *     型と一致する訪問にしか刺せない。
 *   - 青ピンは **今週の訪問** に対するもの。その週だけ効く。
 *     **型とズレていても刺せる** — ズレた訪問を今の位置で守れるのはこちらだけ。
 *
 * 実体は `visit.source='manual_week'`。週生成の削除対象から外れ、再生成ループが
 * 当該 (patient, visit_date) を skip する (= その週だけ型を一時上書きする)。
 *
 * 解除しても **その場では訪問は動かない**。次に週生成を実行したときに型の時刻が
 * 読み込まれる (PO 確認済の挙動)。
 */
'use client';

import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';

export interface ToggleVisitWeekPinVariables {
  visitId: string;
  /** true=今週この位置で固定 / false=型の管理に戻す */
  pinned: boolean;
}

export interface ToggleVisitWeekPinResult {
  visit_id: string;
  pinned: boolean;
  source: string;
}

/** 今週の訪問を「この位置のまま動かさない」状態にする / 解除する。 */
export function useToggleVisitWeekPin(): UseMutationResult<
  ToggleVisitWeekPinResult,
  Error,
  ToggleVisitWeekPinVariables
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<ToggleVisitWeekPinResult, Error, ToggleVisitWeekPinVariables>({
    mutationFn: async ({ visitId, pinned }) =>
      fetcher<ToggleVisitWeekPinResult>(`/api/v1/schedule/v2/visits/${visitId}/week-pin`, {
        method: 'PATCH',
        body: JSON.stringify({ pinned }),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      // source が変わると盤面の表示 (青ピン / 今週のみ) が変わるため visits を再取得。
      void qc.invalidateQueries({ queryKey: ['visits'] });
      void qc.invalidateQueries({ queryKey: ['courses'] });
    },
  });
}

// ─── 一括 (青の全件固定 / 全件解除) — PO 決定 2026-08-08 ─────────────────────

export interface BulkVisitWeekPinVariables {
  isoYear: number;
  isoWeek: number;
  /** true=今週全件固定 / false=今週の固定を全解除 */
  pinned: boolean;
  /** true なら件数だけ返して何も変更しない (確認ダイアログ用) */
  dryRun?: boolean;
}

export interface BulkVisitWeekPinResult {
  target_count: number;
  updated_count: number;
}

/**
 * 当該週の対象訪問を一括で今週固定する / 解除する。
 * 赤ピンの「全件ピン留め / 全件ピン留め解除」と対になる青ピン版。
 */
export function useBulkVisitWeekPin(): UseMutationResult<
  BulkVisitWeekPinResult,
  Error,
  BulkVisitWeekPinVariables
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<BulkVisitWeekPinResult, Error, BulkVisitWeekPinVariables>({
    mutationFn: async ({ isoYear, isoWeek, pinned, dryRun }) =>
      fetcher<BulkVisitWeekPinResult>('/api/v1/schedule/v2/visits/week-pin/bulk', {
        method: 'POST',
        body: JSON.stringify({
          iso_year: isoYear,
          iso_week: isoWeek,
          pinned,
          dry_run: dryRun ?? false,
        }),
        accessToken,
        refreshToken,
      }),
    onSuccess: (_res, vars) => {
      // dry_run (件数確認) では何も変わっていないため invalidate しない。
      if (vars.dryRun) return;
      void qc.invalidateQueries({ queryKey: ['visits'] });
      void qc.invalidateQueries({ queryKey: ['courses'] });
    },
  });
}
