'use client';

/**
 * SyncedHScroll — 横スクロールバーを「上」にも出すラッパー。
 *
 * 幅の広い盤面(職員スケジュール/タイムライン)はブラウザ既定だと横スクロールバーが
 * 一番下にしか出ず、縦に最後までスクロールしないと辿り着けない(PO 指摘 2026-08-22)。
 * 本体と同じ scrollWidth を持つ薄いトラックを先頭に置き、scrollLeft を双方向で同期する。
 * 上のトラックは sticky にして縦スクロール中も見えるようにする。
 *
 * 2026-08-23 (PO 決定・案A): 横スクロールバーは **上の1本に統一**。下のブラウザ既定バーは
 * 非表示にし(横スワイプ/Shift+ホイールは従来どおり効く)、代わりに盤面の空いている部分を
 * 掴んでドラッグすると横に動く(パン)。初回だけ小さなヒントを出す。
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
  /** 下のブラウザ既定バーを隠し、上の1本に統一する(既定 true)。 */
  singleBar?: boolean;
  /** 空き部分のドラッグで横に動かす(既定 true)。ボタン/入力/draggable 要素の上では無効。 */
  dragToPan?: boolean;
  /** 初回ヒントの localStorage キー。null でヒント無し。 */
  hintKey?: string | null;
}

/** パン対象外 = 操作要素やドラッグ要素(およびその子孫)。 */
const NO_PAN_SELECTOR =
  'button, a, input, select, textarea, [role="button"], [draggable="true"], [data-no-pan], [contenteditable="true"]';
const PAN_THRESHOLD_PX = 4;

export function SyncedHScroll({
  children,
  className,
  topClassName,
  sticky = true,
  topOffsetClassName = 'top-0',
  'data-testid': testId,
  singleBar = true,
  dragToPan = true,
  hintKey = 'carelink-hscroll-hint-v1',
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

  // ─── 初回ヒント ───
  const [hint, setHint] = React.useState(false);
  React.useEffect(() => {
    if (!hintKey || !needsScroll) return;
    try {
      if (window.localStorage.getItem(hintKey)) return;
    } catch {
      return;
    }
    setHint(true);
  }, [hintKey, needsScroll]);
  const dismissHint = React.useCallback(() => {
    setHint(false);
    if (!hintKey) return;
    try {
      window.localStorage.setItem(hintKey, '1');
    } catch {
      /* 保存できなくても動作には影響しない */
    }
  }, [hintKey]);

  // ─── 空き部分のドラッグでパン ───
  const pan = React.useRef<{ x: number; left: number; moved: boolean } | null>(null);
  const [panning, setPanning] = React.useState(false);
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragToPan || e.button !== 0) return;
    if ((e.target as HTMLElement).closest(NO_PAN_SELECTOR)) return;
    const body = bodyRef.current;
    if (!body || !needsScroll) return;
    pan.current = { x: e.clientX, left: body.scrollLeft, moved: false };
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const p = pan.current;
    const body = bodyRef.current;
    if (!p || !body) return;
    const dx = e.clientX - p.x;
    if (!p.moved && Math.abs(dx) < PAN_THRESHOLD_PX) return;
    if (!p.moved) {
      p.moved = true;
      setPanning(true);
      dismissHint();
      try {
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      } catch {
        /* jsdom など未実装 */
      }
    }
    body.scrollLeft = p.left - dx;
  };
  const endPan = () => {
    if (pan.current?.moved) {
      // 直後の click を 1 回だけ握り潰す(パンで離した位置のボタンを誤発火させない)。
      suppressClick.current = true;
    }
    pan.current = null;
    setPanning(false);
  };
  const suppressClick = React.useRef(false);
  const onClickCapture = (e: React.MouseEvent) => {
    if (suppressClick.current) {
      suppressClick.current = false;
      e.stopPropagation();
      e.preventDefault();
    }
  };

  return (
    <div data-testid={testId}>
      <div
        ref={topRef}
        aria-hidden="true"
        data-testid={testId ? `${testId}-top-scrollbar` : undefined}
        className={cn(
          'overflow-x-auto overflow-y-hidden bg-bg-base',
          sticky && `sticky ${topOffsetClassName} z-[2]`,
          needsScroll ? 'h-3.5' : 'h-0',
          topClassName,
        )}
        onScroll={() => {
          sync(topRef.current, bodyRef.current);
          if (hint) dismissHint();
        }}
      >
        <div style={{ width: scrollWidth, height: 1 }} />
      </div>
      {hint && needsScroll ? (
        <div
          role="status"
          data-testid={testId ? `${testId}-hint` : undefined}
          className="flex items-center gap-2 border-b border-border-subtle bg-brand-primary-50 px-3 py-1 text-[12px] text-text-secondary"
        >
          <span>
            横に動かすには <b>上のバー</b>を動かすか、<b>空いている部分をドラッグ</b>してください
            （Shift＋ホイールでも可）
          </span>
          <button
            type="button"
            className="ml-auto rounded px-1.5 text-text-muted hover:bg-bg-muted"
            aria-label="ヒントを閉じる"
            onClick={dismissHint}
          >
            ×
          </button>
        </div>
      ) : null}
      <div
        ref={bodyRef}
        className={cn(
          'overflow-x-auto',
          singleBar && 'scrollbar-none',
          dragToPan && needsScroll && (panning ? 'cursor-grabbing select-none' : 'cursor-grab'),
          className,
        )}
        onScroll={() => sync(bodyRef.current, topRef.current)}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endPan}
        onPointerCancel={endPan}
        onPointerLeave={() => {
          if (pan.current && !pan.current.moved) pan.current = null;
        }}
        onClickCapture={onClickCapture}
      >
        {children}
      </div>
    </div>
  );
}
