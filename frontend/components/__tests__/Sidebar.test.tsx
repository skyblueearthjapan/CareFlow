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

// メニューの未処理の休みの申請の件数 (summary の pending_total = 月をまたいだ件数)
let pendingTotal = 0;
const pendingCalls: unknown[][] = [];
vi.mock('@/lib/queries/leave', () => ({
  useLeaveMonthSummary: (...args: unknown[]) => {
    pendingCalls.push(args);
    // pending_count (その月だけ) は使わない
    return { data: { pending_total: pendingTotal, pending_count: 0 } };
  },
}));

// メニューの「タスク管理」の未完了の件数 (patient-tasks-design §2-6)
let openTasks: unknown[] = [];
const taskCalls: unknown[][] = [];
vi.mock('@/lib/queries/tasks', () => ({
  useTasks: (...args: unknown[]) => {
    taskCalls.push(args);
    return { data: openTasks };
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
    openTasks = [];
    taskCalls.length = 0;
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

  it('4. 管理者のメニューの並び：患者 → 休み・勤務表 → 申請履歴 → 連携 → 監査ログ（PO 2026-11-01）', () => {
    setRole('admin');
    render(<Sidebar collapsed={false} />);
    const links = screen.getAllByRole('link').map((a) => a.textContent);
    const i = links.indexOf('患者');
    expect(links.slice(i, i + 5)).toEqual(['患者', '休み・勤務表', '申請履歴', '連携', '監査ログ']);
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
    expect(pendingCalls.every((c) => c[1] === false)).toBe(true);
  });

  it('6. 未処理の休みの申請の件数 (60 秒に 1 回まで)・折りたたみでは点', () => {
    setRole('admin');
    pendingTotal = 3;
    const { unmount } = render(<Sidebar collapsed={false} />);
    expect(pendingCalls[0]).toEqual([expect.stringMatching(/^\d{4}-\d{2}$/), true, 60_000]);
    const link = screen.getByRole('link', { name: /休み・勤務表/ });
    expect(link).toHaveTextContent('3');
    expect(link).toHaveTextContent('未処理の申請 3 件');
    unmount();
    render(<Sidebar collapsed />);
    expect(screen.getByTestId('nav-badge-dot')).toHaveTextContent('未処理の申請 3 件');
  });

  it('タスク管理: 管理者には訪問モニターの下に出し、未完了の件数をバッジに出す', () => {
    setRole('admin');
    openTasks = [{}, {}, {}];
    render(<Sidebar collapsed={false} />);
    const link = screen.getByRole('link', { name: /タスク管理/ });
    expect(link).toHaveAttribute('href', '/tasks');
    expect(link.textContent).toContain('3');
    const hrefs = screen.getAllByRole('link').map((a) => a.getAttribute('href'));
    expect(hrefs.indexOf('/tasks')).toBe(hrefs.indexOf('/monitor') + 1);
    expect(taskCalls[0]).toEqual([{ status: 'open' }, { enabled: true, refetchInterval: 60_000 }]);
  });

  it('タスク管理: 職員には出さず、読みにも行かない', () => {
    setRole('staff');
    render(<Sidebar collapsed={false} />);
    expect(screen.queryByRole('link', { name: /タスク管理/ })).toBeNull();
    expect(taskCalls[0]?.[1]).toEqual({ enabled: false, refetchInterval: 60_000 });
  });
});
