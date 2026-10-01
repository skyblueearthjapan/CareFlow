/**
 * 打刻履歴タブの表示ヘルパ — 期間の計算・日付の書式・備考バッジ（純関数のみ）。
 *
 * 期間は「単位（週 / 月 / 期間指定）＋ from / to」で持つ。‹ › は単位ぶん前後へ送り、
 * 期間ボタンの選択表示は**いまの from / to がどのプリセットと一致するか**で決める
 * （今月から ‹ で 1 つ戻れば「先月」が選択表示になる）。
 */

import type { ActualTimeKind, VisitHistoryRow } from '@/lib/queries/visit-history';

export type HistoryPreset = 'week' | 'lastweek' | 'month' | 'lastmonth';
export type HistoryUnit = 'week' | 'month' | 'custom';

export interface HistoryRange {
  unit: HistoryUnit;
  /** `YYYY-MM-DD`（inclusive）。 */
  from: string;
  to: string;
}

export const HISTORY_PRESETS: ReadonlyArray<{ key: HistoryPreset; label: string }> = [
  { key: 'week', label: '今週' },
  { key: 'lastweek', label: '先週' },
  { key: 'month', label: '今月' },
  { key: 'lastmonth', label: '先月' },
];

/** BE が受ける期間の上限（日数・両端を含む。超えると 422）。 */
export const MAX_RANGE_DAYS = 92;

const WEEKDAYS = ['日', '月', '火', '水', '木', '金', '土'] as const;

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

