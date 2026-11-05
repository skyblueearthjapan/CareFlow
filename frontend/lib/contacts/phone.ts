/**
 * 電話番号・メールの見分け (設計 docs/plans/contacts-phone-email-design-2026-11-05.md §3)。
 *
 * backend `app/services/contacts.py` と同じ決まり。画面は保存の前に結果を見せるために
 * 使い、正しいかの最後の判断はサーバーがする (返ってきた値を正とする)。
 *
 * 手順: NFKC で半角へ → 全角ハイフン類を `-` に → `+81` (空白・ハイフン可) で
 * 始まれば先頭を `0` に → 数字だけを digits にする。
 */

export type PhoneKind = 'mobile' | 'mobile_new' | 'ip' | 'data' | 'freedial' | 'landline';

export type PhoneErrorKind = 'too_short' | 'too_long' | 'bad_format';

export type PhoneResult =
  | {
      ok: true;
      digits: string;
      kind: PhoneKind;
      /** true=送れる / false=送れない / null=未確認 (060)。 */
      smsCapable: boolean | null;
      display: string;
    }
  | { ok: false; digits: string; error: PhoneErrorKind };

/** ハイフンに見える文字 (NFKC の後に残るもの)。 */
const HYPHEN_LIKE = /[‐-―−ーｰ﹣－]/g;

/** BOM と幅の無い文字 (Excel・コピーで混ざる)。前後を削る前に除く。backend の _ZERO_WIDTH と同じ。 */
const ZERO_WIDTH = /[\u200b\u200c\u200d\u2060\ufeff]/g;

function normalizeInput(raw: string): string {
  return raw
    .normalize('NFKC')
    .replace(ZERO_WIDTH, '')
    .replace(HYPHEN_LIKE, '-')
    .trim()
    .replace(/^\+81[\s-]*/, '0');
}

function format344(d: string): string {
  return `${d.slice(0, 3)}-${d.slice(3, 7)}-${d.slice(7)}`;
}

/** 番号の見分け。空文字・null は null (番号なし)。 */
export function classifyPhone(raw: string | null | undefined): PhoneResult | null {
  if (raw == null) return null;
  const s = normalizeInput(raw);
  if (s === '') return null;
  // 数字は ASCII の 0-9 だけ (backend と同じ)。
  const digits = s.replace(/[^0-9]/g, '');
  if (digits === '') return { ok: false, digits, error: 'bad_format' };

  // 0800+7 桁は 080 の携帯と同じ 11 桁なので、フリーダイヤルを先に見る (backend と同じ順)。
  if (/^(0120[0-9]{6}|0800[0-9]{7})$/.test(digits)) {
    return {
      ok: true,
      digits,
      kind: 'freedial',
      smsCapable: false,
      display: `${digits.slice(0, 4)}-${digits.slice(4, 7)}-${digits.slice(7)}`,
    };
  }
  if (/^0[789]0[0-9]{8}$/.test(digits)) {
    return { ok: true, digits, kind: 'mobile', smsCapable: true, display: format344(digits) };
  }
  if (/^060[0-9]{8}$/.test(digits)) {
    return { ok: true, digits, kind: 'mobile_new', smsCapable: null, display: format344(digits) };
  }
  if (/^050[0-9]{8}$/.test(digits)) {
    return { ok: true, digits, kind: 'ip', smsCapable: false, display: format344(digits) };
  }
  if (/^020[0-9]{8}$/.test(digits)) {
    return { ok: true, digits, kind: 'data', smsCapable: false, display: format344(digits) };
  }
  if (/^0[1-9][0-9]{8}$/.test(digits)) {
    // 固定電話: 入力にハイフンがあればその位置 (区切りを除いた並びが digits と一致するとき)。
    const kept = s
      .replace(/[^0-9-]/g, '')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '');
    const display = kept.includes('-') && kept.replace(/-/g, '') === digits ? kept : digits;
    return { ok: true, digits, kind: 'landline', smsCapable: false, display };
  }
  if (digits.length < 10) return { ok: false, digits, error: 'too_short' };
  if (digits.length > 11) return { ok: false, digits, error: 'too_long' };
  return { ok: false, digits, error: 'bad_format' };
}

export const PHONE_KIND_LABEL: Record<PhoneKind, string> = {
  mobile: '携帯',
  mobile_new: '携帯（060）',
  ip: 'IP 電話',
  data: 'データ通信など',
  freedial: 'フリーダイヤル',
  landline: '固定電話',
};

export const PHONE_ERROR_LABEL: Record<PhoneErrorKind, string> = {
  too_short: '桁が足りない',
  too_long: '桁が多い',
  bad_format: '番号の形が違う',
};

/** 番号が正しく、SMS が使える (060 は送れる扱い・印は「確認中」)。 */
export function phoneCanSms(r: PhoneResult | null): boolean {
  return !!r && r.ok && r.smsCapable !== false;
}

// ─── メール ──────────────────────────────────────────────────────────────────

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const CARRIER_RE =
  /@(docomo\.ne\.jp|ezweb\.ne\.jp|au\.com|softbank\.ne\.jp|i\.softbank\.jp|ymobile\.ne\.jp)$/;

export type EmailResult = { ok: true; email: string; carrier: boolean } | { ok: false };

/** メールの見分け。空文字・null は null (メールなし)。 */
export function classifyEmail(raw: string | null | undefined): EmailResult | null {
  if (raw == null) return null;
  const s = raw.replace(ZERO_WIDTH, '').trim().toLowerCase();
  if (s === '') return null;
  if (!EMAIL_RE.test(s)) return { ok: false };
  return { ok: true, email: s, carrier: CARRIER_RE.test(s) };
}
