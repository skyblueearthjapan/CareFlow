/**
 * 患者様の連絡先の編集の決まり (画面側)。設計 §2・見本の「患者の編集」。
 *
 * サーバー (`normalize_patient_contacts`) と同じ移し方を画面でも先にして、
 * 保存の前に結果を見せる。最後の判断はサーバー (返ってきた notices を出す)。
 */
import {
  MAX_PATIENT_CONTACTS,
  type ConsentStatus,
  type ContactRead,
  type ContactRelation,
  type ContactWrite,
  type EffectiveChannel,
  type NotifyChannel,
} from '@/lib/schemas/patientContacts';

import { classifyEmail, classifyPhone, phoneCanSms } from './phone';

export interface ContactDraft {
  /** 画面の中だけの key (React の行の key)。 */
  key: string;
  /** 既存の連絡先の id (同意の日を引き継ぐため PUT に付ける)。 */
  id?: string;
  relation: ContactRelation;
  name: string;
  relation_note: string;
  phone: string;
  email: string;
  is_notify_target: boolean;
  notify_channel: NotifyChannel;
  consent_status: ConsentStatus;
  /** 見せるだけの同意の日 (YYYY-MM-DD)。保存ではサーバーが決める。 */
  consent_on: string | null;
  /** 読んだときの同意 (元に戻したら日付も元に戻す)。 */
  orig_consent_status?: ConsentStatus;
  orig_consent_on?: string | null;
}

let keySeq = 0;
function nextKey(): string {
  keySeq += 1;
  return `c${keySeq}`;
}

export function draftFromRead(c: ContactRead): ContactDraft {
  return {
    key: c.id,
    id: c.id,
    relation: c.relation,
    name: c.name ?? '',
    relation_note: c.relation_note ?? '',
    phone: c.phone_display ?? c.phone ?? '',
    email: c.email ?? '',
    is_notify_target: c.is_notify_target,
    notify_channel: c.notify_channel,
    consent_status: c.consent_status,
    consent_on: c.consent_on ?? null,
    orig_consent_status: c.consent_status,
    orig_consent_on: c.consent_on ?? null,
  };
}

export function draftsFromRead(rows: readonly ContactRead[]): ContactDraft[] {
  return [...rows].sort((a, b) => a.sort_order - b.sort_order).map(draftFromRead);
}

function emptyToNull(s: string): string | null {
  const t = s.trim();
  return t === '' ? null : t;
}

export function draftToWrite(d: ContactDraft): ContactWrite {
  const self = d.relation === 'self';
  const out: ContactWrite = {
    relation: d.relation,
    name: self ? null : emptyToNull(d.name),
    relation_note: self ? null : emptyToNull(d.relation_note),
    phone: emptyToNull(d.phone),
    email: emptyToNull(d.email),
    is_notify_target: d.is_notify_target,
    notify_channel: d.notify_channel,
    consent_status: d.consent_status,
  };
  if (d.id) out.id = d.id;
  return out;
}

/** 送る中身が変わったか (同意の日など見せるだけの値は比べない)。 */
export function contactsChanged(a: readonly ContactDraft[], b: readonly ContactDraft[]): boolean {
  return JSON.stringify(a.map(draftToWrite)) !== JSON.stringify(b.map(draftToWrite));
}

/** 使える手段。 */
export function contactMeans(d: Pick<ContactDraft, 'phone' | 'email'>): {
  sms: boolean;
  email: boolean;
} {
  const e = classifyEmail(d.email);
  return { sms: phoneCanSms(classifyPhone(d.phone)), email: !!e && e.ok };
}

/** 選んだ手段が使えなければ使える方へ落とす (サーバーの effective_channel と同じ)。 */
export function effectiveChannel(
  d: Pick<ContactDraft, 'phone' | 'email' | 'notify_channel'>,
): EffectiveChannel {
  const m = contactMeans(d);
  if (d.notify_channel === 'sms' && m.sms) return 'sms';
  if (d.notify_channel === 'email' && m.email) return 'email';
  return m.sms ? 'sms' : m.email ? 'email' : 'none';
}

/** 「〇 件目（〜）」の呼び方 (知らせの文に使う)。 */
export function contactLabel(list: readonly ContactDraft[], i: number): string {
  const c = list[i];
  if (!c) return '';
  const who =
    c.relation === 'self'
      ? 'ご本人'
      : c.name.trim() || c.relation_note.trim() || RELATION_SHORT[c.relation];
  return `${i + 1} 件目（${who}）`;
}

const RELATION_SHORT: Record<ContactRelation, string> = {
  self: 'ご本人',
  family: 'ご家族',
  facility: '施設',
  care_manager: 'ケアマネ',
  other: 'その他',
};

/**
 * 送る先が 1 件もなくなったら、ほかの連絡先へ移す。
 * NG 回答でないもののうち、ご本人を先に、次に手段があるもの、同じなら並び順。
 * 返り値: 移した先の番号 / 移す必要が無い = -1 / 移す先が無い = null。
 */
