/**
 * TanStack Query hooks for /api/v1/leave — 「休み・勤務表」の月 (設計 §5)。
 *
 * キャッシュのキーは ['leave', ...]。休みを書いたら 'leave' と、予定が動くので
 * スケジュール系 (invalidateScheduleAll) と職員ごとの休み ('staff-overrides') も読み直す。
 */
'use client';

import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';
import { invalidateScheduleAll } from '@/lib/queries/invalidateSchedule';
import {
  leaveApproveBatchResultSchema,
  leaveApproveResultSchema,
  leaveCancelResultSchema,
  leaveConfirmResultSchema,
  kinmuSheetSchema,
  leaveDayBulkResultSchema,
  leaveDayResultSchema,
  leaveMonthReadSchema,
  leaveMonthSummarySchema,
  leaveMonthViewSchema,
  leaveKinmuSheetOutcomeSchema,
  leaveRecordActualsResultSchema,
  leaveRejectResultSchema,
  officeClosedDaySchema,
  type LeaveApproveBatchResult,
  type LeaveApproveResult,
  type LeaveCancelResult,
  type LeaveConfirmResult,
  type KinmuSheet,
  type LeaveDayBulkInput,
  type LeaveDayBulkResult,
  type LeaveDayInput,
  type LeaveDayResult,
  type LeaveMonthRead,
  type LeaveMonthSummary,
  type LeaveMonthView,
  type LeaveKinmuSheetOutcome,
  type LeaveRecordActualsResult,
  type LeaveRejectResult,
  type LeaveTypeCode,
  type OfficeClosedDay,
} from '@/lib/schemas/leave';

const BASE = '/api/v1/leave';

export const LEAVE_KEY = ['leave'] as const;
/** month は 'YYYY-MM'。 */
export const leaveMonthKey = (month: string) => ['leave', 'month', month] as const;
export const leaveSummaryKey = (month: string) => ['leave', 'summary', month] as const;

/**
 * エラーから画面に出す文を取り出す。バックエンドの `detail` をそのまま出す
 * (文字列／まとめて承認の {request_id, message}／入力の検査の配列)。無ければ既定の文。
 */
export function leaveErrorMessage(e: unknown, fallback = '操作に失敗しました'): string {
  if (e instanceof ApiError && e.body && typeof e.body === 'object' && 'detail' in e.body) {
    const detail = (e.body as { detail: unknown }).detail;
    if (typeof detail === 'string' && detail) return detail;
    if (detail && typeof detail === 'object' && !Array.isArray(detail) && 'message' in detail) {
      const msg = (detail as { message: unknown }).message;
      if (typeof msg === 'string' && msg) return msg;
    }
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((d) => (d && typeof d === 'object' && 'msg' in d ? String(d.msg) : ''))
        .filter(Boolean);
      if (msgs.length) return msgs.join(' / ');
    }
  }
  if (e instanceof Error && e.message && !(e instanceof ApiError)) return e.message;
  return fallback;
}

function useTokens() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    ready: status === 'authenticated',
  };
}

/**
 * まとめて承認の失敗の `detail.request_id` (どの申請で止まったか)。無ければ null。
 */
export function leaveErrorRequestId(e: unknown): string | null {
  if (e instanceof ApiError && e.body && typeof e.body === 'object' && 'detail' in e.body) {
    const detail = (e.body as { detail: unknown }).detail;
    if (detail && typeof detail === 'object' && 'request_id' in detail) {
      const id = (detail as { request_id: unknown }).request_id;
      if (typeof id === 'string') return id;
    }
  }
  return null;
}

/**
 * 「実績を記録する」の 409 の `detail.blockers` の message の一覧。無ければ空。
 */
export function leaveErrorBlockers(e: unknown): string[] {
  if (e instanceof ApiError && e.body && typeof e.body === 'object' && 'detail' in e.body) {
    const detail = (e.body as { detail: unknown }).detail;
    if (detail && typeof detail === 'object' && 'blockers' in detail) {
      const list = (detail as { blockers: unknown }).blockers;
      if (Array.isArray(list)) {
        return list
          .map((b) => (b && typeof b === 'object' && 'message' in b ? String(b.message) : ''))
          .filter(Boolean);
      }
    }
  }
  return [];
}

/** 休みが動いたあとに読み直すもの (月の表・職員ごとの休み・スケジュール系)。 */
export async function invalidateAfterLeave(qc: QueryClient): Promise<void> {
  invalidateScheduleAll(qc);
  await Promise.all([
    qc.invalidateQueries({ queryKey: LEAVE_KEY }),
    qc.invalidateQueries({ queryKey: ['staff-overrides'] }),
  ]);
}

