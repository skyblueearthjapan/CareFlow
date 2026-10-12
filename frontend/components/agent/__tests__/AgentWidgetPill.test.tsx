/**
 * たたんだ丸(右下の「ご用件をどうぞ」)を動かす: つかんで動かす・5px 未満はクリック・
 * 覚える(開いた窓とは別)・画面の中に収める・ダブルクリック/Home で右下へ・矢印キー。
 */
import * as React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

vi.mock('next-auth/react', () => ({ useSession: vi.fn() }));
vi.mock('../useAgentFlag', () => ({ useAgentFlag: vi.fn() }));

import { useSession } from 'next-auth/react';

import { AgentWidget, clampPos } from '../AgentWidget';
import { createMockRelay } from '../mockRelay';
import { useAgentFlag } from '../useAgentFlag';

// jsdom には PointerEvent が無い（clientX が渡らない）ので、MouseEvent で足す
if (typeof window.PointerEvent === 'undefined') {
  class PointerEventPolyfill extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 0;
    }
  }
  (window as unknown as { PointerEvent: typeof MouseEvent }).PointerEvent = PointerEventPolyfill;
}

const pill = () => screen.getByRole('button', { name: 'らく助を開く' });
const region = () => screen.queryByRole('region', { name: 'らく助エージェント' });

function dragBy(el: HTMLElement, dx: number, dy: number) {
  fireEvent.pointerDown(el, { clientX: 500, clientY: 500, button: 0, pointerId: 1 });
  fireEvent.pointerMove(el, { clientX: 500 + dx, clientY: 500 + dy, pointerId: 1 });
  fireEvent.pointerUp(el, { clientX: 500 + dx, clientY: 500 + dy, pointerId: 1 });
  fireEvent.click(el, { detail: 1 });
}

describe('たたんだ丸を動かす', () => {
  beforeEach(() => {
    vi.stubEnv('NEXT_PUBLIC_AGENT_PILOT_USERS', 'u-1');
    (useAgentFlag as Mock).mockReturnValue(true);
    (useSession as Mock).mockReturnValue({
      data: { user: { id: 'u-1', role: 'admin', email: 'u-1@example.com' }, accessToken: 't' },
      status: 'authenticated',
    });
    window.localStorage.clear();
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
    vi.clearAllMocks();
  });

  it('clampPos は画面の中に収める', () => {
    expect(clampPos({ right: -20, bottom: 5000 }, 300, 64, 1200, 800)).toEqual({
      right: 0,
      bottom: 736,
    });
    expect(clampPos({ right: 2000, bottom: 40 }, 300, 64, 1200, 800)).toEqual({
      right: 900,
      bottom: 40,
    });
  });

  it('つかんで動かすと開かずに動き、丸の位置だけを覚える', () => {
    render(<AgentWidget relay={createMockRelay({ speed: 0 })} />);
    dragBy(pill(), -100, -50);
    expect(region()).toBeNull(); // 開かない
    expect(pill().style.right).toBe('116px');
    expect(pill().style.bottom).toBe('66px');
    expect(JSON.parse(window.localStorage.getItem('rk-agent-pill-pos') ?? '{}')).toEqual({
      right: 116,
      bottom: 66,
    });
    expect(window.localStorage.getItem('rk-agent-pos')).toBeNull(); // 開いた窓とは別
  });

  it('5px 未満の動きはクリックとして開く', () => {
    render(<AgentWidget relay={createMockRelay({ speed: 0 })} />);
    dragBy(pill(), 3, 2);
    expect(region()).not.toBeNull();
  });

  it('画面の外へは出ない', () => {
    render(<AgentWidget relay={createMockRelay({ speed: 0 })} />);
    dragBy(pill(), 300, 300); // 右下の外へ
    expect(pill().style.right).toBe('0px');
    expect(pill().style.bottom).toBe('0px');
  });

  it('覚えた位置で出て、画面が小さくなったら中へ戻す', () => {
    window.localStorage.setItem('rk-agent-pill-pos', JSON.stringify({ right: 800, bottom: 600 }));
    render(<AgentWidget relay={createMockRelay({ speed: 0 })} />);
    expect(pill().style.right).toBe('800px');
    act(() => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: 700 });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: 500 });
      window.dispatchEvent(new Event('resize'));
    });
    expect(pill().style.right).toBe('400px'); // 700 - 300
    expect(pill().style.bottom).toBe('436px'); // 500 - 64
  });

  it('ダブルクリックで右下へ戻し、開かない', () => {
    vi.useFakeTimers();
    window.localStorage.setItem('rk-agent-pill-pos', JSON.stringify({ right: 300, bottom: 200 }));
    render(<AgentWidget relay={createMockRelay({ speed: 0 })} />);
    fireEvent.click(pill(), { detail: 1 });
    fireEvent.click(pill(), { detail: 2 });
    fireEvent.doubleClick(pill());
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(region()).toBeNull();
    expect(pill().style.right).toBe('16px');
    expect(pill().style.bottom).toBe('16px');
    expect(window.localStorage.getItem('rk-agent-pill-pos')).toBeNull();
  });

  it('動かした丸は 1 回のクリックで少し待ってから開く', () => {
    vi.useFakeTimers();
    window.localStorage.setItem('rk-agent-pill-pos', JSON.stringify({ right: 300, bottom: 200 }));
    render(<AgentWidget relay={createMockRelay({ speed: 0 })} />);
    fireEvent.click(pill(), { detail: 1 });
    expect(region()).toBeNull();
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(region()).not.toBeNull();
  });

  it('矢印キーで動き、Home で右下へ戻る', () => {
    render(<AgentWidget relay={createMockRelay({ speed: 0 })} />);
    pill().focus();
    fireEvent.keyDown(pill(), { key: 'ArrowLeft' });
    fireEvent.keyDown(pill(), { key: 'ArrowUp', shiftKey: true });
    expect(pill().style.right).toBe('26px');
    expect(pill().style.bottom).toBe('66px');
    fireEvent.keyDown(pill(), { key: 'Home' });
    expect(pill().style.right).toBe('16px');
    expect(region()).toBeNull();
  });
});