export function pickFallbackTarget(
  list: readonly ContactDraft[],
  excludeIndex: number,
): number | null {
  if (list.length === 0 || list.some((c) => c.is_notify_target)) return -1;
  const order = list
    .map((_, i) => i)
    .filter((i) => i !== excludeIndex && list[i]!.consent_status !== 'ng')
    .sort((x, y) => {
      const a = list[x]!;
      const b = list[y]!;
      const self = Number(b.relation === 'self') - Number(a.relation === 'self');
      if (self !== 0) return self;
      const means = Number(effectiveChannel(b) !== 'none') - Number(effectiveChannel(a) !== 'none');
      if (means !== 0) return means;
      return x - y;
    });
  return order.length > 0 ? order[0]! : null;
}

export interface EditResult {
  contacts: ContactDraft[];
  /** 画面に出す知らせ (無ければ null)。 */
  notice: string | null;
}

function withFallback(list: ContactDraft[], excludeIndex: number): EditResult | null {
  const idx = pickFallbackTarget(list, excludeIndex);
  if (idx === -1) return { contacts: list, notice: null };
  if (idx === null) return null;
  const next = list.map((c, i) => (i === idx ? { ...c, is_notify_target: true } : c));
  return {
    contacts: next,
    notice: `お知らせは少なくとも 1 件に送るため、${contactLabel(next, idx)}へ移しました。`,
  };
}

/** 「お知らせを送る」の四角を付ける・外す。最後の 1 件は外させない。 */
export function setNotifyTarget(
  list: readonly ContactDraft[],
  i: number,
  checked: boolean,
): EditResult {
  const next = list.map((c, k) => (k === i ? { ...c, is_notify_target: checked } : c));
  if (checked) return { contacts: next, notice: null };
  const moved = withFallback(next, i);
  if (moved) return moved;
  return {
    contacts: [...list],
    notice: 'お知らせは少なくとも 1 件に送ります。外すときは、先にほかの連絡先を足してください。',
  };
}

/** 今日 (JST) の YYYY-MM-DD。 */
export function todayJst(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Tokyo',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(now);
}

/** 同意を変える。NG 回答は送る先から外す (ほかへ移せなければ NG にしない)。 */
export function setConsent(
  list: readonly ContactDraft[],
  i: number,
  status: ConsentStatus,
  today: string = todayJst(),
): EditResult {
  const cur = list[i];
  if (!cur) return { contacts: [...list], notice: null };
  const consent_on =
    cur.orig_consent_status !== undefined && status === cur.orig_consent_status
      ? (cur.orig_consent_on ?? null)
      : status === 'unconfirmed'
        ? null
        : today;
  const updated: ContactDraft = { ...cur, consent_status: status, consent_on };
  if (status === 'ng') updated.is_notify_target = false;
  const next = list.map((c, k) => (k === i ? updated : c));
  if (status !== 'ng' || !cur.is_notify_target) return { contacts: next, notice: null };
  const moved = withFallback(next, i);
  if (moved) return moved;
  return {
    contacts: [...list],
    notice: 'ほかに送れる連絡先がないため、NG 回答にできません。先にほかの連絡先を足してください。',
  };
}

/** 連絡先を外す。送る先が無くなったら移す。 */
export function removeContact(list: readonly ContactDraft[], i: number): EditResult {
  const next = list.filter((_, k) => k !== i);
  return withFallback(next, -1) ?? { contacts: next, notice: null };
}

/** 連絡先を足す (3 件まで)。ご本人がまだいなければご本人から。 */
export function addContact(list: readonly ContactDraft[]): EditResult {
  if (list.length >= MAX_PATIENT_CONTACTS) return { contacts: [...list], notice: null };
  const hasSelf = list.some((c) => c.relation === 'self');
  const draft: ContactDraft = {
    key: nextKey(),
    relation: hasSelf ? 'family' : 'self',
    name: '',
    relation_note: '',
    phone: '',
    email: '',
    is_notify_target: !list.some((c) => c.is_notify_target),
    notify_channel: 'sms',
    consent_status: 'unconfirmed',
    consent_on: null,
  };
  return { contacts: [...list, draft], notice: null };
}

/** 「ご本人だけ」。 */
export function onlySelf(list: readonly ContactDraft[]): EditResult {
  const si = list.findIndex((c) => c.relation === 'self');
  if (si < 0) return { contacts: [...list], notice: 'ご本人の連絡先がありません。' };
  if (list[si]!.consent_status === 'ng') {
    return { contacts: [...list], notice: 'ご本人は NG 回答のため、送る先にできません。' };
  }
  return {
    contacts: list.map((c, i) => ({ ...c, is_notify_target: i === si })),
    notice: null,
  };
}

/** 「登録した全員に送る」(NG 回答の連絡先は除く)。 */
export function allTargets(list: readonly ContactDraft[]): EditResult {
  return {
    contacts: list.map((c) => (c.consent_status === 'ng' ? c : { ...c, is_notify_target: true })),
    notice: null,
  };
}

/** 保存の前に止める誤り (電話・メールの形)。1 件ずつ日本語で。 */
export function contactErrors(list: readonly ContactDraft[]): string[] {
  const out: string[] = [];
  list.forEach((c, i) => {
    const p = classifyPhone(c.phone);
    if (p && !p.ok) out.push(`${i + 1} 件目の電話番号の形が違います。`);
    const e = classifyEmail(c.email);
    if (e && !e.ok) out.push(`${i + 1} 件目のメールアドレスの形が違います。`);
  });
  if (list.filter((c) => c.relation === 'self').length > 1) {
    out.push('ご本人の連絡先は 1 件だけにしてください。');
  }
  return out;
}
