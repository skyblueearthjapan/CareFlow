'use client';

/**
 * 「今週の運転席」(Phase E) の TanStack Query フック — 契約書
 * `docs/plans/week-cockpit-design.md` §2 / §3。
 *
 *   useSubstituteCandidates — 代替候補 (read-only・POST 検索)
 *   useVisitCancelWeek      — 訪問の「今週だけ取消 / 取消をやめる」
 *   useEventCancelWeek      — 固定イベントの「今週だけ外す / 戻す」
 *   useUnsentSummary        — ●未送信サマリ (RPA を呼ばない・週指定)
 *   useReverseSheet         — ⇧上書き (inbound シート → outbound シート反転)
 *
 * 既存の作法に合わせる:
 *   - HTTP は `lib/api/fetcher` の `fetcher` (401 リフレッシュ + ApiError)。
 *   - レスポンスは zod で parse してから返す (visitAssignStaffWeek.ts と同じ)。
 *   - 実データを変える系は ['visits'] / ['courses'] / ['staff','events'] /
 *     op-log を invalidate する (opLog.ts の invalidateAfterUndoRedo と同じ範囲)。
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import { OP_LOG_STATE_KEY } from '@/lib/queries/opLog';
import {
  cockpitEventReadSchema,
  eventCancelWeekRequestSchema,
  reverseSheetResponseSchema,
  substituteCandidatesReadSchema,
  substituteCandidatesRequestSchema,
  unsentSummaryReadSchema,
  unsentSummaryRequestSchema,
  visitCancelWeekRequestSchema,
  visitCancelWeekResponseSchema,
  type CockpitEventRead,
  type EventCancelWeekRequest,
  type ReverseSheetRequest,
  type ReverseSheetResponse,
  type SubstituteCandidatesRead,
  type SubstituteCandidatesRequest,
  type UnsentSummaryRead,
  type UnsentSummaryRequest,
  type VisitCancelWeekRequest,
  type VisitCancelWeekResponse,
  visitServiceOverrideRequestSchema,
  visitServiceOverrideResponseSchema,
  type VisitServiceOverrideRequest,
  type VisitServiceOverrideResponse,
} from '@/lib/schemas/v2/cockpit';

const SUBSTITUTE_CANDIDATES_PATH = '/api/v1/schedule/v2/substitute-candidates';
const VISIT_CANCEL_WEEK_PATH = '/api/v1/schedule/v2/visit-cancel-week';
const VISIT_SERVICE_OVERRIDE_PATH = '/api/v1/schedule/v2/visit-service-override';
const UNSENT_SUMMARY_PATH = '/api/v1/integrations/unsent-summary';

/** TanStack Query キー prefix (代替候補)。 */
export const SUBSTITUTE_CANDIDATES_KEY = 'substitute-candidates' as const;

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/** 盤面まわりのキャッシュ失効 (訪問/コース/イベント/undo スタック)。 */
function invalidateBoard(qc: ReturnType<typeof useQueryClient>): void {
  void qc.invalidateQueries({ queryKey: ['visits'] });
  void qc.invalidateQueries({ queryKey: ['courses'] });
  void qc.invalidateQueries({ queryKey: ['staff', 'events'] });
  void qc.invalidateQueries({ queryKey: [OP_LOG_STATE_KEY] });
}

// ───────────────────────────────────────────────────────────────────────────
// §2-1 代替候補
// ───────────────────────────────────────────────────────────────────────────

/**
 * 急な休みの代替候補 (read-only)。`params=null` の間は取得しない。
 * BE は検索条件が多いため POST だが、副作用が無いので query として扱う。
 * admin 専用 API のため `enabled` に canEdit 相当を渡すこと。
 */
