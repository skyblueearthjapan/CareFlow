/**
 * Header の ⚙ 最適化ルール設定ボタン — role 表示テスト (Phase G-88 Step4)。
 *
 * カバー:
 *   1. admin → ⚙ ボタンが表示され /settings/scheduling へリンクする
 *   2. manager → ⚙ ボタンが表示される
 *   3. staff → ⚙ ボタンは表示されない
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
  signOut: vi.fn(),
}));

// 通知ボタンが叩く query hooks をスタブ (表示に影響しない最小形)。
vi.mock('@/lib/queries/notifications', () => ({
  useNotifications: () => ({
    data: { items: [] },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useMarkRead: () => ({ mutate: vi.fn() }),
  useMarkAllRead: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { useSession } from 'next-auth/react';
import { Header } from '../Header';

function setRole(role: 'admin' | 'manager' | 'staff') {
  (useSession as Mock).mockReturnValue({
    data: { user: { role, name: 'テスト', email: 't@example.com' } },
    status: 'authenticated',
  });
}

describe('Header — 最適化ルール設定ボタン (Phase G-88)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('1. admin → ⚙ ボタンが /settings/scheduling へリンク', () => {
    setRole('admin');
    render(<Header onToggleSidebar={vi.fn()} />);
    const link = screen.getByRole('link', { name: '最適化ルール設定' });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', '/settings/scheduling');
  });

  it('2. manager → ⚙ ボタンが表示される', () => {
    setRole('manager');
    render(<Header onToggleSidebar={vi.fn()} />);
    expect(screen.getByRole('link', { name: '最適化ルール設定' })).toBeInTheDocument();
  });

  it('3. staff → ⚙ ボタンは表示されない', () => {
    setRole('staff');
    render(<Header onToggleSidebar={vi.fn()} />);
    expect(screen.queryByRole('link', { name: '最適化ルール設定' })).not.toBeInTheDocument();
  });
});
