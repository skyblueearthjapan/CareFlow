'use client';

/**
 * 時刻のホイール — 1 分刻みの時刻の列を指で回して選ぶ (設計 2026-09-30 決定 #4)。
 *
 * 見た目と操作は `docs/mockups/mobile-actual-time-fix-mock.html` の `.wheel`:
 *   - 1 行 = 1 分・高さ 44px。10 分ごとの時刻が太字の目盛り。
 *   - 中央の帯が「いま選んでいる時刻」。上下は薄くして回すものだと分かるようにする。
 *   - CSS scroll-snap で 1 行ずつ止まる。列は `min`〜`max` しか無いので、範囲の
 *     端より先へは回らない。
 *   - `value` を外から変える (ひと押しチップ・「1 分 まえ」など) と、その位置へ回る。
 *
 * 値は「その日の 0 時からの分」。表示は "HH:MM"。
 */
import { useEffect, useLayoutEffect, useRef } from 'react';

import { minutesToHm } from '@/lib/format/actualTime';
import { cn } from '@/lib/utils';

/** 1 行の高さ (px)。押す場所の最小サイズ 44px と同じ。 */
export const TIME_WHEEL_ROW_PX = 44;
/** 見えている行数 (中央 1 行 + 上下 2 行ずつ)。 */
const VISIBLE_ROWS = 5;
/** 先頭 / 末尾の行を中央の帯まで回せるようにする上下の余白 (px)。 */
const PAD_PX = ((VISIBLE_ROWS - 1) / 2) * TIME_WHEEL_ROW_PX;
/** スクロールが止まったとみなすまでの待ち (ms)。 */
const SETTLE_MS = 90;
/** PageUp / PageDown で動かす分数 (目盛りと同じ 10 分)。 */
const PAGE_STEP_MIN = 10;

