/**
 * 空き時間帯 (free gap) 共有ユーティリティ — CareFlow Phase G-55.
 *
 * モバイル現場ボード (`components/field/FieldBoard.tsx`) と
 * 親機スケジュール (`components/schedule/v2/CourseDayTable*` / `CourseWeekOverview`)
 * の両方から使う「空き時間帯」算出ロジックを 1 箇所に集約する。
 *
 * 意味論 (両機共通):
 *   - コースの営業枠 (AM 09:30–12:00 / PM 13:00–18:00) から、既存 visit が占有する
 *     [start_time, end_time) を除いた残り時間帯を「空き時間帯」とする。
 *   - 60 分 (= MIN_FREE_GAP_MIN) 未満の gap は表示対象外 (移動 + 約35分業務 +
 *     バッファーで概ね 1 時間が必要なため)。
 *   - 戻り値は開始時刻昇順。
 *
 * NOTE: ここで算出するのはあくまで **時間帯** の空き。「頭数(定員)の空き」
 *   (= remaining = capacity - filled) は呼び出し側でゲートする
 *   (remaining<=0 のときは時間 gap があっても空き表示しない)。両機ともこの
 *   ゲートを表示層 (FieldBoard / CourseDayTable) で行う。
 */

// ─────────────────────────────────────────────────────────────────────────
// 時間ユーティリティ
// ─────────────────────────────────────────────────────────────────────────

/** 'HH:MM' / 'HH:MM:SS' → 0 時起点の分。不正値は null。 */
export function parseHM(s: string | null | undefined): number | null {
  if (!s) return null;
  const m = /^(\d{1,2}):(\d{2})/.exec(s.trim());
  if (!m) return null;
  const h = Number(m[1]);
  const mm = Number(m[2]);
  if (h < 0 || h > 47 || mm < 0 || mm > 59) return null;
  return h * 60 + mm;
}

/** 0 時起点の分 → 'HH:MM'。 */
export function fmtHM(min: number): string {
  const h = Math.floor(min / 60);
  const m = min % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

// ─────────────────────────────────────────────────────────────────────────
// 営業枠 / 空き時間帯
// ─────────────────────────────────────────────────────────────────────────

/**
 * コースの営業枠 (フロント表示用の目安。バックエンド定数の複製)。
 * AM 09:30–12:00 / PM 13:00–18:00。昼休み 12:00–13:00 はブロック間で非営業。
 * 厳密な配置可否 (移動時間等) は提案フロー (propose-slots) が担保する。
 */
export const BUSINESS_BLOCKS: ReadonlyArray<readonly [number, number]> = [
  [9 * 60 + 30, 12 * 60], // 09:30–12:00
  [13 * 60, 18 * 60], // 13:00–18:00
];

/**
 * これより短い gap は空き帯として表示しない (分)。
 * 60分 = 移動 + 約35分業務 + バッファーで概ね 1 時間確保が必要なため。
 */
export const MIN_FREE_GAP_MIN = 60;

/** 営業枠から既存 visit の占有を除いた空き時間帯 (≥MIN_FREE_GAP_MIN)。 */
export interface FreeGap {
  /** gap 開始 (0 時起点の分。interleave の並べ替えキー)。 */
  startMin: number;
  /** gap 終了 (0 時起点の分)。 */
  endMin: number;
  /** 'HH:MM〜HH:MM' の表示ラベル。 */
  label: string;
}

/** computeFreeGaps が読む visit の最小形 (start_time / end_time を持てば何でも良い)。 */
export interface TimeOccupiedVisit {
  start_time: string | null | undefined;
  end_time: string | null | undefined;
}

/**
 * コースの営業枠から既存 visit の占有 [start, end) を除いた空き時間帯を算出する。
 * 戻り値は MIN_FREE_GAP_MIN 以上の gap のみ (短すぎる gap は省略)、start 昇順。
 */
export function computeFreeGaps(visits: ReadonlyArray<TimeOccupiedVisit>): FreeGap[] {
  const occupied: Array<[number, number]> = [];
  for (const v of visits) {
    const s = parseHM(v.start_time);
    const e = parseHM(v.end_time);
    if (s === null || e === null || e <= s) continue;
    occupied.push([s, e]);
  }
  occupied.sort((a, b) => a[0] - b[0]);

  const gaps: FreeGap[] = [];
  const push = (s: number, e: number) => {
    if (e - s >= MIN_FREE_GAP_MIN) {
      gaps.push({ startMin: s, endMin: e, label: `${fmtHM(s)}〜${fmtHM(e)}` });
    }
  };
  for (const [blockStart, blockEnd] of BUSINESS_BLOCKS) {
    let cursor = blockStart;
    for (const [s, e] of occupied) {
      if (e <= cursor || s >= blockEnd) continue; // ブロック外は無視
      const segStart = Math.max(s, blockStart);
      push(cursor, segStart);
      cursor = Math.max(cursor, Math.min(e, blockEnd));
    }
    push(cursor, blockEnd);
  }
  return gaps;
}
