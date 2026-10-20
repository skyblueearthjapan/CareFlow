/**
 * /admin/pending-requests 申請履歴 — 休みまわりの変更 (leave-and-kinmu-sheet-design §5-1).
 *
 * 担保する約束:
 *   - 一覧の上に「休みの申請 N 件・M月は未確定 → 休み・勤務表で開く」の帯 (/admin/leave)
 *   - 右の小さなカレンダー (StaffLeavePanel) は無い
 *   - 休みの行に種類 (午前休・変更のお願い) と「表で開く」(/admin/leave?staff=..&date=..)
 *   - 職員の絞り込みは StaffCombobox (選ぶと target_staff_id で読み直す)
 *   - 承認したら休みのデータも読み直し、成功のトーストを出す
 *
 * 「いま」は 2026-10-21 に固定 → 帯の月は 11 月。
 */
import * as React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import type * as ReactQueryModule from '@tanstack/react-query';
import type { UsePendingRequestsParams } from '@/lib/queries/pending_requests';

const qcStub = { invalidateQueries: vi.fn() };
vi.mock('@tanstack/react-query', async (importOriginal) => {
  const actual = await importOriginal<typeof ReactQueryModule>();
  return { ...actual, useQueryClient: () => qcStub };
});

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { role: 'admin' }, accessToken: 'a', refreshToken: 'r' },
    status: 'authenticated',
  }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

// StaffCombobox は useStaffList 依存のため差し替え (選択操作をボタンで模擬)
vi.mock('@/components/master/StaffCombobox', () => ({
  StaffCombobox: ({ value, onChange }: { value: string; onChange: (id: string) => void }) => (
    <button type="button" data-value={value} onClick={() => onChange('staff-1')}>
      __pick-staff__
    </button>
  ),
}));

vi.mock('@/lib/queries/staff', () => ({
  useStaffList: () => ({
    data: [
      { id: 'staff-1', name: '職員A', status: 'active', deleted_at: null },
      { id: 'staff-2', name: '職員B', status: 'active', deleted_at: null },
      {
        id: 'staff-3',
        name: '職員C',
        status: 'active',
        deleted_at: null,
        employment_type: 'part_time',
      },
      { id: 'staff-4', name: '職員D', status: 'retired', deleted_at: null },
    ],
  }),
}));
vi.mock('@/lib/queries/patients', () => ({
  usePatients: () => ({ data: { items: [] } }),
}));

// 帯の確定の状態は月の軽い情報 (GET /leave/month/{month}/summary) を 1 回だけ読む
interface SummaryStub {
  data?: { confirmed: boolean; confirmed_staff_count: number | null; staff_count: number | null };
  isError: boolean;
}
let summaryStub: SummaryStub = {
  data: { confirmed: false, confirmed_staff_count: 0, staff_count: 2 },
  isError: false,
};
const summaryCalls: unknown[][] = [];
vi.mock('@/lib/queries/leave', () => ({
  useLeaveMonthSummary: (...args: unknown[]) => {
    summaryCalls.push(args);
    return summaryStub;
  },
}));

