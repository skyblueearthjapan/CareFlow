/**
 * Header のアカウントの表示 (PO 2026-10-13):
 *   名前 (displayName) → ログイン ID (大文字) → メール の順で出す。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
  signOut: vi.fn(),
}));

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

function openMenuWith(user: Record<string, unknown>) {
  (useSession as Mock).mockReturnValue({
    data: { user: { role: 'admin', name: 'local-part', ...user } },
    status: 'authenticated',
  });
  render(<Header onToggleSidebar={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'ユーザーメニュー' }));
}

describe('Header — アカウントの名前', () => {
  beforeEach(() => vi.clearAllMocks());

  it('名前があれば名前を出す', async () => {
    openMenuWith({ displayName: '今泉 勇二', username: 'a001', email: 'boss@x.jp' });
    expect(await screen.findByText('今泉 勇二')).toBeInTheDocument();
  });

  it('名前が空ならログイン ID を大文字で出す', async () => {
    openMenuWith({ displayName: null, username: 'a005', email: null });
    expect(await screen.findByText('A005')).toBeInTheDocument();
  });

  it('名前もログイン ID も無ければメールを出す', async () => {
    openMenuWith({ displayName: null, username: null, email: 'only@x.jp' });
    const hits = await screen.findAllByText('only@x.jp');
    expect(hits.length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('local-part')).not.toBeInTheDocument();
  });
});
