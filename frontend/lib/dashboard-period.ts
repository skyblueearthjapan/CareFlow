/**
 * ダッシュボード (スタッフ別の実績) の期間と表示の小道具。
 *
 * 日付は YYYY-MM-DD 文字列のまま UTC の暦で計算する (端末のタイムゾーンに依らない)。
 * 「今週」「今月」は実績の画面なので **今日まで** で切る (先の予定は数えない)。
 */

export type PeriodPreset = 'this_week' | 'last_week' | 'this_month' | 'last_month' | 'custom';

export const PERIOD_PRESETS: { key: PeriodPreset; label: string }[] = [
  { key: 'this_week', label: '今週' },
  { key: 'last_week', label: '先週' },
  { key: 'this_month', label: '今月' },
  { key: 'last_month', label: '先月' },
  { key: 'custom', label: '任意' },
];

/** 今日 (JST) の YYYY-MM-DD。 */
export function todayJst(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo' }).format(now);
}

function parse(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1));
}

function fmt(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function addDays(iso: string, days: number): string {
  const d = parse(iso);
  d.setUTCDate(d.getUTCDate() + days);
  return fmt(d);
}

/** その日を含む週の月曜。 */
function mondayOf(iso: string): string {
  const wd = (parse(iso).getUTCDay() + 6) % 7; // 月=0
  return addDays(iso, -wd);
}

/** 期間の始めと終わり (両端を含む)。任意 (custom) は呼び出し側で決める。 */
export function presetRange(
  preset: Exclude<PeriodPreset, 'custom'>,
  today: string,
): { from: string; to: string } {
  const t = parse(today);
  switch (preset) {
    case 'this_week':
      return { from: mondayOf(today), to: today };
    case 'last_week': {
      const from = addDays(mondayOf(today), -7);
      return { from, to: addDays(from, 6) };
    }
    case 'this_month':
      return { from: fmt(new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), 1))), to: today };
    case 'last_month':
      return {
        from: fmt(new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth() - 1, 1))),
        to: fmt(new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), 0))),
      };
  }
}

/** 1 回に選べる最長の期間 (日)。BE の MAX_PERIOD_DAYS と同じ。 */
export const MAX_PERIOD_DAYS = 93;

/** 期間の日数 (両端を含む)。 */
export function periodDays(from: string, to: string): number {
  return Math.round((parse(to).getTime() - parse(from).getTime()) / 86_400_000) + 1;
}

/** 'YYYY-MM-DD' → 'M/D'。 */
export function monthDay(iso: string): string {
  const [, m, d] = iso.split('-');
  return `${Number(m)}/${Number(d)}`;
}

/** 分 → 'H:MM'。 */
export function hhmm(minutes: number | null | undefined): string {
  if (minutes == null) return '—';
  const m = Math.round(minutes);
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`;
}

/** 小数 1 桁に丸めた表示 (null は「—」)。 */
export function r1(value: number | null | undefined): string {
  return value == null ? '—' : (Math.round(value * 10) / 10).toFixed(1);
}

/** 整数に丸めた表示 (null は「—」)。 */
export function r0(value: number | null | undefined): string {
  return value == null ? '—' : String(Math.round(value));
}

/** 平均との差 ('+0.4' / '-1.2' / '±0.0')。どちらかが無ければ null。 */
export function diff1(value: number | null | undefined, avg: number | null | undefined) {
  if (value == null || avg == null) return null;
  const x = Math.round((value - avg) * 10) / 10;
  if (x === 0) return '±0.0';
  return `${x > 0 ? '+' : ''}${x.toFixed(1)}`;
}
