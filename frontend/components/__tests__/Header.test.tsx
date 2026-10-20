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
let notifItems: unknown[] = [];
const markReadMutate = vi.fn();
vi.mock('@/lib/queries/notifications', () => ({
  useNotifications: () => ({
    data: { items: notifItems },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useMarkRead: () => ({ mutate: markReadMutate }),
  useMarkAllRead: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { useSession } from 'next-auth/react';
import { Header, leaveSheetHrefFromNotice } from '../Header';

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

  it('5. 休みで担当なしへ移した通知は、管理者に「休み・勤務表で開く」を出す', async () => {
    notifItems = [
      {
        id: 'n-1',
        user_id: 'u-1',
        type: 'leave_unassigned',
        title: '職員Aさんの11/12（木）の訪問を未割り当てとしました',
        body: null,
        read_at: null,
        created_at: '2026-10-21T01:00:00Z',
      },
      {
        id: 'n-2',
        user_id: 'u-1',
        type: 'visit_assigned',
        title: 'ほかの通知',
        body: null,
        read_at: null,
        created_at: new Date().toISOString(),
      },
    ];
    setRole('admin');
    const { unmount } = render(<Header onToggleSidebar={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '通知' }));
    const links = await screen.findAllByRole('link', { name: '休み・勤務表で開く' });
    expect(links).toHaveLength(1);
    // 題名の日付の月と日で開く
    expect(links[0]).toHaveAttribute('href', '/admin/leave?month=2026-11&date=2026-11-12');
    // jsdom は画面の移動をしないので、押したときの既定の動きだけ止める
    const stop = (e: Event) => e.preventDefault();
    document.addEventListener('click', stop);
    fireEvent.click(links[0]!);
    document.removeEventListener('click', stop);
    expect(markReadMutate).toHaveBeenCalledWith('n-1');
    unmount();

    // 職員には出さない
    setRole('staff');
    render(<Header onToggleSidebar={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '通知' }));
    expect(await screen.findByText('ほかの通知')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '休み・勤務表で開く' })).not.toBeInTheDocument();
    notifItems = [];
  });

  it('6. 通知の日付から開く月を決める (年は通知の時刻から・年をまたぐ)', () => {
    const at = (created_at: string, title: string, body: string | null = null) =>
      leaveSheetHrefFromNotice({ title, body, created_at });
    // 本文の最初の日付 (まとめた通知)
    expect(
      at(
        '2026-12-20T01:00:00Z',
        '休みで訪問4件を未割り当てとしました',
        '（職員Aさん 1/5 3件・職員Bさん 1/6 1件）',
      ),
    ).toBe('/admin/leave?month=2027-01&date=2027-01-05');
    expect(at('2027-01-03T01:00:00Z', '職員Aさんの12/28（月）の訪問を未割り当てとしました')).toBe(
      '/admin/leave?month=2026-12&date=2026-12-28',
    );
    expect(at('2026-10-21T01:00:00Z', '日付の無い通知')).toBe('/admin/leave');
  });

  it('7. 通知の data (staff_id・date) があればその人・その日のマスを開く', () => {
    expect(
      leaveSheetHrefFromNotice({
        title: '職員Aさんから休みの申請（11/21 午後休）',
        body: null,
        created_at: '2026-10-21T01:00:00Z',
        data: { staff_id: 's-1', date: '2026-11-21' },
      }),
    ).toBe('/admin/leave?month=2026-11&date=2026-11-21&staff=s-1');
  });

  it('8. 新しい申請・承認の通知にも管理者に「休み・勤務表で開く」を出す', async () => {
    notifItems = ['leave_requested', 'leave_approved', 'leave_rejected'].map((type, i) => ({
      id: `n-${i}`,
      user_id: 'u-1',
      type,
      title: `通知${i}`,
      body: null,
      read_at: null,
      created_at: '2026-10-21T01:00:00Z',
      data: { staff_id: 's-1', date: '2026-11-0' + String(i + 1) },
    }));
    setRole('admin');
    render(<Header onToggleSidebar={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '通知' }));
    const links = await screen.findAllByRole('link', { name: '休み・勤務表で開く' });
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      '/admin/leave?month=2026-11&date=2026-11-01&staff=s-1',
      '/admin/leave?month=2026-11&date=2026-11-02&staff=s-1',
    ]);
    notifItems = [];
  });
});
