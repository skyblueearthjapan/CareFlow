/**
 * 時刻のホイール (設計 2026-09-30 決定 #4)。
 *
 * jsdom はレイアウトも scroll-snap も持たないので、ここでは「スクロール位置 ⇄ 値」の
 * 対応だけを縛る: 1 行 44px・止まった位置の行が値になる・範囲の端で止まる・
 * 値を外から変えるとその位置へ回る。回し心地そのものは実機で確かめる。
 */
import * as React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

import { TimeWheel, TIME_WHEEL_ROW_PX } from '../TimeWheel';

// 12:00〜13:06 (到着を合わせるときの典型的な範囲の一部)。
const MIN = 12 * 60;
const MAX = 13 * 60 + 6;

/** 指で回して止まったことにする (スクロール位置を置いて、落ち着くまで待つ)。 */
function scrollTo(el: HTMLElement, top: number) {
  el.scrollTop = top;
  fireEvent.scroll(el);
  act(() => {
    vi.advanceTimersByTime(200);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('TimeWheel — 表示', () => {
  it('範囲の 1 分刻みを並べ、選択中の行に印を付ける', () => {
    render(<TimeWheel min={MIN} max={MAX} value={MAX} onChange={vi.fn()} ariaLabel="到着の時刻" />);
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(MAX - MIN + 1);
    expect(options[0]).toHaveTextContent('12:00');
    expect(options[options.length - 1]).toHaveTextContent('13:06');
    expect(screen.getByRole('option', { selected: true })).toHaveTextContent('13:06');
    expect(screen.getByRole('listbox', { name: '到着の時刻' })).toBeInTheDocument();
  });

  it('10 分ごとの時刻が太字の目盛り', () => {
    render(<TimeWheel min={MIN} max={MAX} value={MIN + 3} onChange={vi.fn()} />);
    expect(screen.getByText('12:50').className).toContain('font-bold');
    expect(screen.getByText('12:51').className).not.toContain('font-bold');
  });

  it('開いた時点で、選択中の時刻が中央の帯に来る位置までスクロールしてある', () => {
    render(<TimeWheel min={MIN} max={MAX} value={MIN + 56} onChange={vi.fn()} />);
    expect(screen.getByTestId('time-wheel').scrollTop).toBe(56 * TIME_WHEEL_ROW_PX);
  });
});

describe('TimeWheel — 回して選ぶ', () => {
  it('止まった位置の行が値になる', () => {
    const onChange = vi.fn();
    render(<TimeWheel min={MIN} max={MAX} value={MAX} onChange={onChange} />);
    scrollTo(screen.getByTestId('time-wheel'), 56 * TIME_WHEEL_ROW_PX);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(MIN + 56); // 12:56
  });

  it('行の途中で止まったら近い方の行に寄せる', () => {
    const onChange = vi.fn();
    render(<TimeWheel min={MIN} max={MAX} value={MAX} onChange={onChange} />);
    scrollTo(screen.getByTestId('time-wheel'), 10 * TIME_WHEEL_ROW_PX + 30);
    expect(onChange).toHaveBeenCalledWith(MIN + 11);
  });

  it('回している途中 (まだ止まっていない) では値を変えない', () => {
    const onChange = vi.fn();
    render(<TimeWheel min={MIN} max={MAX} value={MAX} onChange={onChange} />);
    const el = screen.getByTestId('time-wheel');
    el.scrollTop = 20 * TIME_WHEEL_ROW_PX;
    fireEvent.scroll(el);
    act(() => {
      vi.advanceTimersByTime(30);
    });
    el.scrollTop = 30 * TIME_WHEEL_ROW_PX;
    fireEvent.scroll(el);
    expect(onChange).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(MIN + 30);
  });

  it('同じ行に止まり直しただけなら何も伝えない', () => {
    const onChange = vi.fn();
    render(<TimeWheel min={MIN} max={MAX} value={MIN + 56} onChange={onChange} />);
    scrollTo(screen.getByTestId('time-wheel'), 56 * TIME_WHEEL_ROW_PX + 4);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('行を押してもその時刻を選べる', () => {
    const onChange = vi.fn();
    render(<TimeWheel min={MIN} max={MAX} value={MAX} onChange={onChange} />);
    fireEvent.click(screen.getByText('12:58'));
    expect(onChange).toHaveBeenCalledWith(MIN + 58);
  });
});

describe('TimeWheel — 範囲の端で止まる', () => {
  it('上端より先へ回しても、上端の時刻まで', () => {
    const onChange = vi.fn();
    render(<TimeWheel min={MIN} max={MAX} value={MIN + 10} onChange={onChange} />);
    scrollTo(screen.getByTestId('time-wheel'), 9999 * TIME_WHEEL_ROW_PX);
    expect(onChange).toHaveBeenCalledWith(MAX);
  });

  it('下端より先へ回しても、下端の時刻まで', () => {
    const onChange = vi.fn();
    render(<TimeWheel min={MIN} max={MAX} value={MIN + 10} onChange={onChange} />);
    scrollTo(screen.getByTestId('time-wheel'), -500);
    expect(onChange).toHaveBeenCalledWith(MIN);
  });

  it('範囲の外の時刻は並べない', () => {
    render(<TimeWheel min={MIN} max={MAX} value={MAX} onChange={vi.fn()} />);
    expect(screen.queryByText('11:59')).toBeNull();
    expect(screen.queryByText('13:07')).toBeNull();
  });

  it('矢印キーも端で止まる', () => {
    const onChange = vi.fn();
    render(<TimeWheel min={MIN} max={MAX} value={MAX} onChange={onChange} />);
    const el = screen.getByTestId('time-wheel');
    fireEvent.keyDown(el, { key: 'ArrowDown' });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.keyDown(el, { key: 'ArrowUp' });
    expect(onChange).toHaveBeenCalledWith(MAX - 1);
  });
});

describe('TimeWheel — キー操作 (L-3)', () => {
  function pressed(value: number, key: string) {
    const onChange = vi.fn();
    render(<TimeWheel min={MIN} max={MAX} value={value} onChange={onChange} />);
    fireEvent.keyDown(screen.getByTestId('time-wheel'), { key });
    return onChange;
  }

  it('Home で範囲の下端へ', () => {
    expect(pressed(MIN + 30, 'Home')).toHaveBeenCalledWith(MIN);
  });

  it('End で範囲の上端へ', () => {
    expect(pressed(MIN + 30, 'End')).toHaveBeenCalledWith(MAX);
  });

  it('PageUp は 10 分まえ', () => {
    expect(pressed(MIN + 30, 'PageUp')).toHaveBeenCalledWith(MIN + 20);
  });

  it('PageDown は 10 分あと', () => {
    expect(pressed(MIN + 30, 'PageDown')).toHaveBeenCalledWith(MIN + 40);
  });

  it('10 分動かせないときは端で止まる', () => {
    expect(pressed(MIN + 3, 'PageUp')).toHaveBeenCalledWith(MIN);
  });

  it('下端での Home は何も伝えない', () => {
    expect(pressed(MIN, 'Home')).not.toHaveBeenCalled();
  });

  it('上端での End / PageDown も何も伝えない', () => {
    const onChange = vi.fn();
    render(<TimeWheel min={MIN} max={MAX} value={MAX} onChange={onChange} />);
    const el = screen.getByTestId('time-wheel');
    fireEvent.keyDown(el, { key: 'End' });
    fireEvent.keyDown(el, { key: 'PageDown' });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('関係ないキーは素通しする (既定の動作を止めない)', () => {
    const onChange = vi.fn();
    render(<TimeWheel min={MIN} max={MAX} value={MIN + 30} onChange={onChange} />);
    const notPrevented = fireEvent.keyDown(screen.getByTestId('time-wheel'), { key: 'Tab' });
    expect(notPrevented).toBe(true);
    expect(onChange).not.toHaveBeenCalled();
  });
});

/**
 * レビュー L-2: 止まり待ちのタイマーが、セットした時点の範囲と値を握っていた。慣性で
 * 回っている間に到着 ⇄ 退出を切り替えると、前の範囲で計算した時刻が流れていた。
 */
describe('TimeWheel — 回している途中で範囲が変わる (L-2)', () => {
  // 到着の範囲 12:00〜13:06 → 退出の範囲 13:07〜14:00。
  const DEP_MIN = MAX + 1;
  const DEP_MAX = 14 * 60;

  it('前の範囲で待っていたタイマーは捨てる (前の範囲の時刻を流さない)', () => {
    const onChange = vi.fn();
    const { rerender } = render(<TimeWheel min={MIN} max={MAX} value={MAX} onChange={onChange} />);
    const el = screen.getByTestId('time-wheel');
    // 慣性で回っている (まだ止まっていない)。
    el.scrollTop = 20 * TIME_WHEEL_ROW_PX;
    fireEvent.scroll(el);

    rerender(<TimeWheel min={DEP_MIN} max={DEP_MAX} value={DEP_MIN + 35} onChange={onChange} />);
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('範囲が変わった後に止まったら、新しい範囲で時刻を決める', () => {
    const onChange = vi.fn();
    const { rerender } = render(<TimeWheel min={MIN} max={MAX} value={MAX} onChange={onChange} />);
    const el = screen.getByTestId('time-wheel');
    el.scrollTop = 20 * TIME_WHEEL_ROW_PX;
    fireEvent.scroll(el);
    rerender(<TimeWheel min={DEP_MIN} max={DEP_MAX} value={DEP_MIN + 35} onChange={onChange} />);

    scrollTo(el, 10 * TIME_WHEEL_ROW_PX);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(DEP_MIN + 10); // 13:17 (12:10 ではない)
  });

  it('止まるのを待つ間に差し替わった onChange へ伝える', () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = render(<TimeWheel min={MIN} max={MAX} value={MAX} onChange={first} />);
    const el = screen.getByTestId('time-wheel');
    el.scrollTop = 20 * TIME_WHEEL_ROW_PX;
    fireEvent.scroll(el);
    rerender(<TimeWheel min={MIN} max={MAX} value={MAX} onChange={second} />);
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledWith(MIN + 20);
  });
});

describe('TimeWheel — 値を外から変えるとその位置へ回る', () => {
  function Harness({ initial }: { initial: number }) {
    const [value, setValue] = React.useState(initial);
    return (
      <>
        <button onClick={() => setValue(MIN + 56)}>10分前</button>
        <TimeWheel min={MIN} max={MAX} value={value} onChange={setValue} />
      </>
    );
  }

  it('ひと押しで飛ぶと、その時刻の行までスクロールする', () => {
    render(<Harness initial={MAX} />);
    const el = screen.getByTestId('time-wheel');
    expect(el.scrollTop).toBe((MAX - MIN) * TIME_WHEEL_ROW_PX);

    // 実機では滑らかに回す (scrollTo + smooth)。回った先を確かめる。
    const scrollToSpy = vi.fn((opts: ScrollToOptions) => {
      el.scrollTop = opts.top ?? 0;
    });
    Object.defineProperty(el, 'scrollTo', { configurable: true, value: scrollToSpy });

    fireEvent.click(screen.getByText('10分前'));
    expect(scrollToSpy).toHaveBeenCalledWith({ top: 56 * TIME_WHEEL_ROW_PX, behavior: 'smooth' });
    expect(screen.getByRole('option', { selected: true })).toHaveTextContent('12:56');
  });

  it('指で回して決まった値では、回し直さない (指の動きと喧嘩しない)', () => {
    render(<Harness initial={MAX} />);
    const el = screen.getByTestId('time-wheel');
    const scrollToSpy = vi.fn();
    Object.defineProperty(el, 'scrollTo', { configurable: true, value: scrollToSpy });

    scrollTo(el, 40 * TIME_WHEEL_ROW_PX);
    expect(screen.getByRole('option', { selected: true })).toHaveTextContent('12:40');
    expect(scrollToSpy).not.toHaveBeenCalled();
    expect(el.scrollTop).toBe(40 * TIME_WHEEL_ROW_PX);
  });

  it('範囲の外の値を渡されたら、端の時刻を選択中にする', () => {
    render(<TimeWheel min={MIN} max={MAX} value={MAX + 30} onChange={vi.fn()} />);
    expect(screen.getByRole('option', { selected: true })).toHaveTextContent('13:06');
  });
});
