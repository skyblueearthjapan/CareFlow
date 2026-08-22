'use client';

/**
 * SyncedHScroll — 横スクロールバーを「上」にも出すラッパー。
 *
 * 幅の広い盤面(職員スケジュール/タイムライン)はブラウザ既定だと横スクロールバーが
 * 一番下にしか出ず、縦に最後までスクロールしないと辿り着けない(PO 指摘 2026-08-22)。
 * 本体と同じ scrollWidth を持つ薄いトラックを先頭に置き、scrollLeft を双方向で同期する。
 * 上のトラックは sticky にして縦スクロール中も見えるようにする。
 */
import * as React from 'react';

import { cn } from '@/lib/utils';

export interface SyncedHScrollProps {
  children: React.ReactNode;
  /** 本体(横スクロールする側)の className。`overflow-x-auto` は内部で付与する。 */
  className?: string;
  /** 上トラックの追加 className。 */
  topClassName?: string;
  /** 上トラックを sticky にする(既定 true)。`top` の位置は topOffsetClassName で。 */
  sticky?: boolean;
  /** 例: 'top-0' / 'top-12'。sticky のときのみ有効。 */
  topOffsetClassName?: string;
  'data-testid'?: string;
}

export function SyncedHScroll({
  children,
  className,
  topClassName,
  sticky = true,
  topOffsetClassName = 'top-0',
  'data-testid': testId,
}: SyncedHScrollProps) {
  const topRef = React.useRef<HTMLDivElement>(null);
  const bodyRef = React.useRef<HTMLDivElement>(null);
  const [scrollWidth, setScrollWidth] = React.useState(0);
  const [clientWidth, setClientWidth] = React.useState(0);
  // 同期中の再入防止(scroll イベントの往復ループを避ける)。
  const syncing = React.useRef(false);

  const measure = React.useCallback(() => {
    const body = bodyRef.current;
    if (!body) return;
    setScrollWidth(body.scrollWidth);
    setClientWidth(body.clientWidth);
  }, []);

  React.useEffect(() => {
    measure();
    const body = bodyRef.current;
    if (!body || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(body);
    // 中身(テーブル等)の幅変化も拾う。
    Array.from(body.children).forEach((c) => ro.observe(c));
    return () => ro.disconnect();
  }, [measure, children]);

  const sync = React.useCallback((from: HTMLDivElement | null, to: HTMLDivElement | null) => {
    if (!from || !to || syncing.current) return;
    syncing.current = true;
    to.scrollLeft = from.scrollLeft;
    // 次フレームで解除(同期先の scroll イベントが来てから)。
    requestAnimationFrame(() => {
      syncing.current = false;
    });
  }, []);

  const needsScroll = scrollWidth > clientWidth + 1;

  return (
    <div data-testid={testId}>
      <div
        ref={topRef}
        aria-hidden="true"
        data-testid={testId ? `${testId}-top-scrollbar` : undefined}
        className={cn(
          'overflow-x-auto overflow-y-hidden bg-bg-base',
          sticky && `sticky ${topOffsetClassName} z-[2]`,
          needsScroll ? 'h-3' : 'h-0',
          topClassName,
        )}
        onScroll={() => sync(topRef.current, bodyRef.current)}
      >
        <div style={{ width: scrollWidth, height: 1 }} />
      </div>
      <div
        ref={bodyRef}
        className={cn('overflow-x-auto', className)}
        onScroll={() => sync(bodyRef.current, topRef.current)}
      >
        {children}
      </div>
    </div>
  );
}