export function useSubstituteCandidates(
  params: SubstituteCandidatesRequest | null,
  enabled = true,
) {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<SubstituteCandidatesRead>({
    queryKey: [
      SUBSTITUTE_CANDIDATES_KEY,
      params?.staff_id ?? null,
      params?.date ?? null,
      params?.course_id ?? null,
    ],
    queryFn: async () => {
      const payload = substituteCandidatesRequestSchema.parse(params);
      const raw = await fetcher<unknown>(SUBSTITUTE_CANDIDATES_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return substituteCandidatesReadSchema.parse(raw);
    },
    enabled: status === 'authenticated' && enabled && params != null,
    staleTime: 30_000,
  });
}

// ───────────────────────────────────────────────────────────────────────────
// §2-2 今週だけ取消 (訪問)
// ───────────────────────────────────────────────────────────────────────────

/**
 * 訪問の「今週だけ取消 / 取消をやめる」。status planned↔cancelled。
 * 打刻あり・当日以前・青ピンは BE が 422 で拒否する (FE も事前に警告する)。
 */
export function useVisitCancelWeek(): UseMutationResult<
  VisitCancelWeekResponse,
  Error,
  VisitCancelWeekRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<VisitCancelWeekResponse, Error, VisitCancelWeekRequest>({
    mutationFn: async (raw) => {
      const payload = visitCancelWeekRequestSchema.parse(raw);
      const result = await fetcher<unknown>(VISIT_CANCEL_WEEK_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return visitCancelWeekResponseSchema.parse(result);
    },
    onSuccess: () => invalidateBoard(qc),
  });
}

/**
 * この訪問だけカイポケのサービス内容に合わせる (`visits.kaipoke_service_override`)。
 * マスタ (患者の区分 / スタッフの資格) は動かさない。`service_content: null` で解除。
 *
 * 対象は `visit_id` (盤面のカードから) か `item_id` (同期バーの差分行から) の
 * どちらか一方 — item → visit の解決は **BE 側** が持つ (氏名の正規化ルールを
 * FE に二重化しない)。
 */
export function useVisitServiceOverride(): UseMutationResult<
  VisitServiceOverrideResponse,
  Error,
  VisitServiceOverrideRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<VisitServiceOverrideResponse, Error, VisitServiceOverrideRequest>({
    mutationFn: async (raw) => {
      const payload = visitServiceOverrideRequestSchema.parse(raw);
      const result = await fetcher<unknown>(VISIT_SERVICE_OVERRIDE_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return visitServiceOverrideResponseSchema.parse(result);
    },
    onSuccess: () => invalidateBoard(qc),
  });
}

// ───────────────────────────────────────────────────────────────────────────
// §2-3 固定イベントを今週だけ外す
// ───────────────────────────────────────────────────────────────────────────

/**
 * 固定イベント (朝会など) を今週だけ外す / 戻す。
 * 行は残るため展開 (型から今週を作る) では復活しない。
 */
export function useEventCancelWeek(): UseMutationResult<
  CockpitEventRead,
  Error,
  EventCancelWeekRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<CockpitEventRead, Error, EventCancelWeekRequest>({
    mutationFn: async (raw) => {
      // 他の 4 本と同じ作法: 送る前に zod で検証する (BE は extra='forbid')。
      const { staff_id, event_id, cancel } = eventCancelWeekRequestSchema.parse(raw);
      const result = await fetcher<unknown>(
        `/api/v1/staff/${staff_id}/events/${event_id}/cancel-week`,
        {
          method: 'POST',
          body: JSON.stringify({ cancel }),
          accessToken,
          refreshToken,
        },
      );
      return cockpitEventReadSchema.parse(result);
    },
    onSuccess: () => invalidateBoard(qc),
  });
}

// ───────────────────────────────────────────────────────────────────────────
// §2-4 ●未送信サマリ
// ───────────────────────────────────────────────────────────────────────────

/**
 * ●未送信サマリ (週指定)。保存済みのカイポケ CSV スナップショットとの差分を
 * **RPA なし**で計算するため即時に返る。押した時点の状態が欲しいので mutation。
 */
export function useUnsentSummary(): UseMutationResult<
  UnsentSummaryRead,
  Error,
  UnsentSummaryRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<UnsentSummaryRead, Error, UnsentSummaryRequest>({
    mutationFn: async (raw) => {
      const payload = unsentSummaryRequestSchema.parse(raw);
      const result = await fetcher<unknown>(UNSENT_SUMMARY_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return unsentSummaryReadSchema.parse(result);
    },
    onSuccess: () => {
      // outbound シートを新規作成するため、連携ページのシート/項目一覧を失効させる。
      void qc.invalidateQueries({ queryKey: ['integrations'] });
    },
  });
}

// ───────────────────────────────────────────────────────────────────────────
// §2-5 ⇧上書き (シート反転)
// ───────────────────────────────────────────────────────────────────────────

/**
 * inbound シート (before=らく助 / after=カイポケ) の include=true な項目を反転し、
 * outbound シートを作る。返った sheet_id を既存 `/integrations/apply` に流す。
 */
export function useReverseSheet(): UseMutationResult<
  ReverseSheetResponse,
  Error,
  ReverseSheetRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<ReverseSheetResponse, Error, ReverseSheetRequest>({
    mutationFn: async ({ sheet_id }) => {
      const result = await fetcher<unknown>(
        `/api/v1/integrations/correction-sheets/${sheet_id}/reverse`,
        // ボディ無しの POST (BE はパスパラメータだけを見る)。
        { method: 'POST', accessToken, refreshToken },
      );
      return reverseSheetResponseSchema.parse(result);
    },
    onSuccess: () => {
      // 反転した outbound シートが増えるため、シート/項目一覧を失効させる。
      void qc.invalidateQueries({ queryKey: ['integrations'] });
    },
  });
}
