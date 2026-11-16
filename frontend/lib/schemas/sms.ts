/**
 * 予定のお知らせ (SMS) の API の形 (zod)。
 *
 * 正典:
 *   - 段階 1: backend/app/api/v1/sms.py (preview / send / batches / messages / messages/{id})
 *   - 段階 2: docs/plans/sms-notify-api-contract-2026-11-16.md (status / changes / summary / ...)
 *
 * 本文と番号は個人の情報。画面に出すだけで、保存 (localStorage など) やログには出さない。
 */
import { z } from 'zod';

// ─── ゆるく読むための部品 ─────────────────────────────────────────────────
// 1 つの欄が null / 欠けているだけで一覧の全体が出なくならないよう、
// 表示にだけ使う欄は既定の値に置き換えて読む (送る中身はサーバーが作り直す)。
const str = () =>
  z
    .string()
    .nullish()
    .transform((v) => v ?? '');
const strOrNull = () =>
  z
    .string()
    .nullish()
    .transform((v) => v ?? null);
const num0 = () =>
  z
    .number()
    .nullish()
    .transform((v) => v ?? 0);
const numOrNull = () =>
  z
    .number()
    .nullish()
    .transform((v) => v ?? null);
/** 文字が null・欠け・空のときは、決めた言葉にする (画面に出す名前・理由)。 */
const strOr = (fallback: string) =>
  z
    .string()
    .nullish()
    .transform((v) => v || fallback);
/** お名前が分からないとき (null・空) の言葉。 */
export const SMS_NO_NAME_LABEL = '（お名前なし）';
/** 届かなかった理由が分からないとき (null・空) の言葉。 */
export const SMS_UNKNOWN_REASON_LABEL = '理由は分かりません';
const arr = <T extends z.ZodTypeAny>(item: T) =>
  z
    .array(item)
    .nullish()
    .transform((v): z.output<T>[] => v ?? []);

// ─── 共通 ───────────────────────────────────────────────────────────────

export const smsDestinationSchema = z.object({
  contact_id: str(),
  relation: str(),
  relation_label: str(),
  phone_last4: str(),
});
export type SmsDestination = z.infer<typeof smsDestinationSchema>;

export const smsVisitSlotSchema = z.object({
  date: z.string(),
  start: z.string(),
});
export type SmsVisitSlot = z.infer<typeof smsVisitSlotSchema>;

// ─── GET /sms/status ─────────────────────────────────────────────────────

export const smsStatusSchema = z.object({
  enabled: z.boolean(),
  provider: z.string().optional(),
  send_mode: z.string().optional(),
  test_allowlist_count: z.number().int().optional(),
  within_send_hours: z.boolean().optional(),
  send_hours: z.string().optional(),
  pending_count: z.number().int().optional(),
  urgent_count: z.number().int().optional(),
  failed_recent_count: z.number().int().optional(),
  /**
   * 窓口の設定がそろっているか (決まり §13-6)。false のときだけ止める
   * (欠けているときは止めない・送れるかの最後の判断はサーバーがする)。
   */
  provider_ok: z.boolean().nullish().catch(null).optional(),
  /** 直近 7 日の、届かなかった・分からない・送れなかった件数 (決まり §13-6)。 */
  undelivered_count: z.number().int().nullish().catch(null).optional(),
});
export type SmsStatus = z.infer<typeof smsStatusSchema>;

// ─── POST /sms/week-notices/preview ──────────────────────────────────────

export const SMS_ROW_STATES = [
  'new',
  'already_sent_same',
  'changed_since_sent',
  'sending',
  'not_delivered',
] as const;
export type SmsRowState = (typeof SMS_ROW_STATES)[number];

/** 前に送ろうとしたときの結果 (決まり §13-1)。 */
export const smsLastAttemptSchema = z.object({
  at: strOrNull(),
  result: str(),
  label: str(),
});
export type SmsLastAttempt = z.infer<typeof smsLastAttemptSchema>;

export const weekNoticeRowSchema = z.object({
  patient_id: z.string(),
  patient_name: str(),
  surname: str(),
  // 知らない状態が来たら「送っている途中」とみなす (選べない・送らない側に倒す・決まり §13-1)。
  state: z.enum(SMS_ROW_STATES).catch('sending'),
  last_attempt: smsLastAttemptSchema
    .nullish()
    .catch(null)
    .transform((v) => v ?? null),
  /** お名前に空白が無く、姓の代わりにお名前全体を入れたとき true。 */
  surname_uncertain: z
    .boolean()
    .nullish()
    .catch(false)
    .transform((v) => v ?? false),
  destinations: arr(smsDestinationSchema),
  visits: arr(smsVisitSlotSchema),
  body: str(),
  char_count: num0(),
  segments_est: num0(),
  message_count: num0(),
  est_yen: num0(),
  /** 見た内容の印 (決まり §12)。送るときにそのまま返す。 */
  token: strOrNull(),
});
export type WeekNoticeRow = z.infer<typeof weekNoticeRowSchema>;

