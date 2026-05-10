'use client';

import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';

/**
 * AssistiveTouch 風のドラッグ可能 FAB に必要な状態・ハンドラをまとめた hook.
 *
 * - 押下→ドラッグ→離す で位置移動。`dragThreshold` 以下の動きはタップ扱い。
 * - 位置は `localStorage[storageKey]` に永続化。
 * - ビューポート リサイズ時に画面外へはみ出さないよう自動 re-clamp。
 *
 * 呼び出し側は `position` で `style.left/top` を、`handlers` を `<button>` に
 * spread し、クリック時は `wasDragRef.current` を確認してドラッグ直後の click
 * 抑制を行う(PC 版 AiFab / モバイル版 MobileAiFab で共有)。
 */

export interface FabPosition {
  x: number;
  y: number;
}

interface UseDraggableFabOptions {
  /** FAB の幅・高さ (px)。正方形前提。 */
  fabSize: number;
  /** localStorage に位置を保存するキー。 */
  storageKey: string;
  /**
   * 初期位置を計算する関数。post-mount で 1 度だけ呼ばれる(`window` 参照可)。
   * `localStorage` に既存値があればそちらが優先され、本関数は使われない。
   */
  initialPosition: () => FabPosition;
  /** ビューポート端からの最低マージン (px)。default: 8 */
  screenMargin?: number;
  /** タップとドラッグを区別する移動量しきい値 (px)。default: 5 */
  dragThreshold?: number;
}

interface UseDraggableFabResult {
  /** 現在位置。post-mount まで `null`(SSR / 初回レンダリング のフリッカー回避用)。 */
  position: FabPosition | null;
  /** 現在ドラッグ中かどうか。スタイル(`cursor`, `opacity` 等)に使う。 */
  dragging: boolean;
  /**
   * 直前の press がドラッグだったかを示す ref。`onClick` 内で
   * `if (wasDragRef.current) return;` のようにクリック抑制に使う。
   */
  wasDragRef: MutableRefObject<boolean>;
  /** `<button>` に spread するハンドラ群。 */
  handlers: {
    onPointerDown: (e: React.PointerEvent<HTMLButtonElement>) => void;
    onPointerMove: (e: React.PointerEvent<HTMLButtonElement>) => void;
    onPointerUp: (e: React.PointerEvent<HTMLButtonElement>) => void;
  };
}

export function useDraggableFab(options: UseDraggableFabOptions): UseDraggableFabResult {
  const { fabSize, storageKey, initialPosition, screenMargin = 8, dragThreshold = 5 } = options;

  // initialPosition は意図的に ref に固定 — 1 回限り評価で再 mount を誘発しない。
  const initialPositionRef = useRef(initialPosition);
  initialPositionRef.current = initialPosition;

  const [position, setPosition] = useState<FabPosition | null>(null);
  const [dragging, setDragging] = useState(false);
  const dragStartRef = useRef<{
    pointerId: number;
    pointerX: number;
    pointerY: number;
    startX: number;
    startY: number;
  } | null>(null);
  const wasDragRef = useRef(false);

  const clampToViewport = useCallback(
    (p: FabPosition): FabPosition => {
      const maxX = Math.max(screenMargin, window.innerWidth - fabSize - screenMargin);
      const maxY = Math.max(screenMargin, window.innerHeight - fabSize - screenMargin);
      return {
        x: Math.min(Math.max(screenMargin, p.x), maxX),
        y: Math.min(Math.max(screenMargin, p.y), maxY),
      };
    },
    [fabSize, screenMargin],
  );

  const loadSavedPosition = useCallback((): FabPosition | null => {
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as unknown;
      if (
        parsed &&
        typeof parsed === 'object' &&
        typeof (parsed as FabPosition).x === 'number' &&
        typeof (parsed as FabPosition).y === 'number'
      ) {
        return parsed as FabPosition;
      }
      return null;
    } catch {
      return null;
    }
  }, [storageKey]);

  // post-mount 初期化(window が必要)。
  useEffect(() => {
    setPosition(clampToViewport(loadSavedPosition() ?? initialPositionRef.current()));
  }, [clampToViewport, loadSavedPosition]);

  // リサイズ時に画面外へ追い出されないよう re-clamp。
  useEffect(() => {
    const onResize = () => {
      setPosition((prev) => (prev ? clampToViewport(prev) : prev));
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [clampToViewport]);

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>) => {
      if (!position) return;
      e.currentTarget.setPointerCapture(e.pointerId);
      dragStartRef.current = {
        pointerId: e.pointerId,
        pointerX: e.clientX,
        pointerY: e.clientY,
        startX: position.x,
        startY: position.y,
      };
      wasDragRef.current = false;
    },
    [position],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>) => {
      const start = dragStartRef.current;
      if (!start || start.pointerId !== e.pointerId) return;
      const dx = e.clientX - start.pointerX;
      const dy = e.clientY - start.pointerY;
      if (!wasDragRef.current && Math.hypot(dx, dy) < dragThreshold) return;
      if (!wasDragRef.current) {
        wasDragRef.current = true;
        setDragging(true);
      }
      setPosition(clampToViewport({ x: start.startX + dx, y: start.startY + dy }));
    },
    [clampToViewport, dragThreshold],
  );

  const onPointerUp = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>) => {
      const start = dragStartRef.current;
      if (!start || start.pointerId !== e.pointerId) return;
      try {
        e.currentTarget.releasePointerCapture(e.pointerId);
      } catch {
        // already released
      }
      const wasDrag = wasDragRef.current;
      dragStartRef.current = null;
      setDragging(false);

      if (wasDrag) {
        const finalPos = clampToViewport({
          x: start.startX + (e.clientX - start.pointerX),
          y: start.startY + (e.clientY - start.pointerY),
        });
        setPosition(finalPos);
        try {
          window.localStorage.setItem(storageKey, JSON.stringify(finalPos));
        } catch {
          // quota / disabled — non-fatal
        }
        // 直後の click は抑制 → 次 tick で解除して通常タップを復帰。
        window.setTimeout(() => {
          wasDragRef.current = false;
        }, 0);
      }
    },
    [clampToViewport, storageKey],
  );

  return {
    position,
    dragging,
    wasDragRef,
    handlers: { onPointerDown, onPointerMove, onPointerUp },
  };
}
