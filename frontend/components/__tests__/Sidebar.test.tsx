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

import { useSession } from 'next-auth/react';
import { Sidebar } from '../Sidebar';

function setRole(role: 'admin' | 'manager' | 'staff') {
  (useSession as Mock).mockReturnValue({
    data: { user: { role, name: 'テスト' } },
    status: 'authenticated',
  });
}

describe('Sidebar — ロール別メニュー表示', () => {
  beforeEach(() => vi.clearAllMocks());

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
});
