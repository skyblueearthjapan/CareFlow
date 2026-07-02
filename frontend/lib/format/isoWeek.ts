/**
 * ISO-8601 週番号ユーティリティ (月曜始まり).
 *
 * Schedule Advisor (健康診断パネル) の「当週 vs 前週」比較で、前週の
 * (iso_year, iso_week) を **年跨ぎ・53 週年を含めて正しく** 算出するために使う.
 *
 * すべて UTC で計算し、内部でラウンドトリップの自己整合性を保つ.
 *
 * ## UTC 版 vs ローカル日付版
 * - `isoWeekFromDate`      — UTC 版。Date の UTC 年月日で計算。
 *                            `mondayOfIsoWeek` / `previousIsoWeek` との組み合わせに使う。
 * - `isoWeekFromLocalDate` — ローカル版。Date の **ローカル** 年月日で計算。
 *                            ブラウザで `toWeekStart()` 等から得たローカル日付を扱う
 *                            コンポーネント側コードに使う（旧 `toIsoYearWeek` と同一ロジック）。
 */

/**
 * 任意の Date から ISO 週 (isoYear, isoWeek) を **UTC** で算出する.
 *
 * UTC 日付ベース。`mondayOfIsoWeek` / `previousIsoWeek` のラウンドトリップ用。
 * ローカル日付ベースで計算したい場合は `isoWeekFromLocalDate` を使うこと。
 */
export function isoWeekFromDate(d: Date): { isoYear: number; isoWeek: number } {
  const date = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  // ISO では木曜日が属する年・週がその週の年・週になる. 月=1..日=7.
  const day = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const isoYear = date.getUTCFullYear();
  const yearStart = new Date(Date.UTC(isoYear, 0, 1));
  const isoWeek = Math.ceil(((date.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return { isoYear, isoWeek };
}

/**
 * 任意の Date から ISO 週 (isoYear, isoWeek) を **ローカル日付** で算出する.
 *
 * `d.getFullYear()` / `d.getMonth()` / `d.getDate()` を使うため、ブラウザで
 * `toWeekStart()` 等から得たローカル午前0時 Date をそのまま渡せる。
 * UTC 日付ベースで計算したい場合は `isoWeekFromDate` を使うこと。
 *
 * コードベース内の旧 `toIsoYearWeek` ローカル実装と完全同一のロジック。
 */
export function isoWeekFromLocalDate(d: Date): { isoYear: number; isoWeek: number } {
  const date = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const isoYear = date.getUTCFullYear();
  const yearStart = new Date(Date.UTC(isoYear, 0, 1));
  const isoWeek = Math.ceil(((date.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return { isoYear, isoWeek };
}

/** ISO 週 (isoYear, isoWeek) の月曜 00:00 UTC の Date を返す. */
export function mondayOfIsoWeek(isoYear: number, isoWeek: number): Date {
  // 1 月 4 日は必ず ISO week 1 に含まれる (ISO-8601 の定義).
  const jan4 = new Date(Date.UTC(isoYear, 0, 4));
  const jan4Day = jan4.getUTCDay() || 7; // 1..7
  const week1Monday = new Date(jan4);
  week1Monday.setUTCDate(jan4.getUTCDate() - (jan4Day - 1));
  const monday = new Date(week1Monday);
  monday.setUTCDate(week1Monday.getUTCDate() + (isoWeek - 1) * 7);
  return monday;
}

/**
 * 前週の (isoYear, isoWeek) を返す.
 *
 * 年跨ぎ (週1 → 前年最終週) と 53 週年 (例: 2020, 2015) を正しく処理する.
 * 例:
 *   - previousIsoWeek(2026, 27) → { isoYear: 2026, isoWeek: 26 }
 *   - previousIsoWeek(2026, 1)  → { isoYear: 2025, isoWeek: 52 }
 *   - previousIsoWeek(2021, 1)  → { isoYear: 2020, isoWeek: 53 }
 */
export function previousIsoWeek(
  isoYear: number,
  isoWeek: number,
): { isoYear: number; isoWeek: number } {
  const monday = mondayOfIsoWeek(isoYear, isoWeek);
  monday.setUTCDate(monday.getUTCDate() - 7);
  return isoWeekFromDate(monday);
}
