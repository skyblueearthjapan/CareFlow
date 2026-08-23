import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { SyncedHScroll } from '../synced-h-scroll';

/** jsdom は PointerEvent を持たないので、素の Event に座標を載せて飛ばす。 */
function firePointer(
  el: Element,
  type: 'pointerdown' | 'pointermove' | 'pointerup',
  clientX: number,
  pointerId = 1,
) {
  const ev = new Event(type, { bubbles: true, cancelable: true });
  Object.assign(ev, { clientX, clientY: 10, pointerId, button: 0, isPrimary: true });
  fireEvent(el, ev);
}

/** jsdom はレイアウトを持たないので scrollWidth/clientWidth を固定する。 */
function mockSizes(scrollWidth: number, clientWidth: number) {
  Object.defineProperty(HTMLElement.prototype, 'scrollWidth', {
    configurable: true,
    get: () => scrollWidth,
  });
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    get: () => clientWidth,
  });
}

describe('SyncedHScroll (案A: 上の1本に統一 + 空き部分ドラッグでパン)', () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockSizes(2400, 800);
  });
  afterEach(() => {
    mockSizes(0, 0);
  });

  it('本体はブラウザ既定バーを隠し、上のトラックだけ出す', () => {
    render(
      <SyncedHScroll data-testid="hs">
        <div>wide</div>
      </SyncedHScroll>,
    );
    const top = screen.getByTestId('hs-top-scrollbar');
    expect(top.className).toContain('h-3.5');
    const body = top.parentElement!.querySelector('.scrollbar-none');
    expect(body).not.toBeNull();
  });

  it('空き部分をドラッグすると横に動く(4px 未満は無視)', () => {
    render(
      <SyncedHScroll data-testid="hs">
        <div data-testid="content">wide</div>
      </SyncedHScroll>,
    );
    const body = screen.getByTestId('content').parentElement as HTMLDivElement;
    body.scrollLeft = 100;
    firePointer(body, 'pointerdown', 300, 1);
    firePointer(body, 'pointermove', 298, 1); // しきい値未満
    expect(body.scrollLeft).toBe(100);
    firePointer(body, 'pointermove', 250, 1);
    expect(body.scrollLeft).toBe(150); // 左へ 50px ドラッグ = 右へ 50px スクロール
    firePointer(body, 'pointerup', 0, 1);
  });

  it('ボタンやドラッグ要素の上ではパンしない', () => {
    render(
      <SyncedHScroll data-testid="hs">
        <div>
          <button type="button" data-testid="btn">
            操作
          </button>
          <span draggable="true" data-testid="grip">
            ⠿
          </span>
        </div>
      </SyncedHScroll>,
    );
    const btn = screen.getByTestId('btn');
    const body = btn.parentElement!.parentElement as HTMLDivElement;
    body.scrollLeft = 0;
    firePointer(btn, 'pointerdown', 300, 1);
    firePointer(body, 'pointermove', 200, 1);
    expect(body.scrollLeft).toBe(0);
    firePointer(screen.getByTestId('grip'), 'pointerdown', 300, 2);
    firePointer(body, 'pointermove', 200, 2);
    expect(body.scrollLeft).toBe(0);
  });

  it('初回だけヒントを出し、閉じると記憶される', () => {
    const { unmount } = render(
      <SyncedHScroll data-testid="hs">
        <div>wide</div>
      </SyncedHScroll>,
    );
    expect(screen.getByTestId('hs-hint')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('ヒントを閉じる'));
    expect(screen.queryByTestId('hs-hint')).not.toBeInTheDocument();
    unmount();
    render(
      <SyncedHScroll data-testid="hs">
        <div>wide</div>
      </SyncedHScroll>,
    );
    expect(screen.queryByTestId('hs-hint')).not.toBeInTheDocument();
  });
});
