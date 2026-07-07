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
/** 30 分あたりの高さ (px)。モック v2 と一致。 */
export const TL_ROW_PX = 34;
/** 1 分あたりの高さ (px)。 */
export const TL_PX_PER_MIN = TL_ROW_PX / 30;

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
 * monitor の `assignVisitLanes` と同じ貪欲法 (start 昇順・最初に空くレーン)。
 * 縦タイムラインでは lane を「横方向の分割」に使う (重なり時のみ幅を分ける)。
 * 純関数。
 */
export function assignLanes(blocks: ReadonlyArray<TimelineBlock>): Map<string, LanePlacement> {
  const sorted = [...blocks].sort((a, b) => a.startMin - b.startMin || a.id.localeCompare(b.id));
  const laneEnds: number[] = [];
  const laneOf = new Map<string, number>();
  for (const b of sorted) {
    let lane = laneEnds.findIndex((end) => end <= b.startMin);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(b.endMin);
    } else {
      laneEnds[lane] = b.endMin;
    }
    laneOf.set(b.id, lane);
  }
  // 実際に重なりに関与したレーン数を全体で共有する (モック同様、単純化して全体最大)。
  const laneCount = Math.max(1, laneEnds.length);
  const out = new Map<string, LanePlacement>();
  for (const [id, lane] of laneOf) out.set(id, { lane, laneCount });
  return out;
}

/** 所要分の見た目下限 (px)。短時間訪問でも氏名が読める最小高さ。 */
export const TL_MIN_CARD_PX = 22;

/** カード内の情報量しきい (px): これ以上でサービス名・ピルを出す。 */
export const TL_SHOW_SVC_PX = 44;
export const TL_SHOW_PILLS_PX = 64;
