/**
 * 予定のお知らせ (SMS) の TanStack Query hooks (管理者だけ・機能の旗 sms_notify)。
 *
 *   GET  /api/v1/sms/status
 *   POST /api/v1/sms/week-notices/preview   body {week_start}
 *   POST /api/v1/sms/week-notices/send      body {week_start, patient_ids, expected_count, resend, tokens}
 *   GET  /api/v1/sms/changes/pending
 *   POST /api/v1/sms/changes/send           body {items:[{patient_id, week_start, token}], expected_count}
 *   POST /api/v1/sms/changes/resolve        body {patient_id, week_start, channel, reason, note, change_token}
 *   GET  /api/v1/sms/batches
 *   GET  /api/v1/sms/messages, /api/v1/sms/messages/{id}
 *   GET  /api/v1/sms/summary?month=, /api/v1/sms/summary/export?month= (xlsx)
 *   GET  /api/v1/sms/patients/summary?month=, /api/v1/sms/patients/{id}/recent?limit=
 *   GET  /api/v1/sms/numbers/attention
 *
 * 本文はサーバーが作り直す。画面から本文や番号を送らない (送るのは患者の ID・件数・見た内容の印だけ)。
 * 設計 docs/plans/sms-notify-design-2026-11-16.md・決まり sms-notify-api-contract-2026-11-16.md。
 */
'use client';

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';
import {
  changeResolveResultSchema,
  changeSendResultSchema,
  smsAttentionSchema,
  smsBatchListSchema,
  smsMessageDetailSchema,
  smsMessageListSchema,
  smsPatientSummarySchema,
  smsPendingListSchema,
  smsRecentSchema,
  smsStatusSchema,
  smsSummarySchema,
  weekNoticePreviewSchema,
  weekNoticeSendResultSchema,
  type ChangeResolveRequest,
  type ChangeSendRequest,
  type ChangeSendResult,
  type SmsAttentionItem,
  type SmsBatch,
  type SmsMessage,
  type SmsMessageDetail,
  type SmsMessageFilter,
  type SmsMessageList,
  type SmsPatientSummary,
  type SmsPendingList,
  type SmsStatus,
  type SmsSummary,
  type WeekNoticePreview,
  type WeekNoticeSendRequest,
  type WeekNoticeSendResult,
} from '@/lib/schemas/sms';

const BASE = '/api/v1/sms';

export const SMS_KEY = ['sms'] as const;
export const SMS_STATUS_KEY = ['sms', 'status'] as const;
export const SMS_WEEK_PREVIEW_KEY = (weekStart: string) =>
  ['sms', 'week-preview', weekStart] as const;
export const SMS_PENDING_KEY = ['sms', 'changes', 'pending'] as const;
/** 送る・済みにする mutation の共通の頭。送っているあいだダイアログを閉じさせないために使う。 */
export const SMS_SEND_MUTATION_KEY = ['sms', 'send'] as const;

/** 週の画面の［予定のお知らせ］の件数を読み込む間隔 (メニューの件数と同じ 60 秒)。 */
export const SMS_STATUS_POLL_MS = 60_000;

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

function qs(params: Record<string, string | number | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

/**
 * 使えるか・お知らせ待ちの件数。`enabled=false` (管理者以外) では問い合わせない。
 * 取れない・エラーは「使えない」とみなす (出さない側に倒す)。
 */
export function useSmsStatus(
  enabled: boolean,
  options: { refetchInterval?: number } = {},
): UseQueryResult<SmsStatus, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useQuery<SmsStatus, Error>({
    queryKey: SMS_STATUS_KEY,
    enabled: enabled && status === 'authenticated',
    retry: false,
    staleTime: 30_000,
    refetchInterval: options.refetchInterval,
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/status`, { accessToken, refreshToken });
      return smsStatusSchema.parse(raw);
    },
  });
}

/** 管理者で、旗が ON の拠点があるときだけ true。 */
export function useSmsEnabled(isAdmin: boolean): boolean {
  const q = useSmsStatus(isAdmin);
  return isAdmin && q.data?.enabled === true;
}

/** 週のお知らせの一覧 (POST だが読むだけ)。`enabled=false` のあいだは取りに行かない。 */
export function useWeekNoticePreview(
  weekStart: string,
  enabled: boolean,
): UseQueryResult<WeekNoticePreview, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useQuery<WeekNoticePreview, Error>({
    queryKey: SMS_WEEK_PREVIEW_KEY(weekStart),
    enabled: enabled && status === 'authenticated' && !!weekStart,
    retry: false,
    // 選んでいる最中に中身が入れ替わらないよう、自動では読み込まない。
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    staleTime: 0,
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/week-notices/preview`, {
        method: 'POST',
        body: JSON.stringify({ week_start: weekStart }),
        accessToken,
        refreshToken,
      });
      return weekNoticePreviewSchema.parse(raw);
    },
  });
}

export function useSendWeekNotices(): UseMutationResult<
  WeekNoticeSendResult,
  Error,
  WeekNoticeSendRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useMutation<WeekNoticeSendResult, Error, WeekNoticeSendRequest>({
    mutationKey: [...SMS_SEND_MUTATION_KEY, 'week'],
    mutationFn: async (req) => {
      const raw = await fetcher<unknown>(`${BASE}/week-notices/send`, {
        method: 'POST',
        body: JSON.stringify(req),
        accessToken,
        refreshToken,
      });
      return weekNoticeSendResultSchema.parse(raw);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: SMS_KEY });
    },
  });
}

