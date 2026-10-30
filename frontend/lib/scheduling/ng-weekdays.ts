/**
 * NG 曜日 (訪問しない曜日) の画面用の小さな道具。
 *
 * 正典: docs/plans/biweekly-monthly-design-2026-10-30.md §9
 * (PO 決定 2026-10-30「NG 曜日＝画面に出して全経路で守る」)。
 * 守る判定そのものは backend (`app/services/scheduling/ng_weekdays.py`) が持つ。
 * ここは並べ替え・食い違いの文言だけ (PC の WeeklyPatternEditor とスマホのカルテで共用)。
 */
import { WEEKDAY_KEYS, WEEKDAY_LABELS_JA, type WeekdayKey } from '@/lib/schemas/patient';

/** 月→日の順にそろえ、重ねない。 */
export function sortWeekdays(days: readonly WeekdayKey[]): WeekdayKey[] {
  return WEEKDAY_KEYS.filter((d) => days.includes(d));
}

/**
 * NG 曜日の 1 つを切り替える。空になったら null (= キーを書いていない状態と同じ扱い)。
 */
export function toggleNgWeekday(
  current: readonly WeekdayKey[] | null | undefined,
  day: WeekdayKey,
): WeekdayKey[] | null {
  const now = current ?? [];
  const next = now.includes(day) ? now.filter((d) => d !== day) : [...now, day];
  return next.length === 0 ? null : sortWeekdays(next);
}

/** 希望曜日のうち NG 曜日にも入っている曜日 (月→日の順)。 */
export function ngWeekdayConflicts(
  preferred: readonly WeekdayKey[],
  ng: readonly WeekdayKey[] | null | undefined,
): WeekdayKey[] {
  if (!ng || ng.length === 0) return [];
  return WEEKDAY_KEYS.filter((d) => preferred.includes(d) && ng.includes(d));
}

/** 希望曜日と NG 曜日が重なったときのやわらかい注意書き。重なりが無ければ null。 */
export function ngWeekdayConflictText(conflicts: readonly WeekdayKey[]): string | null {
  if (conflicts.length === 0) return null;
  const days = conflicts.map((d) => WEEKDAY_LABELS_JA[d]).join('・');
  return `希望曜日の ${days} は NG 曜日にも入っています。NG 曜日が優先され、その曜日には予定を入れません。`;
}

/** カルテの表示用: "月・水" / 無ければ "なし"。 */
export function ngWeekdaysLabel(ng: readonly WeekdayKey[] | null | undefined): string {
  if (!ng || ng.length === 0) return 'なし';
  return sortWeekdays(ng)
    .map((d) => WEEKDAY_LABELS_JA[d])
    .join('・');
}
