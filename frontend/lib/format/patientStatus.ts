/**
 * 患者ステータス表示まわりの日付整形（JST 固定）。
 *
 * 正典 = `docs/plans/patient-status-schedule-design-2026-09-09.md` §7-4
 * （患者一覧・詳細に `status_changed_at` を「入院中（9/8〜）」の形で出す）。
 *
 * 時刻はすべて **Asia/Tokyo** で解釈する。BE の過去日ガードと同じ基準にしないと、
 * 深夜や海外タイムゾーンの端末で「今日」が 1 日ズレて 422 になる（盤面の
 * `CourseDayTablePanel` / `SpecialVisitPlaceLauncher` と同じ式を使う）。
 */

/** JST の `YYYY-MM-DD`。`offsetDays` で「明日から」等を作る。 */
export function jstDateString(offsetDays = 0, now: Date = new Date()): string {
  const shifted = new Date(now.getTime() + offsetDays * 86400000);
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Tokyo' }).format(shifted);
}

/**
 * ISO 文字列 / `YYYY-MM-DD` を JST の `M/D` にする。解釈できない値は `null`。
 *
 * BE の `status_changed_at` は timestamp（tz つき / naive の両方があり得る）。
 * naive（`2026-09-08T10:00:00`）は JS が **ローカル時刻**として解釈するため、
 * ここで Asia/Tokyo に投げ直しても日本国内の端末では同じ日付になる。
 */
export function formatJstMonthDay(value: string | null | undefined): string | null {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo' }).format(d).split('-');
  if (parts.length !== 3) return null;
  return `${Number(parts[1])}/${Number(parts[2])}`;
}

/**
 * 「（9/8〜）」。`status_changed_at` が無い（旧データ / 旧 BE）なら空文字。
 * 呼び出し側は `{STATUS_LABEL[st]}{formatStatusSince(p.status_changed_at)}` で使う。
 */
export function formatStatusSince(value: string | null | undefined): string {
  const md = formatJstMonthDay(value);
  return md ? `（${md}〜）` : '';
}
