'use client';

/**
 * 右に寄せた欄（らく助エージェントの右の列・浮いている小窓）の幅を、左の端の境目をつかんで変える。
 * 境目は role="separator"（aria-valuenow = いまの幅）。ドラッグ・←→（16px ずつ・Shift で 64px）・
 * ダブルクリックで既定の幅。決めた幅は localStorage に覚える（読めない・書けないときも動く）。
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export const STEP_PX = 16;
export const BIG_STEP_PX = 64;

export interface ResizableOptions {
  storageKey: string;
  defaultWidth: number;
  min?: number;
  /** 画面の幅 → いちばん広い幅（既定は画面の 50%）。 */
  max?: (viewportWidth: number) => number;
}

const half = (vw: number) => vw * 0.5;

export function clampWidth(w: number, min: number, max: number): number {
  return Math.round(Math.min(Math.max(w, min), Math.max(min, max)));
}

function viewport(): number {
  return typeof window === 'undefined' ? 1600 : window.innerWidth;
}

function load(key: string): number | null {
  try {
    const raw = window.localStorage.getItem(key);
    const n = raw == null ? NaN : Number(raw);
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}

function save(key: string, w: number | null) {
  try {
    if (w == null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, String(Math.round(w)));
  } catch {
    /* 覚えられなくても動く */
  }
}

export function useResizableWidth({ storageKey, defaultWidth, min = 300, max = half }: ResizableOptions) {
  const maxRef = useRef(max);
  maxRef.current = max;
  const limit = useCallback(
    (w: number) => clampWidth(w, min, maxRef.current(viewport())),
    [min],
  );
  const [width, setWidth] = useState(() => clampWidth(defaultWidth, min, max(viewport())));
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ x: number; w: number } | null>(null);
  const widthRef = useRef(width);
  widthRef.current = width;

  useEffect(() => {
    const stored = load(storageKey);
    setWidth(limit(stored ?? defaultWidth));
  }, [storageKey, defaultWidth, limit]);

  // 画面の幅が変わったら、はみ出さないように縮める（覚えた幅は変えない）
  useEffect(() => {
    const onResize = () => setWidth((w) => limit(load(storageKey) ?? w));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [limit, storageKey]);

  const commit = useCallback(
    (w: number) => {
      const v = limit(w);
      setWidth(v);
      save(storageKey, v);
    },
    [limit, storageKey],
  );

  const handleProps = {
    role: 'separator' as const,
    tabIndex: 0,
    'aria-orientation': 'vertical' as const,
    'aria-valuenow': width,
    'aria-valuemin': min,
    'aria-valuemax': Math.round(Math.max(min, max(viewport()))),
    'data-dragging': dragging ? 'true' : 'false',
    title: '左右にドラッグで幅を変えられます（ダブルクリックで元の幅）',
    onPointerDown: (e: React.PointerEvent<HTMLElement>) => {
      if (e.button !== 0) return;
      e.preventDefault();
      drag.current = { x: e.clientX, w: widthRef.current };
      setDragging(true);
      e.currentTarget.setPointerCapture?.(e.pointerId);
    },
    onPointerMove: (e: React.PointerEvent<HTMLElement>) => {
      const d = drag.current;
      if (!d) return;
      // 境目は欄の左の端: 左へ動かすほど欄が広くなる
      setWidth(limit(d.w + (d.x - e.clientX)));
    },
    onPointerUp: (e: React.PointerEvent<HTMLElement>) => {
      if (!drag.current) return;
      drag.current = null;
      setDragging(false);
      e.currentTarget.releasePointerCapture?.(e.pointerId);
      save(storageKey, widthRef.current);
    },
    onDoubleClick: () => {
      setWidth(limit(defaultWidth));
      save(storageKey, null);
    },
    onKeyDown: (e: React.KeyboardEvent<HTMLElement>) => {
      const step = e.shiftKey ? BIG_STEP_PX : STEP_PX;
      if (e.key === 'ArrowLeft') commit(widthRef.current + step);
      else if (e.key === 'ArrowRight') commit(widthRef.current - step);
      else if (e.key === 'Home') commit(min);
      else if (e.key === 'End') commit(maxRef.current(viewport()));
      else return;
      e.preventDefault();
    },
  };

  return { width, dragging, handleProps };
}