export const weekNoticeNotSendableSchema = z.object({
  patient_id: z.string(),
  patient_name: str(),
  reason: str(),
  reason_label: str(),
  visits: arr(smsVisitSlotSchema),
});
export type WeekNoticeNotSendable = z.infer<typeof weekNoticeNotSendableSchema>;

export const weekNoticeTotalsSchema = z.object({
  people: num0(),
  messages: num0(),
  segments: num0(),
  yen: num0(),
});

export const weekNoticePreviewSchema = z.object({
  week_start: z.string(),
  week_label: str(),
  within_send_hours: z.boolean().catch(false),
  send_hours: str(),
  rows: arr(weekNoticeRowSchema),
  not_sendable: arr(weekNoticeNotSendableSchema),
  totals: weekNoticeTotalsSchema.nullish(),
});
export type WeekNoticePreview = z.infer<typeof weekNoticePreviewSchema>;

// ─── POST /sms/week-notices/send ─────────────────────────────────────────

export interface WeekNoticeSendRequest {
  week_start: string;
  patient_ids: string[];
  expected_count: number;
  resend: boolean;
  /** 患者の ID → 見た内容の印 (決まり §12・patient_ids の全員分)。 */
  tokens: Record<string, string>;
}

export const weekNoticeSendResultSchema = z.object({
  batch_id: z.string(),
  week_start: z.string(),
  patient_count: z.number().int(),
  message_count: z.number().int(),
  segments_est_total: z.number().int(),
});
export type WeekNoticeSendResult = z.infer<typeof weekNoticeSendResultSchema>;

// ─── GET /sms/batches ────────────────────────────────────────────────────

export const smsBatchSchema = z.object({
  id: z.string(),
  kind: z.string(),
  week_start: z.string(),
  created_by_user_id: z.string().nullable(),
  created_by_name: z.string().nullable(),
  created_at: z.string(),
  patient_count: z.number().int(),
  message_count: z.number().int(),
  segments_est_total: z.number().int(),
  status_counts: z.record(z.number().int()),
});
export const smsBatchListSchema = z.object({ items: z.array(smsBatchSchema) });
export type SmsBatch = z.infer<typeof smsBatchSchema>;

// ─── GET /sms/messages, /sms/messages/{id} ───────────────────────────────

export const smsMessageSchema = z.object({
  id: z.string(),
  batch_id: z.string(),
  kind: z.string(),
  week_start: z.string(),
  patient_id: strOrNull(),
  patient_name: strOrNull(),
  relation: strOrNull(),
  relation_label: strOrNull(),
  phone_last4: strOrNull(),
  status: z.string(),
  status_raw: strOrNull(),
  skip_reason: strOrNull(),
  error_code: strOrNull(),
  /** `mock` (模擬) / `kddi` (決まり §13-4)。 */
  provider: strOrNull(),
  char_count: num0(),
  segments_est: num0(),
  split_num: numOrNull(),
  carrier: strOrNull(),
  created_by_user_id: strOrNull(),
  created_by_name: strOrNull(),
  created_at: z.string(),
  sent_at: strOrNull(),
  accepted_at: strOrNull(),
  status_decided_at: strOrNull(),
});
export type SmsMessage = z.infer<typeof smsMessageSchema>;

export const smsMessageListSchema = z.object({
  items: z.array(smsMessageSchema),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
});
export type SmsMessageList = z.infer<typeof smsMessageListSchema>;

export const smsStatusEventSchema = z.object({
  at: z.string(),
  status: z.string(),
  raw_status: strOrNull(),
  detail: strOrNull(),
});

export const smsMessageDetailSchema = smsMessageSchema.extend({
  phone: strOrNull(),
  body: strOrNull(),
  provider_msg_id: strOrNull(),
  transaction_id: strOrNull(),
  error_message: strOrNull(),
  fail_reason_raw: strOrNull(),
  not_before: strOrNull(),
  attempt_count: num0(),
  notice_version: num0(),
  events: arr(smsStatusEventSchema),
});
export type SmsMessageDetail = z.infer<typeof smsMessageDetailSchema>;

export interface SmsMessageFilter {
  date_from?: string;
  date_to?: string;
  patient_id?: string;
  status?: string;
  kind?: string;
  carrier?: string;
  batch_id?: string;
  /** 届いていないものだけ (/status の undelivered_count と同じ決まり・日の範囲は付けない)。 */
  undelivered?: boolean;
  limit?: number;
  offset?: number;
}

// ─── GET /sms/changes/pending ────────────────────────────────────────────

export const smsChangeSchema = z.object({
  kind: str(),
  from: smsVisitSlotSchema.nullish().transform((v) => v ?? null),
  to: smsVisitSlotSchema.nullish().transform((v) => v ?? null),
  label: str(),
});