/** お知らせ待ち (お伝えした内容と今の予定が違う方)。 */
export function useSmsPendingChanges(enabled: boolean): UseQueryResult<SmsPendingList, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useQuery<SmsPendingList, Error>({
    queryKey: SMS_PENDING_KEY,
    enabled: enabled && status === 'authenticated',
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/changes/pending`, { accessToken, refreshToken });
      return smsPendingListSchema.parse(raw);
    },
  });
}

export function useSendChangeNotices(): UseMutationResult<
  ChangeSendResult,
  Error,
  ChangeSendRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useMutation<ChangeSendResult, Error, ChangeSendRequest>({
    mutationKey: [...SMS_SEND_MUTATION_KEY, 'change'],
    mutationFn: async (req) => {
      const raw = await fetcher<unknown>(`${BASE}/changes/send`, {
        method: 'POST',
        body: JSON.stringify(req),
        accessToken,
        refreshToken,
      });
      return changeSendResultSchema.parse(raw);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: SMS_KEY });
    },
  });
}

export function useResolveChange(): UseMutationResult<
  { ok: boolean; version: number },
  Error,
  ChangeResolveRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useMutation<{ ok: boolean; version: number }, Error, ChangeResolveRequest>({
    mutationKey: [...SMS_SEND_MUTATION_KEY, 'resolve'],
    mutationFn: async (req) => {
      const raw = await fetcher<unknown>(`${BASE}/changes/resolve`, {
        method: 'POST',
        body: JSON.stringify(req),
        accessToken,
        refreshToken,
      });
      return changeResolveResultSchema.parse(raw);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: SMS_KEY });
    },
  });
}

export function useSmsBatches(
  params: { week_start?: string; limit?: number },
  enabled: boolean,
): UseQueryResult<SmsBatch[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useQuery<SmsBatch[], Error>({
    queryKey: ['sms', 'batches', params],
    enabled: enabled && status === 'authenticated',
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/batches${qs({ ...params })}`, {
        accessToken,
        refreshToken,
      });
      return smsBatchListSchema.parse(raw).items;
    },
  });
}

export function useSmsMessages(
  filter: SmsMessageFilter,
  enabled: boolean,
): UseQueryResult<SmsMessageList, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useQuery<SmsMessageList, Error>({
    queryKey: ['sms', 'messages', filter],
    enabled: enabled && status === 'authenticated',
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const params = { ...filter, undelivered: filter.undelivered ? 'true' : undefined };
      const raw = await fetcher<unknown>(`${BASE}/messages${qs(params)}`, {
        accessToken,
        refreshToken,
      });
      return smsMessageListSchema.parse(raw);
    },
  });
}

export function useSmsMessageDetail(
  messageId: string | null,
): UseQueryResult<SmsMessageDetail, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useQuery<SmsMessageDetail, Error>({
    queryKey: ['sms', 'message', messageId ?? ''],
    enabled: !!messageId && status === 'authenticated',
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/messages/${messageId}`, {
        accessToken,
        refreshToken,
      });
      return smsMessageDetailSchema.parse(raw);
    },
  });
}

export function useSmsSummary(month: string, enabled: boolean): UseQueryResult<SmsSummary, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useQuery<SmsSummary, Error>({
    queryKey: ['sms', 'summary', month],
    enabled: enabled && status === 'authenticated' && !!month,
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/summary${qs({ month })}`, {
        accessToken,
        refreshToken,
      });
      return smsSummarySchema.parse(raw);
    },
  });
}

export function useSmsPatientSummary(
  month: string,
  enabled: boolean,
): UseQueryResult<SmsPatientSummary, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useQuery<SmsPatientSummary, Error>({
    queryKey: ['sms', 'patients-summary', month],
    enabled: enabled && status === 'authenticated' && !!month,
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/patients/summary${qs({ month })}`, {
        accessToken,
        refreshToken,
      });
      return smsPatientSummarySchema.parse(raw);
    },
  });
}

export function useSmsPatientRecent(
  patientId: string,
  enabled: boolean,
  limit = 5,
): UseQueryResult<SmsMessage[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useQuery<SmsMessage[], Error>({
    queryKey: ['sms', 'patient-recent', patientId, limit],
    enabled: enabled && status === 'authenticated' && !!patientId,
    queryFn: async () => {
      const raw = await fetcher<unknown>(
        `${BASE}/patients/${encodeURIComponent(patientId)}/recent${qs({ limit })}`,
        { accessToken, refreshToken },
      );
      return smsRecentSchema.parse(raw).items;
    },
  });
}

export function useSmsAttentionNumbers(
  enabled: boolean,
): UseQueryResult<SmsAttentionItem[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useQuery<SmsAttentionItem[], Error>({
    queryKey: ['sms', 'numbers-attention'],
    enabled: enabled && status === 'authenticated',
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/numbers/attention`, {
        accessToken,
        refreshToken,
      });
      return smsAttentionSchema.parse(raw).items;
    },
  });
}

/** 月のまとめを Excel (xlsx) で取る。保存は呼ぶ側 (a[download])。 */
export function useDownloadSmsSummaryExcel(): UseMutationResult<Blob, Error, string> {
  const { data: session } = useSession();
  const { accessToken } = authPair(session);
  return useMutation<Blob, Error, string>({
    mutationFn: async (month) => {
      const path = `${BASE}/summary/export${qs({ month })}`;
      const headers: Record<string, string> = {};
      if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
      const res = await fetch(path, { method: 'GET', headers, cache: 'no-store' });
      if (!res.ok) {
        const text = await res.text();
        let body: unknown = text;
        try {
          body = JSON.parse(text);
        } catch {
          /* 文字のまま */
        }
        throw new ApiError(`API ${res.status} ${res.statusText} (${path})`, res.status, body);
      }
      return res.blob();
    },
  });
}
