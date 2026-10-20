/**
 * 休みの種類と境目の時刻、カイポケの件名からの種類の判定。
 *
 * バックエンドの `backend/app/core/leave.py` と同じ値・同じ規則
 * (正典: docs/plans/shared-foundation-leave-acceptance-2026-10-20.md §0-1)。
 * 13:00・12:30・'off' などの値はここだけに書き、画面はここを読む。
 * 時刻は 'HH:MM' (先頭 0 埋め・'HH:MM:SS' も可) の文字列で扱う。
 */

export type FullDayOff = 'off' | 'paid_off';
export type HalfDayOff = 'am_off' | 'pm_off';
export type LeaveType = FullDayOff | HalfDayOff;
/** カイポケの件名から決めた種類。'unknown' = 休みの語はあるが種類を決められない。 */
export type KaipokeLeaveKind = LeaveType | 'unknown';

/** 終日の休み (有給も終日休み)。 */
export const FULL_DAY_OFF: ReadonlySet<string> = new Set<FullDayOff>(['off', 'paid_off']);
/** 半日の休み。 */
export const HALF_DAY_OFF: ReadonlySet<string> = new Set<HalfDayOff>(['am_off', 'pm_off']);
/** 休みの種類 ('custom_time' は休みではない)。 */
export const LEAVE_TYPES: ReadonlySet<string> = new Set<LeaveType>([
  'off',
  'paid_off',
  'am_off',
  'pm_off',
]);

/** 午前休の人はこの時刻から勤務。 */
export const AM_OFF_UNTIL = '13:00';
/** 午後休の人はこの時刻から休み。 */
export const PM_OFF_FROM = '12:30';

/** 「休」を含むが休みではない語。先に取り除いてから休みの語を探す。 */
export const LEAVE_TITLE_EXCLUSIONS = ['休憩', '休日出勤', '休日当番', '休薬'] as const;
/** これを含めば休みの件名 (「休み」「公休」「夏季休暇」などは「休」で拾う)。 */
export const LEAVE_TITLE_MARKERS = ['休', '有給', '有休', '年休', '欠勤', '忌引', '病欠'] as const;

/** 有給 (「年休」= 年次有給休暇)。 */
const PAID_WORDS = ['有給', '有休', '年休'];
const AM_WORDS = ['AM', '午前'];
const PM_WORDS = ['PM', '午後'];
/** 終日の休み (公休) とみなす語。 */
const FULL_DAY_WORDS = ['代休', '欠勤', '病欠', '忌引', '公休'];

/** 時刻で決めるときの幅 (分)。始まりは勤務の始まり＋30 分以内、終わりは勤務の終わり−60 分以降。 */
const START_SLACK_MIN = 30;
const END_SLACK_MIN = 60;
/** 午前休の終わり・午後休の始まりの幅。 */
const AM_END_RANGE = ['12:00', '14:00'] as const;
const PM_START_RANGE = ['12:00', '13:30'] as const;
/** 勤務時刻が無いときの既定 (バックエンドの営業時間の既定と同じ・設定があれば呼び出し側が渡す)。 */
export const DEFAULT_BUSINESS_HOURS: readonly [string, string] = ['09:30', '18:00'];

/** 'H:MM' / 'HH:MM:SS' → 'HH:MM' (文字列のまま大小を比べられる形)。 */
function hhmm(value: string): string {
  const [h = '0', m = '0'] = value.split(':');
  return `${h.padStart(2, '0')}:${m.padStart(2, '0').slice(0, 2)}`;
}

const maxTime = (a: string, b: string): string => (a > b ? a : b);
const minTime = (a: string, b: string): string => (a < b ? a : b);

/**
 * 休みの種類と勤務時間 → その日に働ける時間 [開始, 終了]。終日休みなら null。
 * 休みでない種類はそのまま返す。半休で働ける時間が残らないときも null。
 */
export function leaveWindow(
  leaveType: string | null | undefined,
  shiftStart: string,
  shiftEnd: string,
): [string, string] | null {
  const s = hhmm(shiftStart);
  const e = hhmm(shiftEnd);
  if (leaveType && FULL_DAY_OFF.has(leaveType)) return null;
  if (leaveType === 'am_off') {
    const start = maxTime(s, AM_OFF_UNTIL);
    return start < e ? [start, e] : null;
  }
  if (leaveType === 'pm_off') {
    const end = minTime(e, PM_OFF_FROM);
    return s < end ? [s, end] : null;
  }
  return [s, e];
}