export const smsPendingItemSchema = z.object({
  patient_id: z.string(),
  patient_name: str(),
  week_start: z.string(),
  urgent: z.boolean().catch(false),
  detected_at: strOrNull(),
  /** 分 (小数もありうる・画面では丸める)。 */
  waiting_minutes: numOrNull(),
  changes: arr(smsChangeSchema),
  // 分からなければ送れない側に倒す。
  sendable: z.boolean().catch(false),
  not_sendable_reason: strOrNull(),
  not_sendable_label: strOrNull(),
  destinations: arr(smsDestinationSchema),
  call_numbers: arr(z.object({ relation_label: str(), phone_display: str() })),
  body: strOrNull(),
  char_count: numOrNull(),
  segments_est: numOrNull(),
  est_yen: numOrNull(),
  /** 見た内容の印 (決まり §12)。送れない方は null。 */
  token: strOrNull(),
  /** 変更の並びだけの印 (決まり §12)。「済みにする」ときに返す。 */
  change_token: strOrNull(),
});
export type SmsPendingItem = z.infer<typeof smsPendingItemSchema>;

export const smsPendingListSchema = z.object({
  within_send_hours: z.boolean().catch(false),
  send_hours: str(),
  count: num0(),
  urgent_count: num0(),
  items: arr(smsPendingItemSchema),
});
export type SmsPendingList = z.infer<typeof smsPendingListSchema>;

// ─── POST /sms/changes/send, /sms/changes/resolve ────────────────────────

export interface ChangeSendRequest {
  items: Array<{ patient_id: string; week_start: string; token: string }>;
  expected_count: number;
}

export const changeSendResultSchema = z.object({
  batch_id: z.string(),
  patient_count: z.number().int(),
  message_count: z.number().int(),
  segments_est_total: z.number().int(),
});
export type ChangeSendResult = z.infer<typeof changeSendResultSchema>;

export type ResolveChannel = 'phone' | 'none';
export type ResolveReason = 'phoned' | 'patient_request' | 'other';

export interface ChangeResolveRequest {
  patient_id: string;
  week_start: string;
  channel: ResolveChannel;
  reason: ResolveReason;
  note: string;
  /** 見た変更の並びの印 (決まり §12)。 */
  change_token: string;
}

export const changeResolveResultSchema = z.object({
  ok: z.boolean(),
  version: z.number().int(),
});

// ─── GET /sms/summary ────────────────────────────────────────────────────

export const smsSummarySchema = z.object({
  month: z.string(),
  totals: z.object({
    messages: z.number().int(),
    delivered: z.number().int(),
    failed: z.number().int(),
    unknown: z.number().int(),
    pending: z.number().int(),
    skipped: z.number().int(),
    delivered_rate: z.number().nullable(),
    segments: z.number().int(),
    est_yen: z.number().int(),
  }),
  by_kind: z.array(
    z.object({
      kind: z.string(),
      messages: z.number().int(),
      segments: z.number().int(),
      est_yen: z.number().int(),
    }),
  ),
  by_carrier: z.array(
    z.object({
      carrier: z.string(),
      messages: z.number().int(),
      delivered: z.number().int(),
      failed: z.number().int(),
    }),
  ),
  by_fail_reason: z.array(
    z.object({
      reason: z
        .string()
        .nullish()
        .transform((v) => v || 'unknown'),
      label: strOr(SMS_UNKNOWN_REASON_LABEL),
      count: z.number().int(),
    }),
  ),
  by_segments: z.array(z.object({ segments: z.string(), messages: z.number().int() })),
  changes: z.object({
    count: z.number().int(),
    urgent_count: z.number().int(),
    median_minutes_to_tell: z.number().nullable(),
    by_channel: z.array(z.object({ channel: z.string(), count: z.number().int() })),
  }),
  by_week: z.array(
    z.object({
      week_start: z.string(),
      messages: z.number().int(),
      delivered: z.number().int(),
      failed: z.number().int(),
    }),
  ),
});
export type SmsSummary = z.infer<typeof smsSummarySchema>;

// ─── GET /sms/patients/summary ───────────────────────────────────────────

export const smsPatientSummarySchema = z.object({
  month: z.string(),
  items: z.array(
    z.object({
      patient_id: z.string(),
      patient_name: strOr(SMS_NO_NAME_LABEL),
      messages: z.number().int(),
      segments: z.number().int(),
      delivered: z.number().int(),
      failed: z.number().int(),
      last_delivered_at: z.string().nullable(),
      told_by_phone: z.number().int(),
    }),
  ),
});
export type SmsPatientSummary = z.infer<typeof smsPatientSummarySchema>;

// ─── GET /sms/patients/{id}/recent ───────────────────────────────────────

export const smsRecentSchema = z.object({ items: z.array(smsMessageSchema) });

// ─── GET /sms/numbers/attention ──────────────────────────────────────────

export const smsAttentionSchema = z.object({
  items: z.array(
    z.object({
      patient_id: z.string(),
      patient_name: strOr(SMS_NO_NAME_LABEL),
      relation_label: z.string().nullable(),
      phone_last4: z.string().nullable(),
      kind: z.string(),
      kind_label: z.string(),
      count: z.number().int(),
      last_at: z.string().nullable(),
    }),
  ),
});
export type SmsAttentionItem = z.infer<typeof smsAttentionSchema>['items'][number];
