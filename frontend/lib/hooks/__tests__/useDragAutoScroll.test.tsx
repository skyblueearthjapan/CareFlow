/**
 * useDragAutoScroll — ドラッグ中エッジ自動スクロールのテスト。
 * jsdom はレイアウトを持たないため、純関数 (edgeDelta) と
 * リスナー着脱・rAF 起動/停止の配線を検証する。
 */
import * as React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, render } from '@testing-library/react';

import {
  EDGE_PX,
  MAX_SPEED_PX,
  edgeDelta,
  useDragAutoScroll,
} from '@/lib/hooks/useDragAutoScroll';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('edgeDelta', () => {
  it('端に近いほど速く、圏外は 0、方向は上/左が負', () => {
    // 上端ぴったり → 最大速度で上 (負)
    expect(edgeDelta(0, 0, 1000)).toBe(-MAX_SPEED_PX);
    // 上端から EDGE 以内 → 比例した負値
    expect(edgeDelta(EDGE_PX / 2, 0, 1000)).toBe(-Math.ceil(MAX_SPEED_PX / 2));
    // 中央 → 0
    expect(edgeDelta(500, 0, 1000)).toBe(0);
    // 下端ぴったり → 最大速度で下 (正)
    expect(edgeDelta(1000, 0, 1000)).toBe(MAX_SPEED_PX);
    // 圏外 (EDGE ちょうど) → 0
    expect(edgeDelta(EDGE_PX, 0, 1000)).toBe(0);
    expect(edgeDelta(1000 - EDGE_PX, 0, 1000)).toBe(0);
  });

  it('小さすぎるコンテナ (両端の圏が重なる) は対象外', () => {
    expect(edgeDelta(10, 0, EDGE_PX * 2)).toBe(0);
  });
});

function Probe({ enabled = true }: { enabled?: boolean }) {
  useDragAutoScroll(enabled);
  return <div />;
}

describe('useDragAutoScroll (配線)', () => {
  it('dragover で rAF ループが始まり、dragend で止まる', () => {
    const rafSpy = vi
      .spyOn(window, 'requestAnimationFrame')
      .mockImplementation(() => 1 as unknown as number);
    const cancelSpy = vi.spyOn(window, 'cancelAnimationFrame');
    render(<Probe />);

    document.dispatchEvent(new Event('dragover', { bubbles: true }));
    expect(rafSpy).toHaveBeenCalled();

    document.dispatchEvent(new Event('dragend', { bubbles: true }));
    expect(cancelSpy).toHaveBeenCalled();
  });

  it('アンマウントでリスナーを外す (以後 dragover しても rAF は始まらない)', () => {
    const rafSpy = vi
      .spyOn(window, 'requestAnimationFrame')
      .mockImplementation(() => 1 as unknown as number);
    const { unmount } = render(<Probe />);
    unmount();
    document.dispatchEvent(new Event('dragover', { bubbles: true }));
    expect(rafSpy).not.toHaveBeenCalled();
  });

  it('enabled=false なら何もしない', () => {
    const rafSpy = vi
      .spyOn(window, 'requestAnimationFrame')
      .mockImplementation(() => 1 as unknown as number);
    render(<Probe enabled={false} />);
    document.dispatchEvent(new Event('dragover', { bubbles: true }));
    expect(rafSpy).not.toHaveBeenCalled();
  });
});