function minutes(value: string): number {
  const [h, m] = hhmm(value).split(':');
  return Number(h) * 60 + Number(m);
}

/**
 * カイポケの個別業務の件名＋時刻 → 休みの種類。休みでなければ null。
 * 規則はバックエンドの `classify_kaipoke_leave` と同じ (共通の土台 §0-1 の表を上から順に)。
 * 勤務時刻が無いときは `businessHours` (営業時間・既定 9:30〜18:00) で代える。
 */
export function classifyKaipokeLeave(
  subject: string | null | undefined,
  start?: string | null,
  end?: string | null,
  shiftStart?: string | null,
  shiftEnd?: string | null,
  businessHours: readonly [string, string] = DEFAULT_BUSINESS_HOURS,
): KaipokeLeaveKind | null {
  let text = (subject ?? '').normalize('NFKC').trim();
  for (const word of LEAVE_TITLE_EXCLUSIONS) text = text.split(word).join('');
  if (!LEAVE_TITLE_MARKERS.some((word) => text.includes(word))) return null;
  const upper = text.toUpperCase();
  if (PAID_WORDS.some((w) => upper.includes(w))) return 'paid_off';
  if (AM_WORDS.some((w) => upper.includes(w))) return 'am_off';
  if (PM_WORDS.some((w) => upper.includes(w))) return 'pm_off';
  if (FULL_DAY_WORDS.some((w) => upper.includes(w))) return 'off';
  if (!start || !end) return 'off';
  const [ss, se] = shiftStart && shiftEnd ? [shiftStart, shiftEnd] : businessHours;
  const st = hhmm(start);
  const en = hhmm(end);
  const startsEarly = minutes(st) <= minutes(ss) + START_SLACK_MIN;
  const endsLate = minutes(en) >= minutes(se) - END_SLACK_MIN;
  if (startsEarly && endsLate) return 'off';
  if (startsEarly && AM_END_RANGE[0] <= en && en <= AM_END_RANGE[1]) return 'am_off';
  if (PM_START_RANGE[0] <= st && st <= PM_START_RANGE[1] && endsLate) return 'pm_off';
  return 'unknown';
}

/** 件名が休み (種類を問わない) か。 */
export function isLeaveEvent(subject: string | null | undefined): boolean {
  return classifyKaipokeLeave(subject) !== null;
}

/** 休みの申請の締め切りの日。対象日の月の前の月のこの日の 23:59 (JST) まで (設計 §2-3)。 */
export const LEAVE_REQUEST_DEADLINE_DAY = 20;

/** JST は UTC+9 (夏時間なし)。 */
const JST_OFFSET_HOURS = 9;

/**
 * 対象の月 ('YYYY-MM' または 'YYYY-MM-DD') の休みの申請の締め切りの時刻
 * (前の月の 20 日 23:59:59.999 JST)。これを過ぎた申請は「変更のお願い」になる。
 */
export function leaveRequestDeadline(target: string): Date {
  const [y = 0, m = 1] = target.split('-').map(Number);
  // 前の月の 21 日 0:00 JST の 1 ミリ秒前。Date.UTC は月・時の負の値を前へ繰り下げる。
  return new Date(Date.UTC(y, m - 2, LEAVE_REQUEST_DEADLINE_DAY + 1, -JST_OFFSET_HOURS) - 1);
}

/** その日 (またはその月) の休みの申請の締め切りを過ぎたか。 */
export function isPastLeaveDeadline(target: string, now: Date = new Date()): boolean {
  return now.getTime() > leaveRequestDeadline(target).getTime();
}

/** JST の今日 'YYYY-MM-DD' (端末の時刻帯によらない)。 */
export function jstToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo' }).format(now);
}

/**
 * 締め切りがまだ来ていない最初の月 'YYYY-MM' (JST)。
 * 20 日までなら来月、21 日からは再来月。
 */
export function firstOpenLeaveMonth(now: Date = new Date()): string {
  const [y = 0, m = 1] = jstToday(now).split('-').map(Number);
  for (let add = 1; ; add++) {
    const d = new Date(Date.UTC(y, m - 1 + add, 1));
    const ym = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
    if (!isPastLeaveDeadline(ym, now)) return ym;
  }
}
