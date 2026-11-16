/**
 * 予定のお知らせ (SMS) の画面の言葉と、日時の書き方。
 * 状態の言い方は docs/plans/sms-notify-api-contract-2026-11-16.md §11 に合わせる。
 * 分からない値が来たときも、記号のまま出さずに「分かりません」などのふつうの言葉にする。
 */

export const SMS_STATUS_LABEL: Record<string, string> = {
  queued: '送る順番を待っています',
  sending: '送っています',
  accepted: '受け付けられました（まだ届いていません）',
  delivered: '届きました',
  failed: '届きませんでした',
  unknown: '分かりません',
  skipped: '送っていません',
};

/** 表の中で使う短い言い方。 */
export const SMS_STATUS_SHORT: Record<string, string> = {
  queued: '送る順番待ち',
  sending: '送っています',
  accepted: '受け付け済み',
  delivered: '届きました',
  failed: '届きませんでした',
  unknown: '分かりません',
  skipped: '送っていません',
};

export const SMS_STATUS_OPTIONS = [
  'queued',
  'sending',
  'accepted',
  'delivered',
  'failed',
  'unknown',
  'skipped',
] as const;

export function smsStatusLabel(status: string, short = false): string {
  const table = short ? SMS_STATUS_SHORT : SMS_STATUS_LABEL;
  return table[status] ?? '分かりません';
}

/** 状態の色 (文字と背景)。 */
export function smsStatusTone(status: string): string {
  switch (status) {
    case 'delivered':
      return 'bg-emerald-50 text-emerald-800 border-emerald-200';
    case 'failed':
      return 'bg-red-50 text-red-800 border-red-200';
    case 'accepted':
    case 'sending':
    case 'queued':
      return 'bg-sky-50 text-sky-800 border-sky-200';
    default:
      return 'bg-bg-muted text-text-secondary border-border-default';
  }
}

export const SMS_SKIP_REASON_LABEL: Record<string, string> = {
  test_mode: '試験の状態のため',
  // 前の名前。古い記録にも同じ言葉を出す。
  not_in_test_allowlist: '試験の状態のため',
  expired: '送る前に時間が過ぎたため',
  superseded: '新しいお知らせを作ったため',
  contact_changed: '連絡先が変わったため',
  patient_inactive: '稼働中でなくなったため',
};

export function smsSkipReasonLabel(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return SMS_SKIP_REASON_LABEL[reason] ?? 'ほかの理由のため';
}

/** 状態の言葉。送っていないときは理由を添える。 */
export function smsStatusWithReason(
  status: string,
  skipReason: string | null | undefined,
  short = false,
): string {
  const label = smsStatusLabel(status, short);
  if (status !== 'skipped') return label;
  const r = smsSkipReasonLabel(skipReason);
  return r ? `${label}（${r}）` : label;
}

export const SMS_KIND_LABEL: Record<string, string> = {
  week: '週のお知らせ',
  change: '変更のお知らせ',
};

export function smsKindLabel(kind: string): string {
  return SMS_KIND_LABEL[kind] ?? 'そのほか';
}

/** キャリアの言葉。KDDI の値をそのまま持つので、大文字・小文字は区別しない (鍵は小文字)。 */
export const SMS_CARRIER_LABEL: Record<string, string> = {
  au: 'au',
  docomo: 'docomo',
  softbank: 'SoftBank',
  rakuten: '楽天',
  sms: '使われていない番号',
  unknown: '分かりません',
};

export const SMS_CARRIER_OPTIONS = ['au', 'docomo', 'softbank', 'rakuten', 'sms'] as const;

export function smsCarrierLabel(carrier: string | null | undefined): string {
  if (!carrier) return '—';
  return SMS_CARRIER_LABEL[carrier.trim().toLowerCase()] ?? carrier;
}

export const SMS_CHANNEL_LABEL: Record<string, string> = {
  sms: 'SMS',
  phone: 'お電話',
  none: 'お知らせしない',
};

export const SMS_SEGMENTS_LABEL: Record<string, string> = {
  '1': '1 通分',
  '2': '2 通分',
  '3+': '3 通分以上',
};

