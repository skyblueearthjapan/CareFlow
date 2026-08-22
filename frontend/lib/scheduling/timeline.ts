/**
 * 縦タイムライン共有ロジック — スケジュール画面刷新 (T-1)。
 *
 * docs/plans/schedule-timeline-redesign-design.md / docs/mockups/timeline-mock.html。
 * 訪問モニター (`components/monitor/constants.ts`) の比例算法を **縦向きに転置** し、
 * 空き枠は既存 `lib/scheduling/freeGaps.ts` をそのまま使う (再発明しない)。
 *
 * 純関数のみ (副作用なし・単体テスト可能)。描画は timeline コンポーネントが行う。
 */

import { parseHM } from '@/lib/scheduling/freeGaps';

// ─────────────────────────────────────────────────────────────────────────
// 時間軸 (PO決定: 9:00〜18:00・30分格子)
// ─────────────────────────────────────────────────────────────────────────

/** タイムライン開始 (0 時起点の分)。9:00。 */
export const TL_DAY_START_MIN = 9 * 60;
/** タイムライン終了 (0 時起点の分)。18:00。 */
export const TL_DAY_END_MIN = 18 * 60;
/**
 * 30 分あたりの高さ (px)。日タイムライン。縦をたっぷり使い、カードを大きくして
 * 患者名がフル表示できるように余裕を持たせる (2026-07-07 現場要望で 34→52 に拡大)。
 * 9:00〜18:00 = 18行 → 全高 936px。
 */
export const TL_ROW_PX = 52;
/** 1 分あたりの高さ (px)。 */
export const TL_PX_PER_MIN = TL_ROW_PX / 30;
/**
 * 週タイムラインの 30 分あたり高さ (px)。6 曜日を横に並べるが縦は圧縮せず、
 * 日タイムラインと同じ行高で画面の高さをいっぱいに使う (2026-07-07 現場要望で
 * 44→52 に拡大し日と統一)。9:00〜18:00 = 18行 → 全高 936px。
 */
export const TL_WEEK_ROW_PX = 52;

/** 全高 (px)。時間軸レール・列の height に使う。 */
export function timelineHeightPx(
  startMin: number = TL_DAY_START_MIN,
  endMin: number = TL_DAY_END_MIN,
): number {
  return ((endMin - startMin) / 30) * TL_ROW_PX;
}

/** 0 時起点の分 → タイムライン上端からの Y 座標 (px)。範囲外もクランプせず返す。 */
export function minutesToY(min: number, startMin: number = TL_DAY_START_MIN): number {
  return ((min - startMin) / 30) * TL_ROW_PX;
}

/** 所要分 → カード高さ (px)。 */
export function durationToHeight(durationMin: number): number {
  return (durationMin / 30) * TL_ROW_PX;
}

/**
 * T-2 ②-b: ドロップ位置 (列上端からの px オフセット) → 15分スナップの開始分。
 * 連続時間軸のスナップ (テーブルの15分固定行の代替)。クランプはしない (呼び出し側で
 * 範囲チェックして警告する)。
 */
export function snapYOffsetToMinutes(
  offsetPx: number,
  snapMin = 15,
  startMin: number = TL_DAY_START_MIN,
): number {
  const raw = startMin + offsetPx / TL_PX_PER_MIN;
  return Math.round(raw / snapMin) * snapMin;
}

/** 'HH:MM' / 'HH:MM:SS' → Y 座標 (px)。不正値は null。 */
export function timeToY(
  hm: string | null | undefined,
  startMin: number = TL_DAY_START_MIN,
): number | null {
  const m = parseHM(hm);
  return m === null ? null : minutesToY(m, startMin);
}

// ─────────────────────────────────────────────────────────────────────────
// 性別パレット (患者カード地色 / スタッフアバター縁色)
// ─────────────────────────────────────────────────────────────────────────

export type GenderKey = 'm' | 'f' | 'n';

/** patient.sex / staff.sex ('male'/'female'/'unknown'/null) → パレットキー。 */
export function genderKey(sex: string | null | undefined): GenderKey {
  if (sex === 'male') return 'm';
  if (sex === 'female') return 'f';
  return 'n'; // unknown / null / 未設定 → 中立 (砂色)
}

/** タイムラインの性別トークン (tokens.css の CSS 変数を参照)。 */
export interface GenderPalette {
  bg: string;
  bar: string;
  ink: string;
  ln: string;
}

/**
 * 性別 → CSS変数トークン。値は inline style で使う (var() は Tailwind purge を
 * 受けないため timeline カードは inline style で色付けする。monitor と同方針)。
 * 実体トークンは tokens.css に `--sched-gender-*` として定義する。
 */
