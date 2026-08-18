/**
 * /admin/staff-leave — スタッフ休み・月確定の smoke テスト.
 *
 * 担保する約束:
 *   - スタッフ未選択では案内を出す
 *   - スタッフ選択済みなら カレンダー/確定カード/申請中一覧/登録済み一覧 を表示
 *   - 申請中の行に承認/却下ボタンが出て、承認クリックで mutateAsync が飛ぶ
 *   - staff ロールは /dashboard へリダイレクトされる
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const routerReplace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: routerReplace, push: vi.fn() }),
}));

let sessionRole = 'admin';
vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { role: sessionRole }, accessToken: 'a', refreshToken: 'r' },
    status: 'authenticated',
  }),
}));

import type * as ReactQueryModule from '@tanstack/react-query';

const qcStub = { invalidateQueries: vi.fn() };
vi.mock('@tanstack/react-query', async (importOriginal) => {
  const actual = await importOriginal<typeof ReactQueryModule>();
  return { ...actual, useQueryClient: () => qcStub };
});

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

// StaffCombobox は useStaffList 依存のため差し替え (選択操作をボタンで模擬)
vi.mock('@/components/master/StaffCombobox', () => ({
  StaffCombobox: ({ onChange }: { onChange: (id: string) => void }) => (
    <button onClick={() => onChange('staff-1')}>__pick-staff__</button>
  ),
}));

const SHIFTS = Array.from({ length: 7 }, (_, weekday) => ({
  weekday,
  is_on: true,
  start_time: '09:00',
  end_time: '18:00',
}));

const approveMutateAsync = vi.fn().mockResolvedValue({});
const rejectMutateAsync = vi.fn().mockResolvedValue({});
let pendingItems: unknown[] = [];
vi.mock('@/lib/queries/pending_requests', () => ({
  usePendingRequests: () => ({
    data: { items: pendingItems, total: pendingItems.length, limit: 100, offset: 0 },
    isLoading: false,
  }),
  useApproveRequest: () => ({ mutateAsync: approveMutateAsync, isPending: false }),
  useRejectRequest: () => ({ mutateAsync: rejectMutateAsync, isPending: false }),
}));

let overrides: unknown[] = [];
vi.mock('@/lib/queries/staff-overrides', () => ({
  staffOverridesScopeKey: (id: string) => ['staff-overrides', id],
  useStaffOverrides: () => ({ data: overrides, isLoading: false }),
  useCreateOverride: () => ({ mutateAsync: vi.fn().mockResolvedValue({}), isPending: false }),
  useDeleteOverride: () => ({ mutateAsync: vi.fn().mockResolvedValue(undefined), isPending: false }),
}));
vi.mock('@/lib/queries/staff-shifts', () => ({
  useStaffShifts: () => ({ data: { shifts: SHIFTS }, isLoading: false }),
}));
vi.mock('@/lib/queries/staff-shift-confirmations', () => ({
  useShiftConfirmations: () => ({ data: [], isLoading: false }),
  useConfirmShiftMonth: () => ({ mutateAsync: vi.fn().mockResolvedValue({}), isPending: false }),
}));

import { fmtIsoLocal, startOfMonth } from '@/lib/shift-calendar';
import AdminStaffLeavePage from '../page';

function thisMonthDay15(): string {
  const first = startOfMonth(new Date());
  return fmtIsoLocal(new Date(first.getFullYear(), first.getMonth(), 15));
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionRole = 'admin';
  pendingItems = [];
  overrides = [];
});

describe('AdminStaffLeavePage', () => {
  it('スタッフ未選択では案内を表示する', () => {
    render(<AdminStaffLeavePage />);
    expect(screen.getByText('スタッフ休み・月確定')).toBeInTheDocument();
    expect(screen.getByText(/スタッフを選択すると/)).toBeInTheDocument();
  });

  it('スタッフ選択で確定カードと一覧が表示され、承認が飛ぶ', async () => {
    pendingItems = [
      {
        id: 'req-1',
        request_type: 'staff_off',
        payload: { note: '通院' },
        target_staff_id: 'staff-1',
        target_date: thisMonthDay15(),
        status: 'pending',
        requester_user_id: 'user-1',
        created_at: '2026-08-18T00:00:00Z',
        updated_at: '2026-08-18T00:00:00Z',
      },
    ];
    render(<AdminStaffLeavePage />);
    fireEvent.click(screen.getByText('__pick-staff__'));

    expect(await screen.findByText(/の確定$/)).toBeInTheDocument();
    expect(screen.getByText(/申請中の休み（1件）/)).toBeInTheDocument();
    expect(screen.getByText(/登録済みの休み・時間変更/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '承認' }));
    await waitFor(() => expect(approveMutateAsync).toHaveBeenCalledWith('req-1'));
  });

  it('staff ロールは /dashboard へリダイレクトする', () => {
    sessionRole = 'staff';
    render(<AdminStaffLeavePage />);
    expect(routerReplace).toHaveBeenCalledWith('/dashboard');
  });
});
