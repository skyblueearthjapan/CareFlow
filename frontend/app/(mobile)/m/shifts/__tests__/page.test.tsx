/**
 * /m/shifts 出勤カレンダー — 表示のテスト (staff-shift-confirmation-design.md §5).
 *
 * 担保する約束:
 *   - 月タイトル・出勤/休みの集計・凡例が表示される
 *   - 確定行があれば「確定」バッジ、なければ「調整中」の案内
 *   - staffId 無しアカウントには destructive Alert
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

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

const SHIFTS = Array.from({ length: 7 }, (_, weekday) => ({
  weekday,
  is_on: weekday <= 4,
  start_time: weekday <= 4 ? '09:00' : null,
  end_time: weekday <= 4 ? '18:00' : null,
}));

let overrides: unknown[] = [];
let confirmations: unknown[] = [];
vi.mock('@/lib/queries/staff-shifts', () => ({
  useStaffShifts: () => ({ data: { shifts: SHIFTS }, isLoading: false }),
}));
vi.mock('@/lib/queries/staff-overrides', () => ({
  useStaffOverrides: () => ({ data: overrides, isLoading: false }),
}));
vi.mock('@/lib/queries/staff-shift-confirmations', () => ({
  useShiftConfirmations: () => ({ data: confirmations, isLoading: false }),
}));
vi.mock('@/lib/queries/pending_requests', () => ({
  usePendingRequests: () => ({
    data: { items: [], total: 0, limit: 100, offset: 0 },
    isLoading: false,
  }),
}));

import MobileShiftsPage from '../page';

beforeEach(() => {
  vi.clearAllMocks();
  sessionStaffId = 'staff-1';
  overrides = [];
  confirmations = [];
});

describe('MobileShiftsPage', () => {
  it('タイトル・月表示・凡例・集計を表示する', () => {
    render(<MobileShiftsPage />);
    expect(screen.getByText('出勤カレンダー')).toBeInTheDocument();
    const now = new Date();
    expect(
      screen.getByText(`${now.getFullYear()}年${now.getMonth() + 1}月`),
    ).toBeInTheDocument();
    expect(screen.getAllByText('出勤').length).toBeGreaterThan(0);
    expect(screen.getAllByText('休み').length).toBeGreaterThan(0);
  });

  it('未確定の月は「調整中」の案内を出す', () => {
    render(<MobileShiftsPage />);
    expect(screen.getByText(/調整中/)).toBeInTheDocument();
  });

  it('確定済みの月は確定バッジを出す', () => {
    confirmations = [
      {
        id: '00000000-0000-0000-0000-000000000001',
        staff_id: 'staff-1',
        month: '2026-09-01',
        confirmed_by: null,
        confirmed_at: '2026-08-18T03:00:00Z',
      },
    ];
    render(<MobileShiftsPage />);
    expect(screen.getByText(/確定$/)).toBeInTheDocument();
    expect(screen.queryByText(/調整中/)).not.toBeInTheDocument();
  });

  it('staffId 未紐付けアカウントには Alert を出す', () => {
    sessionStaffId = null;
    render(<MobileShiftsPage />);
    expect(screen.getByText('表示できません')).toBeInTheDocument();
  });
});
