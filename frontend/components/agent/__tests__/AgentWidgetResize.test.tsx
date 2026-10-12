/**
 * 浮いている小窓: 左の端の境目で幅を変える（←→・覚える・ダブルクリックで既定）。
 */
import * as React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('next-auth/react', () => ({ useSession: vi.fn() }));
vi.mock('../useAgentFlag', () => ({ useAgentFlag: vi.fn() }));

import { useSession } from 'next-auth/react';

import { AgentWidget } from '../AgentWidget';
import { createMockRelay } from '../mockRelay';
import { useAgentFlag } from '../useAgentFlag';

describe('浮いている小窓の幅', () => {
  beforeEach(() => {
    vi.stubEnv('NEXT_PUBLIC_AGENT_PILOT_USERS', 'u-1');
    (useAgentFlag as Mock).mockReturnValue(true);
    (useSession as Mock).mockReturnValue({
      data: { user: { id: 'u-1', role: 'admin', email: 'u-1@example.com' }, accessToken: 't' },
      status: 'authenticated',
    });
    window.localStorage.clear();
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1600 });
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.clearAllMocks();
  });

  it('左の端をつかんで（←→で）幅を変え、覚える', () => {
    const { unmount } = render(<AgentWidget relay={createMockRelay({ speed: 0 })} />);
    fireEvent.click(screen.getByRole('button', { name: 'らく助を開く' }));
    const h = screen.getByRole('separator', { name: '小窓の幅' });
    const box = screen.getByRole('region', { name: 'らく助エージェント' });
    expect(h).toHaveAttribute('aria-valuenow', '400');
    expect(box.style.width).toBe('400px');
    fireEvent.keyDown(h, { key: 'ArrowLeft', shiftKey: true });
    expect(box.style.width).toBe('464px');
    expect(window.localStorage.getItem('rk-agent-w-compact')).toBe('464');
    fireEvent.keyDown(h, { key: 'End' });
    expect(box.style.width).toBe('800px'); // 画面の 50% まで
    fireEvent.doubleClick(h);
    expect(box.style.width).toBe('400px');
    unmount();
  });
});