/**
 * 書き込みの hooks の選び方。`invalidate: false` なら読み直しは呼び出し側が
 * (続けて押したときに落ち着いてから 1 回だけ) 行う。
 */
export interface LeaveMutationOptions {
  invalidate?: boolean;
}

/** GET /month/{month} — 月×全職員 (管理者)。 */
export function useLeaveMonth(month: string, enabled = true) {
  const { accessToken, refreshToken, ready } = useTokens();
  return useQuery<LeaveMonthView>({
    queryKey: leaveMonthKey(month),
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/month/${month}`, { accessToken, refreshToken });
      return leaveMonthViewSchema.parse(raw);
    },
    enabled: ready && enabled,
  });
}

/**
 * GET /month/{month}/summary — 祝日・事業所の休み・確定の状態 (職員も可)。
 * pollMs を渡すとその間隔で読み直す (その間は新しいものとして扱う・メニューの件数は 60 秒)。
 */
export function useLeaveMonthSummary(month: string, enabled = true, pollMs?: number) {
  const { accessToken, refreshToken, ready } = useTokens();
  return useQuery<LeaveMonthSummary>({
    queryKey: leaveSummaryKey(month),
    ...(pollMs ? { staleTime: pollMs, refetchInterval: pollMs } : {}),
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/month/${month}/summary`, {
        accessToken,
        refreshToken,
      });
      return leaveMonthSummarySchema.parse(raw);
    },
    enabled: ready && enabled,
  });
}

/** GET /month/{month}/kinmu-sheets — その月に作った勤務表 (予定版→確定版・版の新しい順)。 */
export function useKinmuSheets(month: string, enabled = true) {
  const { accessToken, refreshToken, ready } = useTokens();
  return useQuery<KinmuSheet[]>({
    queryKey: ['leave', 'kinmu-sheets', month],
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/month/${month}/kinmu-sheets`, {
        accessToken,
        refreshToken,
      });
      return kinmuSheetSchema.array().parse(raw);
    },
    enabled: ready && enabled,
  });
}

/** POST /day/bulk — この日の全員に入れる (1 トランザクション・1 人でも入れられなければ全部やめる)。 */
export function useSetLeaveDayBulk({ invalidate = true }: LeaveMutationOptions = {}) {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<LeaveDayBulkResult, Error, LeaveDayBulkInput>({
    mutationFn: async (input) => {
      const raw = await fetcher<unknown>(`${BASE}/day/bulk`, {
        method: 'POST',
        body: JSON.stringify(input),
        accessToken,
        refreshToken,
      });
      return leaveDayBulkResultSchema.parse(raw);
    },
    onSettled: () => {
      if (invalidate) void invalidateAfterLeave(qc);
    },
  });
}

/** POST /day — 休みを入れる・種類を変える。 */
export function useSetLeaveDay({ invalidate = true }: LeaveMutationOptions = {}) {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<LeaveDayResult, Error, LeaveDayInput>({
    mutationFn: async (input) => {
      const raw = await fetcher<unknown>(`${BASE}/day`, {
        method: 'POST',
        body: JSON.stringify(input),
        accessToken,
        refreshToken,
      });
      return leaveDayResultSchema.parse(raw);
    },
    onSettled: () => {
      if (invalidate) void invalidateAfterLeave(qc);
    },
  });
}

/** DELETE /day?staff_id=&date= — 休みを取り消す (担当なしへ移した訪問は戻さない)。 */
export function useCancelLeaveDay({ invalidate = true }: LeaveMutationOptions = {}) {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<LeaveCancelResult, Error, { staff_id: string; date: string }>({
    mutationFn: async ({ staff_id, date }) => {
      const qs = `staff_id=${encodeURIComponent(staff_id)}&date=${encodeURIComponent(date)}`;
      const raw = await fetcher<unknown>(`${BASE}/day?${qs}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
      return leaveCancelResultSchema.parse(raw);
    },
    onSettled: () => {
      if (invalidate) void invalidateAfterLeave(qc);
    },
  });
}

/** POST /requests/{id}/approve — 1 件を承認 (override_type で種類を変えて承認)。 */
export function useApproveLeaveRequest({ invalidate = true }: LeaveMutationOptions = {}) {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<
    LeaveApproveResult,
    Error,
    { id: string; override_type?: LeaveTypeCode | null }
  >({
    mutationFn: async ({ id, override_type }) => {
      const raw = await fetcher<unknown>(`${BASE}/requests/${id}/approve`, {
        method: 'POST',
        body: JSON.stringify(override_type ? { override_type } : {}),
        accessToken,
        refreshToken,
      });
      return leaveApproveResultSchema.parse(raw);
    },
    onSettled: () => {
      if (invalidate) void invalidateAfterLeave(qc);
    },
  });
}