export const SMS_ROW_STATE_LABEL: Record<string, string> = {
  new: '',
  already_sent_same: '送信済み',
  changed_since_sent: '前回と内容が違います',
  sending: '送っている途中です',
  not_delivered: '前回は届いていません',
};

/**
 * SMS でお知らせできない理由 (決まり §13-3)。サーバーの言葉 (reason_label / not_sendable_label) が
 * あればそれを使い、無いときだけここの言葉を使う。
 */
export const SMS_NOT_SENDABLE_REASON_LABEL: Record<string, string> = {
  unsupported_char: 'お名前などに、SMS で送れない文字があります',
  patient_inactive: 'いまは稼働中でない方です（お電話でお伝えください）',
};

export function smsNotSendableLabel(
  reason: string | null | undefined,
  serverLabel: string | null | undefined,
): string {
  if (serverLabel) return serverLabel;
  return (reason && SMS_NOT_SENDABLE_REASON_LABEL[reason]) || 'ほかの理由';
}

/** 模擬の窓口で作った記録に添える言葉 (決まり §13-4)。 */
export const SMS_MOCK_LABEL = '模擬（実際には送っていません）';

/**
 * 送信の一覧の状態の選択肢「届いていないもの」の値。
 * サーバーには `undelivered=true` で渡す (届かなかった・分からない・送れなかった。試験の状態のため
 * 送らなかった分は除く。/sms/status の undelivered_count と同じ決まり)。
 */
export const SMS_UNDELIVERED_FILTER = 'undelivered';

const JST_DATE_TIME = new Intl.DateTimeFormat('ja-JP', {
  timeZone: 'Asia/Tokyo',
  month: 'numeric',
  day: 'numeric',
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
});

const JST_DATE = new Intl.DateTimeFormat('ja-JP', {
  timeZone: 'Asia/Tokyo',
  year: 'numeric',
  month: 'numeric',
  day: 'numeric',
});

/** ISO の日時 (UTC) を日本時間の「11/30(月) 10:05」に。 */
export function formatJstDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const parts = JST_DATE_TIME.formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '';
  return `${get('month')}/${get('day')}(${get('weekday')}) ${get('hour')}:${get('minute')}`;
}

export function formatJstDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return JST_DATE.format(d);
}

/** "2026-12-01" → "12/1(火)"。 */
export function formatYmdShort(ymd: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(ymd);
  if (!m) return ymd;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const wd = ['日', '月', '火', '水', '木', '金', '土'][d.getDay()];
  return `${Number(m[2])}/${Number(m[3])}(${wd})`;
}

/** 42 → "42 分"、135 → "2 時間 15 分"。小数は丸める。 */
export function formatWaiting(minutes: number | null | undefined): string {
  if (minutes == null || !Number.isFinite(minutes)) return '—';
  const total = Math.max(0, Math.round(minutes));
  if (total < 60) return `${total} 分`;
  const h = Math.floor(total / 60);
  const m = total % 60;
  return m === 0 ? `${h} 時間` : `${h} 時間 ${m} 分`;
}

export function formatYen(yen: number): string {
  return `${yen.toLocaleString('ja-JP')} 円`;
}

/** いまの月 (日本時間) の "YYYY-MM"。 */
export function currentJstMonth(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Tokyo',
    year: 'numeric',
    month: '2-digit',
  }).formatToParts(now);
  const y = parts.find((p) => p.type === 'year')?.value ?? '';
  const m = parts.find((p) => p.type === 'month')?.value ?? '';
  return `${y}-${m}`;
}

/** 送った後の一覧が新しくなったときの言葉 (確かめた内容と違うかもしれない)。 */
export const SMS_LIST_REFRESHED_MESSAGE = '一覧が新しくなりました。もう一度確かめてください';

/** 送れたかどうか分からないとき (通信が切れた・サーバーの 5xx) の言葉。 */
export const SMS_SEND_UNKNOWN_MESSAGE =
  '送れたかどうか分かりません。一覧が新しくなったら「送信済み」を確かめてください';
