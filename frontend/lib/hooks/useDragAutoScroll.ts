'use client';
/**
 * ドラッグ中のエッジ自動スクロール (PO 報告 2026-09-02: 職員スケジュールで
 * ドラッグ中に上スクロールが効かず、画面外の行へ配置できない)。
 *
 * ブラウザ標準の HTML5 DnD は入れ子のスクロールコンテナを自動スクロールしない
 * (ウィンドウすら上方向は不安定)。らく助は PC で「ページ固定・ペイン内部スクロール」
 * 設計のため、これが全ドラッグ面 (職員スケジュール/盤面/タイムライン/プール/運転席) に効く。
 *
 * 仕組み: document の dragover (capture) でポインタ位置を追跡し、rAF ループで
 * ポインタ直下のスクロール可能な祖先を内側→外側に走査。コンテナの可視領域の端
 * `EDGE`px 圏内なら近いほど速くスクロールする (縦横は独立に判定)。どのコンテナも
 * 動かせない軸はウィンドウへフォールバック。ドラッグ終了 (drop/dragend) と
 * dragover 途絶 (ウィンドウ外へ出た) で停止する。
 */
import { useEffect } from 'react';

/** コンテナ端からこの距離 (px) 以内で自動スクロールが始まる。 */
export const EDGE_PX = 48;
/** 端に完全に接した時の速度 (px/フレーム ≒ px/16ms)。 */
export const MAX_SPEED_PX = 24;

/**
 * 端への近接からスクロール量を計算する純関数。
 * 戻り値: 負 = start 側 (上/左) へ、正 = end 側 (下/右) へ、0 = 圏外。
 */
export function edgeDelta(
  pos: number,
  start: number,
  end: number,
  edge: number = EDGE_PX,
  maxSpeed: number = MAX_SPEED_PX,
): number {
  if (end - start <= edge * 2) return 0; // 小さすぎるコンテナは対象外 (両端が重なる)
  const fromStart = pos - start;
  const fromEnd = end - pos;
  if (fromStart >= 0 && fromStart < edge) {
    return -Math.ceil(((edge - fromStart) / edge) * maxSpeed);
  }
  if (fromEnd >= 0 && fromEnd < edge) {
    return Math.ceil(((edge - fromEnd) / edge) * maxSpeed);
  }
  return 0;
}

type Axis = 'x' | 'y';

function isScrollable(el: Element, axis: Axis): boolean {
  const cs = window.getComputedStyle(el);
  const ov = axis === 'y' ? cs.overflowY : cs.overflowX;
  if (ov !== 'auto' && ov !== 'scroll') return false;
  return axis === 'y'
    ? el.scrollHeight > el.clientHeight + 1
    : el.scrollWidth > el.clientWidth + 1;
}

function canScrollBy(el: Element, axis: Axis, delta: number): boolean {
  if (axis === 'y') {
    return delta < 0
      ? el.scrollTop > 0
      : el.scrollTop < el.scrollHeight - el.clientHeight - 1;
  }
  return delta < 0
    ? el.scrollLeft > 0
    : el.scrollLeft < el.scrollWidth - el.clientWidth - 1;
}

/** 1 フレーム分のスクロール処理 (テスト容易性のため hook の外に切り出し)。 */
export function scrollStep(x: number, y: number): void {
  const start = document.elementFromPoint(x, y);
  let doneX = false;
  let doneY = false;
  for (
    let node: Element | null = start;
    node && node !== document.documentElement && (!doneX || !doneY);
    node = node.parentElement
  ) {
    const r = node.getBoundingClientRect();
    // コンテナの「見えている範囲」(ビューポートでクリップ) の端で判定する。
    const top = Math.max(r.top, 0);
    const bottom = Math.min(r.bottom, window.innerHeight);
    const left = Math.max(r.left, 0);
    const right = Math.min(r.right, window.innerWidth);
    if (!doneY && isScrollable(node, 'y')) {
      const dy = edgeDelta(y, top, bottom);
      if (dy !== 0 && canScrollBy(node, 'y', dy)) {
        node.scrollTop += dy;
        doneY = true;
      }
    }
    if (!doneX && isScrollable(node, 'x')) {
      const dx = edgeDelta(x, left, right);
      if (dx !== 0 && canScrollBy(node, 'x', dx)) {
        node.scrollLeft += dx;
        doneX = true;
      }
    }
  }
  // どのコンテナも受け持たなかった軸はウィンドウへ (モバイル等ページスクロールの画面)。
  if (!doneY || !doneX) {
    const dy = doneY ? 0 : edgeDelta(y, 0, window.innerHeight);
    const dx = doneX ? 0 : edgeDelta(x, 0, window.innerWidth);
    if (dy !== 0 || dx !== 0) window.scrollBy(dx, dy);
  }
}

/** dragover が途絶してから停止するまでの猶予 (ms)。ウィンドウ外へ出た検知用。 */
const IDLE_STOP_MS = 300;

/**
 * ドラッグ中のエッジ自動スクロールを有効化する。ページ (またはレイアウト) で
 * 1 回呼べば、その配下のすべての HTML5 DnD に効く。
 */
export function useDragAutoScroll(enabled: boolean = true): void {
  useEffect(() => {
    if (!enabled || typeof window === 'undefined') return undefined;
    let x = 0;
    let y = 0;
    let raf = 0;
    let active = false;
    let lastOver = 0;

    const loop = () => {
      if (!active) return;
      scrollStep(x, y);
      raf = window.requestAnimationFrame(loop);
    };
    const stop = () => {
      active = false;
      if (raf) window.cancelAnimationFrame(raf);
      raf = 0;
    };
    const onDragOver = (e: DragEvent) => {
      x = e.clientX;
      y = e.clientY;
      lastOver = Date.now();
      if (!active) {
        active = true;
        raf = window.requestAnimationFrame(loop);
      }
    };
    // dragover はウィンドウ外に出ると発火しなくなる → 途絶で停止する番犬。
    const watchdog = window.setInterval(() => {
      if (active && Date.now() - lastOver > IDLE_STOP_MS) stop();
    }, 150);

    document.addEventListener('dragover', onDragOver, true);
    document.addEventListener('drop', stop, true);
    document.addEventListener('dragend', stop, true);
    return () => {
      stop();
      window.clearInterval(watchdog);
      document.removeEventListener('dragover', onDragOver, true);
      document.removeEventListener('drop', stop, true);
      document.removeEventListener('dragend', stop, true);
    };
  }, [enabled]);
}
