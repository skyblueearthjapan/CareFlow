/**
 * P2 — staff linkage UI for `/admin/users` create / edit dialogs.
 *
 * Covers:
 *   - create: linked staff disabled, picking staff auto-fills username, re-picking
 *     updates auto-fill, manual edit locks the value, payload carries correct fields.
 *   - create: admin/manager role → email required; payload has no username/staff_id when unused.
 *   - edit: unlink sends staff_id:null; both-empty warns/blocks.
 *   - edit: role change triggers constraints (staff→username, admin/manager→email).
 *   - edit: diff-send; staff change sends new staff_id.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const { mockCreate, mockUpdate, hooks } = vi.hoisted(() => ({
  mockCreate: vi.fn(),
  mockUpdate: vi.fn(),
  hooks: {
    staffList: [
      { id: 'staff-1', name: '川名 千恵', code: 'S001', deleted_at: null },
      { id: 'staff-2', name: '佐藤 花子', code: 'S002', deleted_at: null },
      { id: 'staff-3', name: '田中 一郎', code: 'S003', deleted_at: null },
    ] as unknown[],
    usersItems: [
      { id: 'user-a', email: 'a@x.jp', username: null, role: 'admin', staff_id: 'staff-1' },
    ] as unknown[],
  },
}));

vi.mock('@/lib/queries/staff', () => ({
  useStaffList: () => ({ data: hooks.staffList }),
}));

vi.mock('@/lib/queries/admin-users', () => ({
  useAdminUsers: () => ({ data: { items: hooks.usersItems, total: hooks.usersItems.length } }),
  useCreateAdminUser: (opts: { onSuccess?: (r: unknown) => void } = {}) => ({
    mutate: (payload: unknown) => mockCreate(payload, opts),
    reset: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
  useUpdateAdminUser: (opts: { onSuccess?: () => void } = {}) => ({
    mutate: (vars: unknown) => mockUpdate(vars, opts),
    isPending: false,
    isError: false,
    error: null,
  }),
}));

import { UserCreateDialog } from '../UserCreateDialog';
import { UserEditDialog } from '../UserEditDialog';

// ---------------------------------------------------------------------------
// Shared fixtures
// ---------------------------------------------------------------------------

const managerTarget = {
  id: 'user-b',
  email: 'b@x.jp',
  username: null,
  staff_name: '佐藤 花子',
  role: 'manager' as const,
  staff_id: 'staff-2',
  must_change_password: false,
  failed_login_count: 0,
  created_at: '2026-01-01',
  updated_at: '2026-01-01',
};

// ---------------------------------------------------------------------------
// UserCreateDialog
// ---------------------------------------------------------------------------

describe('UserCreateDialog — P2 staff linkage', () => {
  beforeEach(() => mockCreate.mockReset());

  it('disables already-linked staff and auto-fills username from staff code', () => {
    render(<UserCreateDialog open onOpenChange={() => {}} />);

    const linked = screen.getByRole('option', { name: /S001/ }) as HTMLOptionElement;
    expect(linked.disabled).toBe(true);

    const select = screen.getByLabelText('スタッフ紐付け');
    fireEvent.change(select, { target: { value: 'staff-2' } });

    const username = screen.getByLabelText(/ログインID/) as HTMLInputElement;
    expect(username.value).toBe('S002');
  });

  it('re-picking a different staff overwrites the auto-filled username', () => {
    render(<UserCreateDialog open onOpenChange={() => {}} />);

    const select = screen.getByLabelText('スタッフ紐付け');
    fireEvent.change(select, { target: { value: 'staff-2' } });

    const usernameEl = screen.getByLabelText(/ログインID/) as HTMLInputElement;
    expect(usernameEl.value).toBe('S002');

    // Change to staff-3 — should overwrite because it was auto-filled.
    fireEvent.change(select, { target: { value: 'staff-3' } });
    expect(usernameEl.value).toBe('S003');
  });

  it('manual username edit is not overwritten on staff re-selection', () => {
    render(<UserCreateDialog open onOpenChange={() => {}} />);

    const select = screen.getByLabelText('スタッフ紐付け');
    const usernameEl = screen.getByLabelText(/ログインID/) as HTMLInputElement;

    // Auto-fill S002.
    fireEvent.change(select, { target: { value: 'staff-2' } });
    expect(usernameEl.value).toBe('S002');

    // Manual edit — sets autoFilledRef to false.
    fireEvent.change(usernameEl, { target: { value: 'custom' } });

    // Change staff — should NOT overwrite manual value.
    fireEvent.change(select, { target: { value: 'staff-3' } });
    expect(usernameEl.value).toBe('custom');
  });

  it('submits payload with username and staff_id (staff role)', () => {
    render(<UserCreateDialog open onOpenChange={() => {}} />);

    fireEvent.change(screen.getByLabelText('スタッフ紐付け'), { target: { value: 'staff-2' } });
    fireEvent.click(screen.getByRole('button', { name: '作成' }));

    expect(mockCreate).toHaveBeenCalledTimes(1);
    const payload = mockCreate.mock.calls[0][0];
    expect(payload).toMatchObject({ role: 'staff', username: 'S002', staff_id: 'staff-2' });
    expect(payload.email).toBeUndefined();
  });

  it('admin role: email required label shown, submit blocked without email', () => {
    render(<UserCreateDialog open onOpenChange={() => {}} />);

    fireEvent.change(screen.getByLabelText('ロール'), { target: { value: 'admin' } });

    expect(screen.getByLabelText(/メールアドレス（必須）/)).toBeInTheDocument();
    // email is empty → submit disabled.
    expect(screen.getByRole('button', { name: '作成' })).toBeDisabled();
  });

  it('admin/manager role: payload has no username or staff_id when not set', () => {
    render(<UserCreateDialog open onOpenChange={() => {}} />);

    fireEvent.change(screen.getByLabelText('ロール'), { target: { value: 'manager' } });
    fireEvent.change(screen.getByLabelText(/メールアドレス/), {
      target: { value: 'mgr@x.jp' },
    });
    fireEvent.click(screen.getByRole('button', { name: '作成' }));

    expect(mockCreate).toHaveBeenCalledTimes(1);
    const payload = mockCreate.mock.calls[0][0];
    expect(payload).toMatchObject({ role: 'manager', email: 'mgr@x.jp' });
    expect(payload.username).toBeUndefined();
    expect(payload.staff_id).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// UserEditDialog
// ---------------------------------------------------------------------------

describe('UserEditDialog — P2 staff linkage', () => {
  beforeEach(() => mockUpdate.mockReset());

  it('sends staff_id:null when unlinking', () => {
    render(<UserEditDialog target={managerTarget} onClose={() => {}} />);

    fireEvent.change(screen.getByLabelText('スタッフ紐付け'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    expect(mockUpdate).toHaveBeenCalledTimes(1);
    const vars = mockUpdate.mock.calls[0][0];
    expect(vars.id).toBe('user-b');
    expect(vars.payload.staff_id).toBeNull();
  });

  it('warns and blocks submit when email and username are both empty', () => {
    render(<UserEditDialog target={managerTarget} onClose={() => {}} />);

    fireEvent.change(screen.getByLabelText('メール'), { target: { value: '' } });

    expect(screen.getByText(/ログインできなくなります/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
  });

  it('diff-send: only changed fields in payload (role only change)', () => {
    render(<UserEditDialog target={managerTarget} onClose={() => {}} />);

    // Change role to admin — need to keep email so constraint not violated.
    fireEvent.change(screen.getByLabelText('ロール'), { target: { value: 'admin' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    expect(mockUpdate).toHaveBeenCalledTimes(1);
    const { payload } = mockUpdate.mock.calls[0][0];
    expect(payload.role).toBe('admin');
    // Unchanged fields should be undefined (diff-send pattern).
    expect(payload.email).toBeUndefined();
    expect(payload.username).toBeUndefined();
    expect(payload.must_change_password).toBeUndefined();
    // staff unchanged → undefined.
    expect(payload.staff_id).toBeUndefined();
  });

  it('role change to staff with no username blocks submit and shows message', () => {
    // Target has no username, role is manager → change to staff.
    render(<UserEditDialog target={managerTarget} onClose={() => {}} />);

    fireEvent.change(screen.getByLabelText('ロール'), { target: { value: 'staff' } });

    expect(screen.getByText(/ログインIDが必須です/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
  });

  it('role change to admin/manager with no email blocks submit and shows message', () => {
    // Target has email. Clear it, then switch role to admin.
    render(<UserEditDialog target={{ ...managerTarget, role: 'staff', email: null, username: 'S002' }} onClose={() => {}} />);

    fireEvent.change(screen.getByLabelText('ロール'), { target: { value: 'admin' } });

    expect(screen.getByText(/メールアドレスが必須です/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
  });

  it('changing linked staff sends new staff_id (not null)', () => {
    render(<UserEditDialog target={managerTarget} onClose={() => {}} />);

    // staff-2 → staff-3.
    fireEvent.change(screen.getByLabelText('スタッフ紐付け'), { target: { value: 'staff-3' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    expect(mockUpdate).toHaveBeenCalledTimes(1);
    const { payload } = mockUpdate.mock.calls[0][0];
    expect(payload.staff_id).toBe('staff-3');
  });
});
