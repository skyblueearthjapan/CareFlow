/**
 * 週の「空にする」と「週を生成」の件数確認 — 青ピン廃止 BP-3 / BP-4 (PO 2026-10-12)。
 * 設計: docs/plans/blue-pin/blue-pin-abolish-design-2026-10-12.md
 *
 * 週の作り方まわりの API クライアントはこのファイルに集める。
 *   - POST /api/v1/schedule/v2/weeks/{y}/{w}/clear       … dry_run で件数、本実行で空にする
 *   - POST /api/v1/schedule/v2/weeks/{y}/{w}/clear/undo  … 空にする前の状態へ戻す
 *   - POST /api/v1/schedule/generate-week-only (dry_run) … 「型から N 件を足します」の N
 */
'use client';

import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';

export interface ClearWeekResult {
  /** 消す件数。 */
  to_delete: number;
  /** うち、カイポケへ送った訪問 (次の送信で削除になる)。 */
  kaipoke_linked: number;
  /** 残す件数 (打刻・実績あり)。 */
  kept_checked_in: number;
  /** 本実行のときだけ返る。「元に戻す」に使う。 */
  snapshot_id?: string;
}

export interface ClearWeekVariables {
  isoYear: number;
  isoWeek: number;
  dryRun: boolean;
}

export interface UndoClearWeekVariables {
  isoYear: number;
  isoWeek: number;
  snapshotId: string;
}

export interface GenerateWeekPreviewVariables {
  isoYear: number;
  isoWeek: number;
  officeId?: string | null;
}

export interface GenerateWeekPreviewResult {
  /** 型から足す件数。 */
  to_add: number;
}

function useAuthPair() {
  const { data: session } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

function invalidateWeek(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: ['visits'] });
  void qc.invalidateQueries({ queryKey: ['courses'] });
  void qc.invalidateQueries({ queryKey: ['schedule'] });
  void qc.invalidateQueries({ queryKey: ['integrations', 'inbound-snapshots'] });
}

/** 「この週を空にする」: dryRun=true は件数だけ、false は実行。 */
export function useClearWeek(): UseMutationResult<ClearWeekResult, Error, ClearWeekVariables> {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthPair();
  return useMutation<ClearWeekResult, Error, ClearWeekVariables>({
    mutationFn: ({ isoYear, isoWeek, dryRun }) =>
      fetcher<ClearWeekResult>(`/api/v1/schedule/v2/weeks/${isoYear}/${isoWeek}/clear`, {
        method: 'POST',
        body: JSON.stringify({ dry_run: dryRun }),
        accessToken,
        refreshToken,
      }),
    onSuccess: (_res, vars) => {
      if (!vars.dryRun) invalidateWeek(qc);
    },
  });
}

/** 「元に戻す」: 空にする直前の状態へ戻す。 */
export function useUndoClearWeek(): UseMutationResult<unknown, Error, UndoClearWeekVariables> {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthPair();
  return useMutation<unknown, Error, UndoClearWeekVariables>({
    mutationFn: ({ isoYear, isoWeek, snapshotId }) =>
      fetcher<unknown>(`/api/v1/schedule/v2/weeks/${isoYear}/${isoWeek}/clear/undo`, {
        method: 'POST',
        body: JSON.stringify({ snapshot_id: snapshotId }),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => invalidateWeek(qc),
  });
}

/** 「週を生成」の確認用: 実行せず、足す件数だけ取得する。 */
export function useGenerateWeekPreview(): UseMutationResult<
  GenerateWeekPreviewResult,
  Error,
  GenerateWeekPreviewVariables
> {
  const { accessToken, refreshToken } = useAuthPair();
  return useMutation<GenerateWeekPreviewResult, Error, GenerateWeekPreviewVariables>({
    mutationFn: ({ isoYear, isoWeek, officeId }) => {
      const body: Record<string, unknown> = { iso_year: isoYear, iso_week: isoWeek, dry_run: true };
      if (officeId) body.office_id = officeId;
      return fetcher<GenerateWeekPreviewResult>('/api/v1/schedule/generate-week-only', {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
    },
  });
}
