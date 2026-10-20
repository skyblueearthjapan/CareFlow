/**
 * Sidebar のロール別メニュー表示テスト (2026-08-24)。
 *
 * 背景: 「連携」「監査ログ」(strictAdmin) が `role === 'admin'` の生比較で
 * 判定されており、旧セッション JWT に残る 'manager' (mig 0069 で admin へ
 * 移行済みの別名 — lib/rbac.ts) だとメニューが消える不整合があった。
 * isAdminRole() で判定するよう修正した回帰テスト。
 *
 * カバー:
 *   1. admin   → 連携・監査ログ・申請履歴 すべて表示
 *   2. manager (旧トークン) → admin の別名として同じく表示
 *   3. staff   → 連携・監査ログ・申請履歴 は非表示 (共通メニューは表示)
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard',
}));

// メニューの未処理の休みの申請の件数
let pendingTotal = 0;
const pendingCalls: unknown[][] = [];
vi.mock('@/lib/queries/pending_requests', () => ({
  usePendingRequests: (...args: unknown[]) => {
    pendingCalls.push(args);
    return { data: { items: [], total: pendingTotal, limit: 1, offset: 0 } };
  },
}));

import { useSession } from 'next-auth/react';
import { Sidebar } from '../Sidebar';

function setRole(role: 'admin' | 'manager' | 'staff') {
  (useSession as Mock).mockReturnValue({
    data: { user: { role, name: 'テスト' } },
    status: 'authenticated',
  });
}

describe('Sidebar — ロール別メニュー表示', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    pendingTotal = 0;
    pendingCalls.length = 0;
  });

  it('1. admin → 連携・監査ログ・申請履歴が表示される', () => {
    setRole('admin');
    render(<Sidebar collapsed={false} />);
    expect(screen.getByRole('link', { name: /連携/ })).toHaveAttribute(
      'href',
      '/integrations/kaipoke',
    );
    expect(screen.getByRole('link', { name: /監査ログ/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /申請履歴/ })).toBeInTheDocument();
  });

  it('2. manager (旧セッショントークン) → admin の別名として連携・監査ログが表示される', () => {
    setRole('manager');
    render(<Sidebar collapsed={false} />);
    expect(screen.getByRole('link', { name: /連携/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /監査ログ/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /申請履歴/ })).toBeInTheDocument();
  });

  it('3. staff → 連携・監査ログ・申請履歴は非表示、共通メニューは表示', () => {
    setRole('staff');
    render(<Sidebar collapsed={false} />);
    expect(screen.queryByRole('link', { name: /連携/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /監査ログ/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /申請履歴/ })).not.toBeInTheDocument();
    // 共通メニューは全ロール表示
    expect(screen.getByRole('link', { name: /スケジュール/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /訪問モニター/ })).toBeInTheDocument();
  });

  it('4. 「休み・勤務表」は申請履歴のすぐ下・管理者だけ', () => {
    setRole('admin');
    render(<Sidebar collapsed={false} />);
    const links = screen.getAllByRole('link').map((a) => a.textContent);
    const i = links.indexOf('申請履歴');
    expect(links[i + 1]).toBe('休み・勤務表');
    expect(screen.getByRole('link', { name: '休み・勤務表' })).toHaveAttribute(
      'href',
      '/admin/leave',
    );
    // 0 件なら件数を出さない
    expect(screen.queryByText(/未処理の申請/)).not.toBeInTheDocument();
  });

  it('5. staff には「休み・勤務表」を出さない', () => {
    setRole('staff');
    render(<Sidebar collapsed={false} />);
    expect(screen.queryByRole('link', { name: '休み・勤務表' })).not.toBeInTheDocument();
    // 管理者でなければ件数を読まない
    expect(pendingCalls.every((c) => (c[1] as { enabled: boolean }).enabled === false)).toBe(true);
  });

  it('6. 未処理の休みの申請の件数 (60 秒に 1 回まで)・折りたたみでは点', () => {
    setRole('admin');
    pendingTotal = 3;
    const { unmount } = render(<Sidebar collapsed={false} />);
    expect(pendingCalls[0]).toEqual([
      { request_type: 'staff_off', status: 'pending', limit: 1 },
      { enabled: true, pollMs: 60_000 },
    ]);
    const link = screen.getByRole('link', { name: /休み・勤務表/ });
    expect(link).toHaveTextContent('3');
    expect(link).toHaveTextContent('未処理の申請 3 件');
    unmount();
    render(<Sidebar collapsed />);
    expect(screen.getByTestId('nav-badge-dot')).toHaveTextContent('未処理の申請 3 件');
  });
});
