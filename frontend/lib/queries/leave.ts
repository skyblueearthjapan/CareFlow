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
  leaveDayResultSchema,
  leaveMonthReadSchema,
  leaveMonthSummarySchema,
  leaveMonthViewSchema,
  leaveRejectResultSchema,
  officeClosedDaySchema,
  type LeaveApproveBatchResult,
  type LeaveApproveResult,
  type LeaveCancelResult,
  type LeaveConfirmResult,
  type LeaveDayInput,
  type LeaveDayResult,
  type LeaveMonthRead,
  type LeaveMonthSummary,
  type LeaveMonthView,
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

/** 休みが動いたあとに読み直すもの。 */
function invalidateAfterLeave(qc: QueryClient): void {
  void qc.invalidateQueries({ queryKey: LEAVE_KEY });
  void qc.invalidateQueries({ queryKey: ['staff-overrides'] });
  invalidateScheduleAll(qc);
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

/** GET /month/{month}/summary — 祝日・事業所の休み・確定の状態 (職員も可)。 */
export function useLeaveMonthSummary(month: string, enabled = true) {
  const { accessToken, refreshToken, ready } = useTokens();
  return useQuery<LeaveMonthSummary>({
    queryKey: leaveSummaryKey(month),
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

/** POST /day — 休みを入れる・種類を変える。 */
export function useSetLeaveDay() {
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
    onSettled: () => invalidateAfterLeave(qc),
  });
}

/** DELETE /day?staff_id=&date= — 休みを取り消す (担当なしへ移した訪問は戻さない)。 */
export function useCancelLeaveDay() {
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
    onSettled: () => invalidateAfterLeave(qc),
  });
}

/** POST /requests/{id}/approve — 1 件を承認 (override_type で種類を変えて承認)。 */
export function useApproveLeaveRequest() {
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
      invalidateAfterLeave(qc);
      void qc.invalidateQueries({ queryKey: ['pending-requests'] });
    },
  });
}

/** POST /requests/approve-batch — まとめて承認 (1 件でも失敗したら全部やめる)。 */
export function useApproveLeaveBatch() {
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
      invalidateAfterLeave(qc);
      void qc.invalidateQueries({ queryKey: ['pending-requests'] });
    },
  });
}

/** POST /requests/{id}/reject — 却下 (理由は必須・本人へ通知)。 */
export function useRejectLeaveRequest() {
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
      void qc.invalidateQueries({ queryKey: LEAVE_KEY });
      void qc.invalidateQueries({ queryKey: ['pending-requests'] });
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