export interface TimeWheelProps {
  /** 選べる範囲の下端 (0 時からの分・この値を含む)。 */
  min: number;
  /** 選べる範囲の上端 (0 時からの分・この値を含む)。 */
  max: number;
  /** 選択中の時刻 (0 時からの分)。範囲外なら端に寄せて表示する。 */
  value: number;
  onChange: (value: number) => void;
  /** 読み上げ用の名前 (例: 「到着の時刻」)。 */
  ariaLabel?: string;
  className?: string;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

const useIsoLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

export function TimeWheel({ min, max, value, onChange, ariaLabel, className }: TimeWheelProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  const settleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 自分のスクロールから伝えた値。これと同じ `value` が返ってきても回し直さない。 */
  const emitted = useRef<number | null>(null);
  /** 初回は一気に合わせ、2 回目以降は回して見せる。 */
  const positioned = useRef(false);

  const selected = clamp(value, min, max);

  // 止まり待ちのタイマーは、セットした時点ではなく**止まった時点**の範囲と値で判定する。
  // 慣性で回っている間に範囲が変わる (到着 ⇄ 退出の切り替えなど) と、古い範囲で
  // 計算した時刻を `onChange` に流してしまうため。
  const latest = useRef({ min, max, selected, onChange });
  latest.current = { min, max, selected, onChange };

  // 範囲が変わったら、前の範囲で待っていたタイマーは捨てる (行の並びが変わるので、
  // そのスクロール位置はもう同じ時刻を指していない)。
  useIsoLayoutEffect(() => {
    if (settleTimer.current) {
      clearTimeout(settleTimer.current);
      settleTimer.current = null;
    }
  }, [min, max]);

  // `value` (と範囲) が外から変わったら、その行を中央の帯へ回す。
  useIsoLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const top = (selected - min) * TIME_WHEEL_ROW_PX;
    const fromOwnScroll = emitted.current === selected;
    emitted.current = null;
    if (!positioned.current) {
      positioned.current = true;
      el.scrollTop = top;
      return;
    }
    // 指で回して決まった値は、もうその位置にある (snap が揃える)。ここで動かすと
    // 指の動きと喧嘩するので触らない。
    if (fromOwnScroll) return;
    if (typeof el.scrollTo === 'function') {
      el.scrollTo({ top, behavior: 'smooth' });
    } else {
      el.scrollTop = top;
    }
  }, [selected, min]);

  useEffect(
    () => () => {
      if (settleTimer.current) clearTimeout(settleTimer.current);
    },
    [],
  );

  function handleScroll() {
    if (settleTimer.current) clearTimeout(settleTimer.current);
    settleTimer.current = setTimeout(() => {
      settleTimer.current = null;
      const el = ref.current;
      if (!el) return;
      const now = latest.current;
      const next = clamp(now.min + Math.round(el.scrollTop / TIME_WHEEL_ROW_PX), now.min, now.max);
      if (next !== now.selected) {
        emitted.current = next;
        now.onChange(next);
      }
    }, SETTLE_MS);
  }

  /** キーで動かす先。↑↓ = 1 分、PageUp / PageDown = 10 分、Home / End = 範囲の端。 */
  function keyTarget(key: string): number | null {
    switch (key) {
      case 'ArrowUp':
        return selected - 1;
      case 'ArrowDown':
        return selected + 1;
      case 'PageUp':
        return selected - PAGE_STEP_MIN;
      case 'PageDown':
        return selected + PAGE_STEP_MIN;
      case 'Home':
        return min;
      case 'End':
        return max;
      default:
        return null;
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    const target = keyTarget(e.key);
    if (target == null) return;
    e.preventDefault();
    const next = clamp(target, min, max);
    if (next !== selected) onChange(next);
  }

  const rows: number[] = [];
  for (let m = min; m <= max; m += 1) rows.push(m);

  return (
    <div className={cn('relative', className)}>
      <div
        ref={ref}
        role="listbox"
        tabIndex={0}
        aria-label={ariaLabel}
        aria-activedescendant={`time-wheel-${selected}`}
        data-testid="time-wheel"
        onScroll={handleScroll}
        onKeyDown={handleKeyDown}
        className="snap-y snap-mandatory overflow-y-auto overscroll-contain rounded-2xl border border-border-default bg-bg-muted [scrollbar-width:none] [touch-action:pan-y] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary [&::-webkit-scrollbar]:hidden"
        style={{
          height: VISIBLE_ROWS * TIME_WHEEL_ROW_PX,
          paddingTop: PAD_PX,
          paddingBottom: PAD_PX,
        }}
      >
        {rows.map((m) => {
          const isSelected = m === selected;
          return (
            <div
              key={m}
              id={`time-wheel-${m}`}
              role="option"
              aria-selected={isSelected}
              onClick={() => {
                if (!isSelected) onChange(m);
              }}
              className={cn(
                'tnum flex snap-center items-center justify-center text-lg text-text-muted',
                m % 10 === 0 && 'text-[23px] font-bold text-text-secondary',
                isSelected && 'text-[30px] font-bold text-brand-primary-hover',
              )}
              style={{ height: TIME_WHEEL_ROW_PX }}
            >
              {minutesToHm(m)}
            </div>
          );
        })}
      </div>
      {/* 中央の帯 = いま選んでいる時刻。トークン色は var() なので Tailwind の
          /alpha 修飾子が効かない (クラスが生成されない)。薄い地色は style で作る。 */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-px border-y-2 border-brand-primary"
        style={{
          top: PAD_PX,
          height: TIME_WHEEL_ROW_PX,
          background: 'color-mix(in srgb, var(--brand-primary) 8%, transparent)',
        }}
      />
      {/* 上下を薄くする (回すものだと分かるように)。 */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-px rounded-2xl"
        style={{
          background:
            'linear-gradient(to bottom, var(--bg-muted) 0, transparent 34%, transparent 66%, var(--bg-muted) 100%)',
        }}
      />
    </div>
  );
}
