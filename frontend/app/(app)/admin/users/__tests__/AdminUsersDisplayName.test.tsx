/**
 * /admin/users — 名前（画面に出す呼び名）と並び順 (PO 2026-10-13)。
 *
 *   - 一覧: 「名前」列・ログイン ID は大文字。名前が空なら「--」(既定は空)。
 *   - 並び順: S の ID (番号順) → A の ID (番号順) → ほかの ID → ID なし (メール順)。
 *   - 作成/編集ダイアログ: 名前を送る・空にすると null を送る。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

const { mockCreate, mockUpdate, hooks } = vi.hoisted(() => ({
  mockCreate: vi.fn(),
  mockUpdate: vi.fn(),
  hooks: { usersItems: [] as unknown[] },
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { id: 'me', role: 'admin' } }, status: 'authenticated' }),
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: vi.fn() }) }));
vi.mock('@/lib/queries/staff', () => ({ useStaffList: () => ({ data: [] }) }));
vi.mock('@/lib/queries/admin-users', () => ({
  useAdminUsers: () => ({
    data: { items: hooks.usersItems, total: hooks.usersItems.length },
    isLoading: false,
    isError: false,
    error: null,
  }),
  useResetAdminUserPassword: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteAdminUser: () => ({ mutate: vi.fn() }),
  useCreateAdminUser: () => ({
    mutate: (payload: unknown) => mockCreate(payload),
    reset: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
  useUpdateAdminUser: () => ({
    mutate: (vars: unknown) => mockUpdate(vars),
    isPending: false,
    isError: false,
    error: null,
  }),
}));

import AdminUsersPage from '../page';
import { UserCreateDialog } from '../_components/UserCreateDialog';
import { UserEditDialog } from '../_components/UserEditDialog';
import { compareAdminUsers } from '@/lib/schemas/admin-user';

function user(id: string, username: string | null, email: string | null, extra = {}) {
  return {
    id,
    email,
    username,
    display_name: null as string | null,
    staff_name: null,
    role: 'staff' as const,
    staff_id: null,
    must_change_password: false,
    failed_login_count: 0,
    created_at: '2026-01-01',
    updated_at: '2026-01-01',
    ...extra,
  };
}

describe('compareAdminUsers — 並び順', () => {
  it('S(番号順) → A(番号順) → ほかの ID → ID なし(メール順)。番号は数で比べる', () => {
    const items = [
      user('1', null, 'zz@x.jp'),
      user('2', 'a005', null),
      user('3', 's010', null),
      user('4', 'kanri', null),
      user('5', 's009', null),
      user('6', null, 'aa@x.jp'),
      user('7', 'a001', null),
      user('8', 's2', null),
      user('9', 's001', null),
    ];
    const order = [...items].sort(compareAdminUsers).map((u) => u.username ?? u.email);
    expect(order).toEqual([
      's001',
      's2',
      's009',
      's010',
      'a001',
      'a005',
      'kanri',
      'aa@x.jp',
      'zz@x.jp',
    ]);
  });
});

describe('AdminUsersPage — 名前の列', () => {
  beforeEach(() => {
    hooks.usersItems = [
      user('u-a', 'a001', 'boss@x.jp', { role: 'admin', display_name: '今泉 勇二' }),
      user('u-s', 's001', null),
    ];
  });

  it('名前の列を出し、ログイン ID は大文字・名前が空なら「--」。S が A より上', () => {
    render(<AdminUsersPage />);
    expect(screen.getByRole('columnheader', { name: '名前' })).toBeInTheDocument();
    const rows = screen.getAllByRole('row').slice(1);
    expect(within(rows[0]).getByText('S001')).toBeInTheDocument();
    expect(within(rows[0]).getAllByText('--').length).toBeGreaterThan(0);
    expect(within(rows[1]).getByText('A001')).toBeInTheDocument();
    expect(within(rows[1]).getByText('今泉 勇二')).toBeInTheDocument();
  });
});

describe('ダイアログ — 名前の入力', () => {
  beforeEach(() => {
    mockCreate.mockReset();
    mockUpdate.mockReset();
    hooks.usersItems = [];
  });

  it('作成: 名前を前後の空白を落として送る', () => {
    render(<UserCreateDialog open onOpenChange={() => {}} />);
    const name = screen.getByLabelText('名前（画面に出す呼び名）');
    expect(name).toHaveAttribute('placeholder', '例: 今泉 勇二');
    fireEvent.change(name, { target: { value: ' 山田 花子 ' } });
    fireEvent.change(screen.getByLabelText(/ログインID/), { target: { value: 's050' } });
    fireEvent.click(screen.getByRole('button', { name: '作成' }));
    expect(mockCreate).toHaveBeenCalledWith(
      expect.objectContaining({ username: 's050', display_name: '山田 花子' }),
    );
  });

  it('作成: 名前が空なら送らない', () => {
    render(<UserCreateDialog open onOpenChange={() => {}} />);
    fireEvent.change(screen.getByLabelText(/ログインID/), { target: { value: 's051' } });
    fireEvent.click(screen.getByRole('button', { name: '作成' }));
    expect(mockCreate.mock.calls[0][0].display_name).toBeUndefined();
  });

  it('編集: 空の名前に書き入れると送る・消すと null を送る', () => {
    const target = user('u-1', 's001', null);
    const { rerender } = render(<UserEditDialog target={target} onClose={() => {}} />);
    const name = screen.getByLabelText('名前（画面に出す呼び名）');
    expect(name).toHaveValue('');
    fireEvent.change(name, { target: { value: '川名 千恵' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    expect(mockUpdate).toHaveBeenLastCalledWith({
      id: 'u-1',
      payload: expect.objectContaining({ display_name: '川名 千恵' }),
    });

    const named = { ...target, display_name: '川名 千恵' };
    rerender(<UserEditDialog target={named} onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText('名前（画面に出す呼び名）'), {
      target: { value: '  ' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    expect(mockUpdate.mock.lastCall?.[0].payload.display_name).toBeNull();
  });

  it('編集: 名前を触らなければ送らない', () => {
    render(<UserEditDialog target={user('u-2', 's002', null)} onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    expect(mockUpdate.mock.lastCall?.[0].payload.display_name).toBeUndefined();
  });
});
