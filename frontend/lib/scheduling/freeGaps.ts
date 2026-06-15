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

/**
 * 同住所 2 名ペアの最低占有 (分)。BE ``SAME_ADDRESS_PAIR_MIN_OCCUPANCY`` と同値の
 * 表示用複製。 同住所・同時刻の 2 名 (例: 安永/菅原 16:00) は 1 スタッフが連続して
 * 回るため、 各々の実サービス (例 35 分) ではなく **90 分** 占有とみなす。 これにより
 * 空き時間帯がペア占有 (16:00-17:30) を踏まず、 正しく占有後 (17:30-) から始まる。
 */
export const SAME_ADDRESS_PAIR_MIN_OCCUPANCY = 90;

/**
 * 営業時間設定 (Phase G-88) からフロント表示用の営業枠を組み立てる。
 *
 * 親機の最適化設定 (`/api/v1/scheduling-settings`) の `business_start` /
 * `business_end` を反映するための導線。従来の表示と同じく昼休み帯 (12:00–13:00)
 * は枠の間で非営業として扱う (午前/午後の境界 = 正午は内部固定概念のため踏襲)。
 *
 *   - business 範囲が昼休み帯を内包 → [start,12:00] と [13:00,end] の 2 枠
 *   - 昼休み帯と重ならない → 単一の [start,end] 枠
 *
 * 入力が不正 (start>=end 等) のときは null を返し、呼び出し側は既定
 * `BUSINESS_BLOCKS` にフォールバックする (取得前 / 失敗時の後方互換)。
 */
export function businessBlocksFromHours(
  businessStart: string | null | undefined,
  businessEnd: string | null | undefined,
): ReadonlyArray<readonly [number, number]> | null {
  const start = parseHM(businessStart);
  const end = parseHM(businessEnd);
  if (start === null || end === null || start >= end) return null;

  const LUNCH_START = 12 * 60; // 12:00
  const LUNCH_END = 13 * 60; // 13:00

  // 昼休み帯と重ならない (営業が昼前で終わる / 昼後に始まる) → 単一枠。
  if (end <= LUNCH_START || start >= LUNCH_END) {
    return [[start, end]];
  }

  const blocks: Array<readonly [number, number]> = [];
  const am: readonly [number, number] = [start, Math.min(end, LUNCH_START)];
  if (am[1] > am[0]) blocks.push(am);
  const pm: readonly [number, number] = [Math.max(start, LUNCH_END), end];
  if (pm[1] > pm[0]) blocks.push(pm);
  return blocks.length > 0 ? blocks : null;
}

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
  /**
   * 同住所バケットキー (`buildSameAddressKey(lat, lng)` 由来)。指定すると、 同キー・
   * 同 start_time が 2 件以上 (= 同住所 2 名ペア) のとき占有を 90 分に底上げして空き
   * 時間帯を算出する。 未指定 (旧呼び出し) は従来どおり生の [start, end) を使う。
   */
  same_address_key?: string | null;
}

/**
 * コースの営業枠から既存 visit の占有 [start, end) を除いた空き時間帯を算出する。
 * 戻り値は MIN_FREE_GAP_MIN 以上の gap のみ (短すぎる gap は省略)、start 昇順。
 *
 * @param businessBlocks 営業枠 (Phase G-88: 設定値から `businessBlocksFromHours`
 *   で組み立てた枠を渡せる)。省略時は既定 `BUSINESS_BLOCKS` (09:30–12:00/13:00–18:00)。
 */
export function computeFreeGaps(
  visits: ReadonlyArray<TimeOccupiedVisit>,
  businessBlocks: ReadonlyArray<readonly [number, number]> = BUSINESS_BLOCKS,
): FreeGap[] {
  // 有効な occupancy を抽出 (同住所キー付き).
  const parsed = visits
    .map((v) => ({
      s: parseHM(v.start_time),
      e: parseHM(v.end_time),
      key: v.same_address_key ?? null,
    }))
    .filter((v): v is { s: number; e: number; key: string | null } => {
      return v.s !== null && v.e !== null && v.e > v.s;
    });

  // 同住所 2 名ペア (同 same_address_key・同 start が 2 件以上) は占有を 90 分に底上げ
  // (BE SAME_ADDRESS_PAIR_MIN_OCCUPANCY と同規約). 単独同住所は延長しない.
  const occupied: Array<[number, number]> = parsed.map((v) => {
    let endEff = v.e;
    if (v.key) {
      const mates = parsed.filter((o) => o.key === v.key && o.s === v.s);
      if (mates.length >= 2) {
        endEff = Math.max(v.s + SAME_ADDRESS_PAIR_MIN_OCCUPANCY, ...mates.map((m) => m.e));
      }
    }
    return [v.s, endEff];
  });
  occupied.sort((a, b) => a[0] - b[0]);

  const gaps: FreeGap[] = [];
  const push = (s: number, e: number) => {
    if (e - s >= MIN_FREE_GAP_MIN) {
      gaps.push({ startMin: s, endMin: e, label: `${fmtHM(s)}〜${fmtHM(e)}` });
    }
  };
  for (const [blockStart, blockEnd] of businessBlocks) {
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
