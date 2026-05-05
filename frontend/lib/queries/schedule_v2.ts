/**
 * TanStack Query hooks for the v2 schedule grid (W3-FE5).
 *
 * 設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.2 (Layer 1
 * 時間配置) と API 契約 ``docs/plans/v2-api-contracts.md`` §6.1 に対応する
 * 週レイアウト固定の HTTP 呼び出しをラップする。
 *
 * Endpoints:
 *   POST /api/v1/schedule/fix
 *
 * RBAC (W3-BE-FIX):
 *   - admin / manager のみ。staff は 403。
 *
 * 注意 — API 契約と実装の差分:
 *   v2-api-contracts.md の初期ドラフトでは start/end を ``HHMM`` 形式
 *   (例: "0800") と記載していたが、W3-BE-FIX 実装は Pydantic schema
 *   (`WeeklyPatternEntryV2.preferred_start`) に合わせて **HH:MM 形式**
 *   ("08:00") を要求する。本フックはバックエンドの実装に合わせる。
 *   (契約書側の改訂は別途 W3-DOC で行う想定。)
 */
'use client';

import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';

const SCHEDULE_FIX_PATH = '/api/v1/schedule/fix';

function authPair(session: ReturnType<typeof useSession>['data']): {
  accessToken: string | null;
  refreshToken: string | null;
} {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/**
 * 1 件分のレイアウト要素 (患者 × 曜日 × 時刻スロット).
 *
 * BE `app/services/schedule_fix_service.py::FixedVisit` と一致。
 */
export interface FixedVisitV2 {
  patient_id: string;
  /** 0=Mon..6=Sun */
  weekday: number;
  /** ``HH:MM`` 24h. */
  start_time: string;
  /** ``HH:MM`` 24h. start より後 (= サービス時間が正) であること. */
  end_time: string;
  /** 1 名 or 2 名体制 (§3.3). */
  staff_count: 1 | 2;
}

/** POST /api/v1/schedule/fix Request body. */
export interface ScheduleFixRequest {
  iso_year: number;
  iso_week: number;
  layout: FixedVisitV2[];
}

/** 1 患者分の更新サマリ (差分のみ反映が成立した証跡). */
export interface WeeklyPatternChangeV2 {
  patient_id: string;
  entries_before: Array<Record<string, unknown>>;
  entries_after: Array<Record<string, unknown>>;
}

/** POST /api/v1/schedule/fix Response body. */
export interface ScheduleFixResponse {
  patients_updated: number;
  weekly_pattern_changes: WeeklyPatternChangeV2[];
}

/**
 * POST /api/v1/schedule/fix — 週レイアウトを各患者の weekly_pattern に保存.
 *
 * 全件 1 トランザクション。1 件でも失敗すれば BE 側で全 rollback され、
 * 4xx / 500 が返る。差分が無い患者は touch されない (受入基準: 差分のみ書込み)。
 *
 * onSuccess で `patients` クエリを invalidate して、weekly_pattern 編集系の
 * UI が即座に最新値を反映できるようにする。
 */
export function useFixSchedule(): UseMutationResult<
  ScheduleFixResponse,
  Error,
  ScheduleFixRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<ScheduleFixResponse, Error, ScheduleFixRequest>({
    mutationFn: async (payload) => {
      return await fetcher<ScheduleFixResponse>(SCHEDULE_FIX_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      // weekly_pattern が更新されたので患者一覧キャッシュを無効化する.
      void qc.invalidateQueries({ queryKey: ['patients'] });
    },
  });
}
