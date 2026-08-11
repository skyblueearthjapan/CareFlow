'use client';

/**
 * 現場ボード `/m` (CareFlow Mobile) 用 TanStack Query フック群 (Phase2-3c)。
 *
 * Backend (`app/api/v1/schedule_v2.py`) のエンドポイントを 1:1 で写像する:
 *   - GET  /api/v1/schedule/v2/board          → useFieldBoard
 *   - POST /api/v1/schedule/v2/propose-slots   → useProposeSlots (mutation)
 *
 * 認証は NextAuth セッションの access/refresh token を `fetcher` に渡す
 * (他の v2 フックと同じ流儀)。RBAC は BE 側で admin/manager を担保。
 */
import {
  useMutation,
  useQuery,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import { isoWeekFromLocalDate } from '@/lib/format/isoWeek';
import { boardResponseSchema, type BoardResponse, type WeekdayCode } from '@/lib/schemas/v2/board';
import {
  proposeSlotsRequestSchema,
  proposeSlotsResponseSchema,
  type ProposeSlotsRequest,
  type ProposeSlotsResponse,
} from '@/lib/schemas/v2/propose_slots';
import {
  travelEstimateRequestSchema,
  travelEstimateResponseSchema,
  type TravelEstimateRequest,
  type TravelEstimateResponse,
} from '@/lib/schemas/v2/travel_estimate';

const BOARD_PATH = '/api/v1/schedule/v2/board';
const PROPOSE_SLOTS_PATH = '/api/v1/schedule/v2/propose-slots';
const TRAVEL_ESTIMATE_PATH = '/api/v1/schedule/v2/travel-estimate';

const FIELD_BOARD_KEY = ['field-board'] as const;

function authPair(session: ReturnType<typeof useSession>['data']): {
  accessToken: string | null;
  refreshToken: string | null;
} {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

// ─────────────────────────────────────────────────────────────────────────
// ISO 年週ヘルパー
// ─────────────────────────────────────────────────────────────────────────

/** 任意の Date を ISO 週の月曜 (Mon=0..Sun=6 規約) に丸める。 */
export function toWeekStart(d: Date): Date {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  // JS getDay(): Sun=0..Sat=6. Map Sun→6, Mon→0, …, Sat→5.
  const dow = (x.getDay() + 6) % 7;
  x.setDate(x.getDate() - dow);
  return x;
}

/** Date → { isoYear, isoWeek } (ISO-8601 週番号)。lib/format/isoWeek の共通実装に委譲。 */
export function toIsoYearWeek(d: Date): { isoYear: number; isoWeek: number } {
  return isoWeekFromLocalDate(d);
}

/** weekday コード (Mon..Sun) → 0..6 の整数 (Mon=0)。 */
export const WEEKDAY_CODE_TO_INT: Record<WeekdayCode, number> = {
  Mon: 0,
  Tue: 1,
  Wed: 2,
  Thu: 3,
  Fri: 4,
  Sat: 5,
  Sun: 6,
};

// ─────────────────────────────────────────────────────────────────────────
// GET /v2/board — 週ボード
// ─────────────────────────────────────────────────────────────────────────

export interface UseFieldBoardParams {
  isoYear: number;
  isoWeek: number;
  /** 単一拠点に絞る場合の office_id。未指定なら全 active 拠点。 */
  officeId?: string | null;
}

/**
 * GET /api/v1/schedule/v2/board — 対象週 × 拠点の実 Visit を
 * office × weekday × course 構造で取得する。
 *
 * レスポンスは zod (`boardResponseSchema`) で再パースするため、BE が列を
 * 追加してもクライアント型は安全に保たれる (forward compat)。
 */
export function useFieldBoard(params: UseFieldBoardParams): UseQueryResult<BoardResponse, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const { isoYear, isoWeek, officeId } = params;

  return useQuery<BoardResponse, Error>({
    queryKey: [...FIELD_BOARD_KEY, { isoYear, isoWeek, officeId: officeId ?? null }],
    enabled: status === 'authenticated',
    queryFn: async () => {
      const usp = new URLSearchParams({
        iso_year: String(isoYear),
        iso_week: String(isoWeek),
      });
      if (officeId) usp.set('office_id', officeId);
      const raw = await fetcher<unknown>(`${BOARD_PATH}?${usp.toString()}`, {
        accessToken,
        refreshToken,
      });
      return boardResponseSchema.parse(raw);
    },
  });
}

// ─────────────────────────────────────────────────────────────────────────
// POST /v2/propose-slots — 自動提案
// ─────────────────────────────────────────────────────────────────────────

/**
 * POST /api/v1/schedule/v2/propose-slots — 候補患者の希望から、対象週 × 拠点の
 * 実現可能な空き枠を算出・ランキングして取得する (read-only, DB 書込なし)。
 *
 * 「自動提案する」ボタンから呼ぶ mutation。返ってきた slots をランキング表示 +
 * ミニスケジュール (実時刻) でレンダする。
 */
export function useProposeSlots(): UseMutationResult<
  ProposeSlotsResponse,
  Error,
  ProposeSlotsRequest
> {
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<ProposeSlotsResponse, Error, ProposeSlotsRequest>({
    mutationFn: async (raw) => {
      const payload = proposeSlotsRequestSchema.parse(raw);
      const result = await fetcher<unknown>(PROPOSE_SLOTS_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return proposeSlotsResponseSchema.parse(result);
    },
  });
}

// ─────────────────────────────────────────────────────────────────────────
// POST /v2/travel-estimate — 移動時間見積 (Phase G-84, 空き枠直接配置の案2)
// ─────────────────────────────────────────────────────────────────────────

/**
 * POST /api/v1/schedule/v2/travel-estimate — 直前訪問 → 配置先の移動 +
 * バッファー所要分を見積もる (read-only, DB 書込なし)。
 *
 * 空き枠タップ時の PlacementSheet が、開始時刻の初期値 (案2 = 直前訪問の終了 +
 * total_minutes) を決めるために呼ぶ mutation。座標が確定できないと minutes は
 * null で返り、フロントは枠頭 (案1) にフォールバックする。
 */
export function useTravelEstimate(): UseMutationResult<
  TravelEstimateResponse,
  Error,
  TravelEstimateRequest
> {
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<TravelEstimateResponse, Error, TravelEstimateRequest>({
    mutationFn: async (raw) => {
      const payload = travelEstimateRequestSchema.parse(raw);
      const result = await fetcher<unknown>(TRAVEL_ESTIMATE_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return travelEstimateResponseSchema.parse(result);
    },
  });
}

// ─────────────────────────────────────────────────────────────────────────
// 提案 warnings の日本語ラベル (UI 表示用)
// ─────────────────────────────────────────────────────────────────────────

/** propose-slots warnings コードの日本語ラベル。未知コードはそのまま表示。 */
const PROPOSE_WARNING_LABEL_JA: Record<string, string> = {
  travel_time_shortage: '移動時間がタイト',
  buffer_shortage: 'バッファー不足',
  lunch_overlap: '昼休みと重複',
  late_finish: '終了が遅め',
  capacity_tight: '容量に余裕なし',
  sex_restriction_unmet: '性別条件を満たせない',
  two_staff_not_guaranteed: '2名体制: 2人目のスタッフ枠は別途確保が必要',
  // N-3 (schedule-advisor P0-1): 候補コースの割付スタッフ実態の警告。
  staff_unassigned: 'スタッフ未割付',
  staff_absent: '担当スタッフが休み',
  staff_sex_mismatch: '性別条件に不適合',
  // NG スタッフ (患者×スタッフ割当禁止, docs/plans/patient-ng-staff-design.md §6).
  staff_ng_mismatch: 'NGスタッフに該当',
  // I-07 (H5 統合): 受入枠カレンダー上は受け入れ不可の枠 (警告のみ・除外しない).
  acceptance_calendar: '受入枠カレンダーでは受け入れ不可',
  // イベント考慮2段階提案 (2026-07-27): クリーン枠ゼロのときだけ出るフォールバック枠。
  // 詳細 (どのイベントと重なるか) は slot.event_conflicts に入る。
  event_conflict: '担当スタッフのイベントと重なる（配置後にイベントの調整が必要）',
};

export function proposeWarningLabel(code: string): string {
  return PROPOSE_WARNING_LABEL_JA[code] ?? code;
}