export const GENDER_PALETTE: Record<GenderKey, GenderPalette> = {
  m: {
    bg: 'var(--sched-male-bg)',
    bar: 'var(--sched-male-bar)',
    ink: 'var(--sched-male-ink)',
    ln: 'var(--sched-male-ln)',
  },
  f: {
    bg: 'var(--sched-female-bg)',
    bar: 'var(--sched-female-bar)',
    ink: 'var(--sched-female-ink)',
    ln: 'var(--sched-female-ln)',
  },
  n: {
    bg: 'var(--sched-neutral-bg)',
    bar: 'var(--sched-neutral-bar)',
    ink: 'var(--sched-neutral-ink)',
    ln: 'var(--sched-neutral-ln)',
  },
};

export function genderPalette(sex: string | null | undefined): GenderPalette {
  return GENDER_PALETTE[genderKey(sex)];
}

// ─────────────────────────────────────────────────────────────────────────
// レーン分割 (同一列で時間帯が重なる訪問を左右に振り分ける)
// ─────────────────────────────────────────────────────────────────────────

export interface TimelineBlock {
  id: string;
  startMin: number;
  endMin: number;
}

export interface LanePlacement {
  lane: number;
  laneCount: number;
}

/**
 * 同一列 (コース) 内で時間帯が重なる訪問を貪欲にレーンへ振り分ける。
 * start 昇順・最初に空くレーンへ割当。
 *
 * **重要 (2026-07-07 修正)**: laneCount は「連続して重なる塊 (クラスタ)」ごとに
 * 数える。同住所2名ペア等で一部が重なっても、**その後に続く重ならない単体の訪問は
 * laneCount=1 (全幅)** になる。旧実装は列全体の最大レーン数を全員へ一律適用していたため、
 * 1組でも重なりがあると列の全カードが半分幅になっていた (現場報告のバグ)。
 * 純関数。
 */
export function assignLanes(blocks: ReadonlyArray<TimelineBlock>): Map<string, LanePlacement> {
  const sorted = [...blocks].sort((a, b) => a.startMin - b.startMin || a.id.localeCompare(b.id));
  const out = new Map<string, LanePlacement>();

  // 現在のクラスタ (時間帯が数珠つなぎに重なる塊) を貯め、途切れたら確定する。
  let cluster: Array<{ id: string; lane: number }> = [];
  let laneEnds: number[] = [];
  let clusterMaxEnd = -Infinity;

  const flush = () => {
    const laneCount = Math.max(1, laneEnds.length);
    for (const c of cluster) out.set(c.id, { lane: c.lane, laneCount });
    cluster = [];
    laneEnds = [];
    clusterMaxEnd = -Infinity;
  };

  for (const b of sorted) {
    // 直前のクラスタの終端に達していれば (重ならない) クラスタを確定して切る。
    if (cluster.length > 0 && b.startMin >= clusterMaxEnd) flush();
    let lane = laneEnds.findIndex((end) => end <= b.startMin);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(b.endMin);
    } else {
      laneEnds[lane] = b.endMin;
    }
    cluster.push({ id: b.id, lane });
    clusterMaxEnd = Math.max(clusterMaxEnd, b.endMin);
  }
  flush();
  return out;
}

/** スケール指定の Y 座標 (px)。週タイムライン (rowPx = TL_WEEK_ROW_PX) 用。 */
export function minutesToYScaled(
  min: number,
  rowPx: number,
  startMin: number = TL_DAY_START_MIN,
): number {
  return ((min - startMin) / 30) * rowPx;
}

/** スケール指定のカード高さ (px)。 */
export function durationToHeightScaled(durationMin: number, rowPx: number): number {
  return (durationMin / 30) * rowPx;
}

/** 所要分の見た目下限 (px)。短時間訪問でも氏名が1行フルで読める最小高さ。 */
export const TL_MIN_CARD_PX = 30;

/** カード高がこれ以上のとき住所行 (📍) を出す (30分カード=49px から表示・PO要望)。 */
export const TL_SHOW_ADDR_PX = 46;

/** カード内の情報量しきい (px): これ以上で 時刻・サービス行 / ピル行 を出す。 */
export const TL_SHOW_SVC_PX = 46;
export const TL_SHOW_PILLS_PX = 74;

// ─────────────────────────────────────────────────────────────────────────
// 「今週の運転席」横タイムライン (week-cockpit-design.md §3 StaffTimelineView)
// ─────────────────────────────────────────────────────────────────────────

/**
 * 運転席タイムラインの時間軸 (0 時起点の分)。8:00〜19:00。
 * 縦タイムライン (9:00〜18:00) より広いのは、朝会 (8:30) と遅番の戻り (18:30) を
 * 盤面に載せるため (モック `staff-schedule-week-cockpit-mock.html` の H0/H1)。
 */
export const TL_COCKPIT_START_MIN = 8 * 60;
export const TL_COCKPIT_END_MIN = 19 * 60;