/** POST /requests/approve-batch — まとめて承認 (1 件でも失敗したら全部やめる)。 */
export function useApproveLeaveBatch({ invalidate = true }: LeaveMutationOptions = {}) {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<LeaveApproveBatchResult, Error, string[]>({
    mutationFn: async (ids) => {
      const raw = await fetcher<unknown>(`${BASE}/requests/approve-batch`, {
        method: 'POST',
        body: JSON.stringify({ request_ids: ids }),
        accessToken,
        refreshToken,
      });
      return leaveApproveBatchResultSchema.parse(raw);
    },
    onSettled: () => {
      if (invalidate) void invalidateAfterLeave(qc);
    },
  });
}

/** POST /requests/{id}/reject — 却下 (理由は必須・本人へ通知)。 */
export function useRejectLeaveRequest({ invalidate = true }: LeaveMutationOptions = {}) {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<LeaveRejectResult, Error, { id: string; reason: string }>({
    mutationFn: async ({ id, reason }) => {
      const raw = await fetcher<unknown>(`${BASE}/requests/${id}/reject`, {
        method: 'POST',
        body: JSON.stringify({ reason }),
        accessToken,
        refreshToken,
      });
      return leaveRejectResultSchema.parse(raw);
    },
    onSettled: () => {
      if (invalidate) void invalidateAfterLeave(qc);
    },
  });
}

/** PUT /closed-days — 事業所の休みを入れる (同じ日なら名前を変える)。 */
export function usePutClosedDay() {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<OfficeClosedDay, Error, { date: string; label: string }>({
    mutationFn: async (input) => {
      const raw = await fetcher<unknown>(`${BASE}/closed-days`, {
        method: 'PUT',
        body: JSON.stringify(input),
        accessToken,
        refreshToken,
      });
      return officeClosedDaySchema.parse(raw);
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: LEAVE_KEY }),
  });
}

/** DELETE /closed-days/{date} — 事業所の休みを消す。 */
export function useDeleteClosedDay() {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: async (date) => {
      await fetcher<unknown>(`${BASE}/closed-days/${date}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: LEAVE_KEY }),
  });
}

/** PUT /months/{month} — 常勤の従業者が勤務すべき時間数。 */
export function usePutLeaveMonth() {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<LeaveMonthRead, Error, { month: string; required_hours: number | null }>({
    mutationFn: async ({ month, required_hours }) => {
      const raw = await fetcher<unknown>(`${BASE}/months/${month}`, {
        method: 'PUT',
        body: JSON.stringify({ required_hours }),
        accessToken,
        refreshToken,
      });
      return leaveMonthReadSchema.parse(raw);
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: LEAVE_KEY }),
  });
}

/** POST /month/{month}/confirm — この月を確定して通知 (全員分)。 */
export function useConfirmLeaveMonth() {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<LeaveConfirmResult, Error, string>({
    mutationFn: async (month) => {
      const raw = await fetcher<unknown>(`${BASE}/month/${month}/confirm`, {
        method: 'POST',
        accessToken,
        refreshToken,
      });
      return leaveConfirmResultSchema.parse(raw);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: LEAVE_KEY });
      void qc.invalidateQueries({ queryKey: ['staff-shift-confirmations'] });
    },
  });
}

/** POST /month/{month}/record-actuals — 実績を記録し、勤務表の確定版を作る。 */
export function useRecordActuals() {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<LeaveRecordActualsResult, Error, string>({
    mutationFn: async (month) => {
      const raw = await fetcher<unknown>(`${BASE}/month/${month}/record-actuals`, {
        method: 'POST',
        accessToken,
        refreshToken,
      });
      return leaveRecordActualsResultSchema.parse(raw);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: LEAVE_KEY }),
  });
}

/** POST /month/{month}/kinmu-sheets/rebuild — 勤務表を作り直す・未作成なら作る (理由は必須)。 */
export function useRebuildKinmuSheet() {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<
    LeaveKinmuSheetOutcome,
    Error,
    { month: string; kind: 'plan' | 'final'; reason: string }
  >({
    mutationFn: async ({ month, kind, reason }) => {
      const raw = await fetcher<unknown>(`${BASE}/month/${month}/kinmu-sheets/rebuild`, {
        method: 'POST',
        body: JSON.stringify({ kind, reason }),
        accessToken,
        refreshToken,
      });
      return leaveKinmuSheetOutcomeSchema.parse(raw);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: LEAVE_KEY }),
  });
}
