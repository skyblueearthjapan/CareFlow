/**
 * /m/leave 休み申請 — 送信フローと状態表示のテスト (mobile-leave-request-design.md §3).
 *
 * 担保する約束:
 *   - 日付未選択では申請ボタンが disabled
 *   - 選択日ぶんの POST が payload.override_type='off' (DB 正典) で飛ぶ
 *   - staffId が無いアカウントには destructive Alert を出し申請させない
 *   - 申請中 (pending) の一覧が表示され、取り下げボタンが DELETE を呼ぶ
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

let sessionStaffId: string | null = 'staff-1';
vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: {
      user: { staffId: sessionStaffId, role: 'staff' },
      accessToken: 'a',
      refreshToken: 'r',
    },
    status: 'authenticated',
  }),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

const createMutateAsync = vi.fn();
const withdrawMutateAsync = vi.fn();
let pendingItems: unknown[] = [];
vi.mock('@/lib/queries/pending_requests', () => ({
  usePendingRequests: () => ({
    data: { items: pendingItems, total: pendingItems.length, limit: 100, offset: 0 },
    isLoading: false,
  }),
  useCreatePendingRequest: () => ({ mutateAsync: createMutateAsync, isPending: false }),
  useWithdrawPendingRequest: () => ({ mutateAsync: withdrawMutateAsync, isPending: false }),
}));

let overrides: unknown[] = [];
vi.mock('@/lib/queries/staff-overrides', () => ({
  useStaffOverrides: () => ({ data: overrides, isLoading: false }),
}));

import { toast } from '@/components/ui/sonner';
import MobileLeavePage from '../page';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

function pendingRow(id: string, targetDate: string, note?: string) {
  return {
    id,
    request_type: 'staff_off',
    payload: { staff_id: 'staff-1', date: targetDate, override_type: 'off', note },
    target_staff_id: 'staff-1',
    target_date: targetDate,
    status: 'pending',
    requester_user_id: 'user-1',
    created_at: '2026-08-18T00:00:00Z',
    updated_at: '2026-08-18T00:00:00Z',
  };
}

/** カレンダーから「今日以降の申請可能な日」を1つタップする。 */
function clickFirstSelectableDay() {
  const enabled = document.querySelectorAll<HTMLButtonElement>(
    'button[name="day"]:not(:disabled)',
  );
  expect(enabled.length).toBeGreaterThan(0);
  fireEvent.click(enabled[enabled.length - 1]!);
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStaffId = 'staff-1';
  pendingItems = [];
  overrides = [];
  createMutateAsync.mockResolvedValue({});
  withdrawMutateAsync.mockResolvedValue(undefined);
});

describe('MobileLeavePage', () => {
  it('タイトルとカレンダーを表示し、未選択では申請ボタンが disabled', () => {
    render(<MobileLeavePage />);
    expect(screen.getByText('休み申請')).toBeInTheDocument();
    const btn = screen.getByRole('button', { name: /日付を選んでください/ });
    expect(btn).toBeDisabled();
  });

  it('日付を選ぶと選択日数つきボタンになり、申請で override_type=off の POST が飛ぶ', async () => {
    render(<MobileLeavePage />);
    clickFirstSelectableDay();

    const btn = await screen.findByRole('button', { name: /この内容で申請する（1日）/ });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);

    await waitFor(() => expect(createMutateAsync).toHaveBeenCalledTimes(1));
    const arg = createMutateAsync.mock.calls[0]![0] as {
      request_type: string;
      target_staff_id: string;
      target_date: string;
      payload: Record<string, unknown>;
    };
    expect(arg.request_type).toBe('staff_off');
    expect(arg.target_staff_id).toBe('staff-1');
    expect(arg.payload.override_type).toBe('off');
    expect(arg.payload.date).toBe(arg.target_date);
    await waitFor(() => expect(asMock(toast.success)).toHaveBeenCalled());
  });

  it('staffId 未紐付けアカウントには Alert を出し申請ボタンを disabled にする', () => {
    sessionStaffId = null;
    render(<MobileLeavePage />);
    expect(screen.getByText('申請できません')).toBeInTheDocument();
  });

  it('申請中の一覧を表示し、取り下げ確認 OK で DELETE を呼ぶ', async () => {
    pendingItems = [pendingRow('req-1', '2026-08-25', '通院のため')];
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<MobileLeavePage />);

    expect(screen.getByText('通院のため')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /取り下げ/ }));

    await waitFor(() => expect(withdrawMutateAsync).toHaveBeenCalledWith('req-1'));
    expect(confirmSpy).toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('取り下げ確認をキャンセルすると DELETE を呼ばない', async () => {
    pendingItems = [pendingRow('req-2', '2026-08-26')];
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<MobileLeavePage />);

    fireEvent.click(screen.getByRole('button', { name: /取り下げ/ }));
    expect(withdrawMutateAsync).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});