function toIso(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** `YYYY-MM-DD` → 端末ローカルの日付（UTC 解釈で 1 日ずれるのを避ける）。不正は null。 */
function parseYmd(ymd: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(ymd);
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isNaN(d.getTime()) ? null : d;
}

function addDays(ymd: string, days: number): string {
  const d = parseYmd(ymd);
  if (!d) return ymd;
  d.setDate(d.getDate() + days);
  return toIso(d);
}

function monthRange(year: number, month: number): HistoryRange {
  return {
    unit: 'month',
    from: toIso(new Date(year, month, 1)),
    to: toIso(new Date(year, month + 1, 0)),
  };
}

/** プリセット → from / to（週は月曜始まり・`recordPeriodRange` と同じ）。 */
export function presetRange(preset: HistoryPreset, today: Date = new Date()): HistoryRange {
  const base = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  if (preset === 'week' || preset === 'lastweek') {
    const back = ((base.getDay() + 6) % 7) + (preset === 'lastweek' ? 7 : 0);
    const monday = addDays(toIso(base), -back);
    return { unit: 'week', from: monday, to: addDays(monday, 6) };
  }
  return monthRange(base.getFullYear(), base.getMonth() - (preset === 'lastmonth' ? 1 : 0));
}

/** ‹ › — 週なら 7 日、月なら 1 か月ぶん送る。期間指定はそのまま。 */
export function shiftRange(range: HistoryRange, dir: -1 | 1): HistoryRange {
  if (range.unit === 'week') {
    return { unit: 'week', from: addDays(range.from, 7 * dir), to: addDays(range.to, 7 * dir) };
  }
  if (range.unit === 'month') {
    const f = parseYmd(range.from);
    if (!f) return range;
    return monthRange(f.getFullYear(), f.getMonth() + dir);
  }
  return range;
}

/** いまの期間がどのプリセットと一致するか（一致なし・期間指定は null）。 */
export function matchPreset(range: HistoryRange, today: Date = new Date()): HistoryPreset | null {
  if (range.unit === 'custom') return null;
  for (const { key } of HISTORY_PRESETS) {
    const p = presetRange(key, today);
    if (p.unit === range.unit && p.from === range.from && p.to === range.to) return key;
  }
  return null;
}

/** 両端を含む日数。読めない日付は 0。 */
export function rangeDays(from: string, to: string): number {
  const f = parseYmd(from);
  const t = parseYmd(to);
  if (!f || !t) return 0;
  return Math.round((t.getTime() - f.getTime()) / 86_400_000) + 1;
}

/** `YYYY-MM-DD` → `M/D (曜)`。読めない値は空文字。 */
export function formatHistoryDate(ymd: string | null | undefined): string {
  const d = ymd ? parseYmd(ymd) : null;
  if (!d) return '';
  return `${d.getMonth() + 1}/${d.getDate()} (${WEEKDAYS[d.getDay()]})`;
}

/** `YYYY-MM-DD` → `YYYY/MM/DD (曜)`。 */
export function formatHistoryDateLong(ymd: string | null | undefined): string {
  const d = ymd ? parseYmd(ymd) : null;
  if (!d) return '';
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} (${WEEKDAYS[d.getDay()]})`;
}

/** 期間ラベル `2026/09/01 (火) 〜 09/30 (水)`（終わり側は年を省く）。 */
export function formatRangeLabel(from: string, to: string): string {
  return `${formatHistoryDateLong(from)} 〜 ${formatHistoryDateLong(to).slice(5)}`;
}

/** 予定時刻 `HH:MM[:SS]` → `HH:MM`。 */
function hm(time: string | null | undefined): string {
  return time ? time.slice(0, 5) : '';
}

/** 予定の `HH:MM–HH:MM`。予定外（null）は null。 */
export function plannedRange(row: Pick<VisitHistoryRow, 'start_time' | 'end_time'>): string | null {
  const start = hm(row.start_time);
  if (!start) return null;
  const end = hm(row.end_time);
  return end ? `${start}–${end}` : start;
}

/** 予定の所要（分）。予定が無い・読めないときは null。 */
export function plannedMinutes(
  row: Pick<VisitHistoryRow, 'start_time' | 'end_time'>,
): number | null {
  const toMin = (t: string | null | undefined): number | null => {
    const m = /^(\d{1,2}):(\d{2})/.exec(t ?? '');
    return m ? Number(m[1]) * 60 + Number(m[2]) : null;
  };
  const s = toMin(row.start_time);
  const e = toMin(row.end_time);
  return s == null || e == null || e <= s ? null : e - s;
}

/** 行の「看護師」— 実際の打刻者、無ければ予定の担当（BE の並びキーと同じ）。 */
export function nurseName(
  row: Pick<VisitHistoryRow, 'actual_staff_name' | 'planned_staff_name'>,
): string {
  return row.actual_staff_name ?? row.planned_staff_name ?? '';
}

/** 位置判定が要確認か（備考「場所 要確認」と同じ条件）。 */
export function isLocationReview(matchStatus: string | null | undefined): boolean {
  return matchStatus === 'review' || matchStatus === 'mismatch' || matchStatus === 'no_gps';
}

export type RemarkTone = 'success' | 'warning' | 'info' | 'muted' | 'unplanned' | 'adjust';

/** BE の備考「遅れて届いた（10/2 8:30 受信）」の共通部分 (history.REMARK_LATE_DELIVERY)。 */
export const LATE_DELIVERY_REMARK = '遅れて届いた';

export interface RemarkBadge {
  /** 表の中で見せる短い文言。 */
  label: string;
  tone: RemarkTone;
  /** BE の備考そのまま（短くしたときだけ `title` に出す）。 */
  full?: string;
}

/**
 * 備考バッジ。打刻の有無（これから / 打刻なし / 訪問中）は `state` から、
 * それ以外は **BE の `remarks` をそのまま**使う（条件を FE に複製しない）。
 * 表では「代行（予定: ○○）」→「代行」、「予定外の訪問」→「予定外」、
 * 「時刻調整」→「調整」に縮める。「調整」は注意ではない（遅れて記録されるのは
 * 看護師の誤りではない）ので、注意色にせず専用の色で出す。BE の備考に無くても
 * 行のフラグ（`arrival_adjusted` など）が立っていれば出す。
 */
export function remarkBadges(
  row: Pick<VisitHistoryRow, 'state' | 'remarks'> &
    Partial<Pick<VisitHistoryRow, 'arrival_adjusted' | 'departure_adjusted' | 'departure_manual'>>,
): RemarkBadge[] {
  const out: RemarkBadge[] = [];
  if (row.state === 'future') out.push({ label: 'これから', tone: 'muted' });
  else if (row.state === 'none') out.push({ label: '打刻なし', tone: 'muted' });
  else if (row.state === 'in_progress') out.push({ label: '訪問中', tone: 'success' });

  let adjusted = false;
  for (const remark of row.remarks ?? []) {
    if (remark.startsWith('代行')) out.push({ label: '代行', tone: 'info', full: remark });
    else if (remark.startsWith('予定外')) {
      out.push({ label: '予定外', tone: 'unplanned', full: remark });
    } else if (remark.startsWith('時刻調整')) {
      adjusted = true;
      out.push({ label: '調整', tone: 'adjust', full: remark });
    } else if (remark.includes(LATE_DELIVERY_REMARK)) {
      // 圏外で退避して後から届いた打刻。注意色にしない (看護師の誤りではない)。
      // 到着・退出が別々に届いた 2 つの備考は 1 つの札にまとめ、全文は title に出す。
      const late = out.find((b) => b.label === LATE_DELIVERY_REMARK);
      if (late) late.full = `${late.full}、${remark}`;
      else out.push({ label: LATE_DELIVERY_REMARK, tone: 'muted', full: remark });
    } else if (remark === 'QRなし') out.push({ label: remark, tone: 'muted' });
    else out.push({ label: remark, tone: 'warning' });
  }
  if (!adjusted && isAdjustedRow(row)) out.push({ label: '調整', tone: 'adjust' });
  return out;
}

/** 実績の時刻に調整がある行か（到着・退出のどちらか、または手で入れた退出）。 */
export function isAdjustedRow(
  row: Partial<
    Pick<VisitHistoryRow, 'arrival_adjusted' | 'departure_adjusted' | 'departure_manual'>
  >,
): boolean {
  return !!row.arrival_adjusted || !!row.departure_adjusted || !!row.departure_manual;
}

/** 到着 / 退出の表示名。 */
export const ACTUAL_KIND_LABEL: Record<ActualTimeKind, string> = {
  arrival: '到着',
  departure: '退出',
};

const JST_MD_HM = new Intl.DateTimeFormat('ja-JP', {
  month: 'numeric',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
  timeZone: 'Asia/Tokyo',
});

/** 合わせた日時（ISO）→ JST の `M/D HH:MM`。読めない値は空文字。 */
export function formatAdjustedAt(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : JST_MD_HM.format(d);
}

export interface HistoryGroup {
  /** 見出し（看護師名 / 患者名）。 */
  title: string;
  rows: VisitHistoryRow[];
  /** 打刻（到着）のある件数。 */
  arrived: number;
}

/** 並び順どおりに連続する行を見出しごとにまとめる（BE が並べた順を崩さない）。 */
export function groupRows(
  rows: VisitHistoryRow[],
  keyOf: (row: VisitHistoryRow) => string,
): HistoryGroup[] {
  const groups: HistoryGroup[] = [];
  for (const row of rows) {
    const title = keyOf(row);
    let last = groups[groups.length - 1];
    if (!last || last.title !== title) {
      last = { title, rows: [], arrived: 0 };
      groups.push(last);
    }
    last.rows.push(row);
    if (row.arrival_at) last.arrived += 1;
  }
  return groups;
}
