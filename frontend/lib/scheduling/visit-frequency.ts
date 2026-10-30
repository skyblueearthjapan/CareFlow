/**
 * 訪問頻度 (毎週 / 隔週 / 月の第何週) の画面用の小さな道具。
 *
 * 正典: docs/plans/biweekly-monthly-design-2026-10-30.md §3・§4。
 * 「どの週に訪問するか」の判定そのものは backend
 * (`app/services/scheduling/visit_frequency.py`) が持ち、画面は
 * `GET /patients/{id}/visit-frequency-preview` の結果を並べるだけにする
 * (表示と計算をずらさない)。ここにあるのは日付の整形と入力の整えだけ。
 */
import type { WeeklyPattern } from '@/lib/schemas/patient';

const DOW_JA = ['日', '月', '火', '水', '木', '金', '土'] as const;

/** "YYYY-MM-DD" をローカル日付として読む (UTC にずらさない)。不正なら null。 */
export function parseYmd(value: string | null | undefined): Date | null {
  if (!value) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  if (
    d.getFullYear() !== Number(m[1]) ||
    d.getMonth() !== Number(m[2]) - 1 ||
    d.getDate() !== Number(m[3])
  ) {
    return null;
  }
  return d;
}

/** Date → "YYYY-MM-DD" (ローカル)。 */
export function toYmd(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/** どの日付を選んでも、その週の月曜 ("YYYY-MM-DD") にそろえる。不正なら null。 */
export function snapToMonday(value: string | null | undefined): string | null {
  const d = parseYmd(value);
  if (!d) return null;
  const offset = (d.getDay() + 6) % 7; // 月=0 … 日=6
  d.setDate(d.getDate() - offset);
  return toYmd(d);
}

/** "2026-11-05" → "11/5（木）"。不正なら元の文字列。 */
export function formatMonthDayDow(value: string): string {
  const d = parseYmd(value);
  if (!d) return value;
  return `${d.getMonth() + 1}/${d.getDate()}（${DOW_JA[d.getDay()]}）`;
}

/** "1,3" → [1, 3] (1〜5 だけ・重複なし・昇順)。全角の数字・読点も受ける。 */
export function parseVisitWeeks(value: string | null | undefined): number[] {
  if (!value) return [];
  const normalized = value
    .replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0))
    .replace(/[、，]/g, ',');
  const set = new Set<number>();
  for (const part of normalized.split(',')) {
    const n = Number(part.trim());
    if (Number.isInteger(n) && n >= 1 && n <= 5) set.add(n);
  }
  return [...set].sort((a, b) => a - b);
}

/** [3, 1] → "1,3"。空は null。 */
export function formatVisitWeeks(weeks: readonly number[]): string | null {
  const uniq = [...new Set(weeks.filter((n) => Number.isInteger(n) && n >= 1 && n <= 5))].sort(
    (a, b) => a - b,
  );
  return uniq.length > 0 ? uniq.join(',') : null;
}

export type FrequencyStatus = 'ok' | 'missing_anchor' | 'missing_weeks';

/** backend の `frequency_status` と同じ決まり (設定が足りないときは毎週作る)。 */
export function frequencyStatusOf(
  wp: Pick<WeeklyPattern, 'visit_frequency' | 'biweekly_anchor' | 'visit_weeks'>,
): FrequencyStatus {
  if (wp.visit_frequency === 'biweekly' && !snapToMonday(wp.biweekly_anchor)) {
    return 'missing_anchor';
  }
  if (wp.visit_frequency === 'monthly' && parseVisitWeeks(wp.visit_weeks).length === 0) {
    return 'missing_weeks';
  }
  return 'ok';
}

/** 設定が足りないときの注意書き。ok は null。 */
export function frequencyWarningText(status: FrequencyStatus): string | null {
  switch (status) {
    case 'missing_anchor':
      return '起点の週が未設定のため、毎週の予定になります。起点の週を選んでください。';
    case 'missing_weeks':
      return '第何週が未設定のため、毎週の予定になります。訪問する週を選んでください。';
    default:
      return null;
  }
}

/** 一覧などで短く出す頻度の説明 (毎週・未設定は null)。 */
export function visitFrequencyShortLabel(
  wp: Pick<WeeklyPattern, 'visit_frequency' | 'visit_weeks'> | null | undefined,
): string | null {
  if (!wp) return null;
  if (wp.visit_frequency === 'biweekly') return '隔週';
  if (wp.visit_frequency === 'monthly') {
    const weeks = parseVisitWeeks(wp.visit_weeks);
    return weeks.length > 0 ? `第${weeks.join('・')}週のみ` : '月の第何週';
  }
  return null;
}

/** 週の予定でお休みの週の方に添える文言 (設計 §4)。 */
export function offWeekLabel(
  wp: Pick<WeeklyPattern, 'visit_frequency' | 'visit_weeks'> | null | undefined,
): string {
  if (wp?.visit_frequency === 'monthly') {
    const weeks = parseVisitWeeks(wp.visit_weeks);
    if (weeks.length > 0) return `第${weeks.join('・')}週のみ・お休みの週`;
  }
  if (wp?.visit_frequency === 'biweekly') return '隔週・お休みの週';
  return '頻度によるお休みの週';
}
