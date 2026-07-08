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
import { fireEvent, render, screen } from '@testing-library/react';

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

  // RB (PO決定 2026-07-08 / 8749c6a): PC版は全ロール同一表示。設定は staff も閲覧でき、
  // 保存操作だけが権限どおり無効になる。
  it('3. staff → ⚙ ボタンも表示される (全ロール同一表示)', () => {
    setRole('staff');
    render(<Header onToggleSidebar={vi.fn()} />);
    expect(screen.getByRole('link', { name: '最適化ルール設定' })).toBeInTheDocument();
  });

  // PW運用 (PO決定 2026-07-08 / 1bd2daa): パスワードは全員共通のため、自己変更の入口は
  // admin のみ。誤って共通パスワードから外れるのを防ぐ。
  // (メニューはユーザーメニューの Popover 内にあるので開いてから確認する)
  it('4. パスワード変更メニューは admin にだけ出る', async () => {
    setRole('admin');
    const { unmount } = render(<Header onToggleSidebar={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'ユーザーメニュー' }));
    expect(await screen.findByText('パスワード変更')).toBeInTheDocument();
    unmount();

    setRole('staff');
    render(<Header onToggleSidebar={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'ユーザーメニュー' }));
    // ログアウトは出る = Popover は開いている。その上でパスワード変更だけ無いことを見る。
    expect(await screen.findByText('ログアウト')).toBeInTheDocument();
    expect(screen.queryByText('パスワード変更')).not.toBeInTheDocument();
  });
});