const listCalls: UsePendingRequestsParams[] = [];
let listItems: unknown[] = [];
const approveMutate = vi.fn();
vi.mock('@/lib/queries/pending_requests', () => ({
  usePendingRequests: (params: UsePendingRequestsParams) => {
    listCalls.push(params);
    if (params.limit === 1) {
      return { data: { items: [], total: 3, limit: 1, offset: 0 }, isLoading: false };
    }
    return {
      data: { items: listItems, total: listItems.length, limit: 100, offset: 0 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    };
  },
  useApproveRequest: () => ({ mutate: approveMutate, isPending: false }),
  useRejectRequest: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { toast } from '@/components/ui/sonner';
import AdminPendingRequestsPage from '../page';

function leaveRow(id: string, date: string, payload: Record<string, unknown>) {
  return {
    id,
    request_type: 'staff_off',
    payload: { staff_id: 'staff-1', date, ...payload },
    edited_payload: null,
    target_staff_id: 'staff-1',
    target_patient_id: null,
    target_date: date,
    scope: null,
    status: 'pending',
    rejection_reason: null,
    requester_user_id: 'user-1',
    created_at: '2026-10-15T00:00:00Z',
    updated_at: '2026-10-15T00:00:00Z',
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-21T10:00:00+09:00'));
  listCalls.length = 0;
  summaryCalls.length = 0;
  listItems = [];
  summaryStub = {
    data: { confirmed: false, confirmed_staff_count: 0, staff_count: 2 },
    isError: false,
  };
});

afterEach(() => {
  vi.useRealTimers();
});

describe('AdminPendingRequestsPage — 休みの帯', () => {
  it('休みの申請の件数・来月は未確定・「休み・勤務表で開く」を出す', () => {
    render(<AdminPendingRequestsPage />);
    const band = screen.getByRole('region', { name: '休みの申請' });
    expect(band).toHaveTextContent('休みの申請 3 件');
    expect(band).toHaveTextContent('11月は未確定');
    expect(within(band).getByRole('link', { name: /休み・勤務表で開く/ })).toHaveAttribute(
      'href',
      '/admin/leave?month=2026-11',
    );
    // 申請中の staff_off の件数を読み、来月 (2026-11) の確定の状態は summary を 1 回だけ読む
    expect(listCalls).toContainEqual({ request_type: 'staff_off', status: 'pending', limit: 1 });
    expect(new Set(summaryCalls.map((c) => c[0]))).toEqual(new Set(['2026-11']));
    // 右の小さなカレンダーは無い
    expect(screen.queryByText('休み・月確定')).not.toBeInTheDocument();
  });

  it('一部だけ確定なら人数、全員確定なら「確定済み」', () => {
    summaryStub = {
      data: { confirmed: false, confirmed_staff_count: 1, staff_count: 2 },
      isError: false,
    };
    const { unmount } = render(<AdminPendingRequestsPage />);
    expect(screen.getByRole('region', { name: '休みの申請' })).toHaveTextContent(
      '11月は未確定（1/2 人確定）',
    );
    unmount();
    summaryStub = {
      data: { confirmed: true, confirmed_staff_count: 2, staff_count: 2 },
      isError: false,
    };
    render(<AdminPendingRequestsPage />);
    expect(screen.getByRole('region', { name: '休みの申請' })).toHaveTextContent('11月は確定済み');
  });

  it('確定の状態を読めなかったら「読み込めませんでした」と出す', () => {
    summaryStub = { isError: true };
    render(<AdminPendingRequestsPage />);
    const band = screen.getByRole('region', { name: '休みの申請' });
    expect(band).toHaveTextContent('11月の確定の状態を読み込めませんでした');
    expect(band).not.toHaveTextContent('未確定');
  });
});

describe('AdminPendingRequestsPage — 休みの行', () => {
  it('種類と変更のお願いの印、「表で開く」のリンクを出す', () => {
    listItems = [
      leaveRow('req-1', '2026-11-12', { override_type: 'am_off' }),
      leaveRow('req-2', '2026-11-13', { override_type: 'cancel', kind: 'change', note: '用事' }),
      leaveRow('req-3', '2026-11-16', {}),
    ];
    render(<AdminPendingRequestsPage />);
    const rows = screen.getAllByRole('row').slice(1);
    expect(rows[0]).toHaveTextContent('午前休');
    expect(within(rows[0]!).queryByText('変更のお願い')).not.toBeInTheDocument();
    expect(rows[1]).toHaveTextContent('休みの取り消し');
    expect(within(rows[1]!).getByText('変更のお願い')).toBeInTheDocument();
    // 旧い申請 (override_type なし) は公休
    expect(rows[2]).toHaveTextContent('公休');
    expect(within(rows[0]!).getByRole('link', { name: '表で開く' })).toHaveAttribute(
      'href',
      '/admin/leave?staff=staff-1&date=2026-11-12',
    );
  });

  it('職員の絞り込みは StaffCombobox で、選ぶと target_staff_id で読み直す', () => {
    render(<AdminPendingRequestsPage />);
    expect(screen.queryByLabelText('対象スタッフ ID')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '__pick-staff__' }));
    const main = listCalls.filter((p) => p.limit !== 1);
    expect(main[main.length - 1]).toMatchObject({ target_staff_id: 'staff-1' });
  });

  it('承認したら休みのデータを読み直し、成功のトーストを出す (元に戻すは無い)', () => {
    listItems = [leaveRow('req-1', '2026-11-12', { override_type: 'pm_off' })];
    approveMutate.mockImplementation((_id: string, opts: { onSuccess?: () => void }) =>
      opts.onSuccess?.(),
    );
    render(<AdminPendingRequestsPage />);
    fireEvent.click(screen.getByRole('button', { name: '承認' }));

    expect(approveMutate).toHaveBeenCalledWith('req-1', expect.anything());
    expect(toast.success).toHaveBeenCalledWith('承認しました（職員Aさん 11/12（木） 午後休）');
    expect(qcStub.invalidateQueries).toHaveBeenCalledWith({ queryKey: ['staff-overrides'] });
    expect(qcStub.invalidateQueries).toHaveBeenCalledWith({ queryKey: ['staff-overrides-week'] });
  });
});
