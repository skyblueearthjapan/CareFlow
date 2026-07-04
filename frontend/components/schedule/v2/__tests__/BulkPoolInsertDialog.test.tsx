/**
 * BulkPoolInsertDialog テスト (W-2 FE).
 *
 * 検証:
 *   1. 開くと simulate が自動発火し、プレビューが表示される。
 *   2. 「見せる」2: 必須チェックボックス gating — 未チェックで適用ボタン disabled、
 *      チェックで enabled。
 *   3. 409 (state_token 不一致): エラー toast + 「再計算」ボタンで simulate 再実行。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// ─── モック ──────────────────────────────────────────────────────────────────

const { mockToast, mocks } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  mocks: {
    simulateAsync: vi.fn(),
    applyAsync: vi.fn(),
    simulateReset: vi.fn(),
    applyReset: vi.fn(),
  },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { accessToken: 'tok', refreshToken: 'ref' },
    status: 'authenticated',
  }),
}));

vi.mock('@/lib/queries/poolBulk', () => ({
  usePoolBulkSimulateMutation: () => ({
    mutateAsync: mocks.simulateAsync,
    reset: mocks.simulateReset,
    isPending: false,
  }),
  usePoolBulkApplyMutation: () => ({
    mutateAsync: mocks.applyAsync,
    reset: mocks.applyReset,
    isPending: false,
  }),
}));

vi.mock('@/lib/queries/staff', () => ({
  useStaffList: () => ({ data: [] }),
}));

vi.mock('@/lib/queries/fieldBoard', () => ({
  proposeWarningLabel: (code: string) => code,
}));

vi.mock('../PoolCandidateList', () => ({
  EXCLUDED_REASON_LABEL: {} as Record<string, string>,
}));

vi.mock('../ProposalWeekCalendar', () => ({
  ProposalWeekCalendar: () => <div data-testid="proposal-week-calendar" />,
}));

vi.mock('../../WeekdayScheduleCard', () => ({
  WeekdayScheduleCard: () => <div data-testid="weekday-schedule-card" />,
}));

vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? <div data-testid="dialog">{children}</div> : null,
  DialogContent: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="dialog-content">{children}</div>
  ),
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
}));

vi.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    onClick,
    disabled,
    ...rest
  }: {
    children?: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    [k: string]: unknown;
  }) => (
    <button onClick={onClick} disabled={disabled} {...rest}>
      {children}
    </button>
  ),
}));

vi.mock('@/components/ui/checkbox', () => ({
  Checkbox: ({
    checked,
    onCheckedChange,
    ...rest
  }: {
    checked?: boolean;
    onCheckedChange?: (v: boolean) => void;
    [k: string]: unknown;
  }) => (
    <input
      type="checkbox"
      checked={!!checked}
      onChange={(e) => onCheckedChange?.(e.target.checked)}
      {...rest}
    />
  ),
}));

vi.mock('@/components/ui/tabs', () => ({
  Tabs: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsList: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsTrigger: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
  TabsContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/components/ui/alert', () => ({
  Alert: ({ children, ...rest }: { children: React.ReactNode; [k: string]: unknown }) => (
    <div {...rest}>{children}</div>
  ),
  AlertTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AlertDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/components/ui/badge', () => ({
  Badge: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));

vi.mock('lucide-react', () => ({
  AlertTriangle: () => <span />,
  CalendarRange: () => <span />,
  CheckCircle2: () => <span />,
  Layers: () => <span />,
  Loader2: () => <span data-testid="loader" />,
  RefreshCw: () => <span />,
}));

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

// ─── ヘルパー ────────────────────────────────────────────────────────────────

import { BulkPoolInsertDialog } from '../BulkPoolInsertDialog';
import { ApiError } from '@/lib/api-client';

const OFFICE_ID = '11111111-1111-4111-8111-111111111111';

const BASE_PROPS = {
  open: true,
  onClose: vi.fn(),
  isoYear: 2026,
  isoWeek: 27,
  officeId: OFFICE_ID,
  patientIds: ['p-1', 'p-2'],
} as const;

/** 1 枠投入できる最小 simulate レスポンス */
function makeSimulateResult() {
  return {
    placements: [
      {
        seq: 1,
        patient_id: 'p-1',
        patient_name: '患者 A',
        weekday: 0,
        course_code: 'B',
        office_id: OFFICE_ID,
        start_time: '10:15:00',
        service_minutes: 35,
        delta_minutes: 4,
        warnings: [],
      },
    ],
    partial: [],
    unplaced: [],
    week_before_after: [],
    kpi: {
      placed_patients: 1,
      placed_slots: 1,
      travel_minutes_before: 100,
      travel_minutes_after: 110,
      travel_km_before: 10,
      travel_km_after: 11,
    },
    state_token: 'token-abc',
  };
}

// ─── テスト ──────────────────────────────────────────────────────────────────

describe('BulkPoolInsertDialog (W-2)', () => {
  beforeEach(() => {
    mocks.simulateAsync.mockReset();
    mocks.applyAsync.mockReset();
    mocks.simulateReset.mockReset();
    mocks.applyReset.mockReset();
    mockToast.success.mockReset();
    mockToast.error.mockReset();
    mockToast.warning.mockReset();
    mockToast.info.mockReset();
  });

  it('開くと simulate が自動発火し、プレビューと適用ボタンが表示される', async () => {
    mocks.simulateAsync.mockResolvedValue(makeSimulateResult());
    render(<BulkPoolInsertDialog {...BASE_PROPS} />);

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );
    expect(mocks.simulateAsync).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('bulk-pool-insert-apply-button')).toBeInTheDocument();
  });

  it('チェックボックス gating: 未チェックで適用ボタン disabled、チェックで enabled', async () => {
    mocks.simulateAsync.mockResolvedValue(makeSimulateResult());
    render(<BulkPoolInsertDialog {...BASE_PROPS} />);

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );

    const applyButton = screen.getByTestId('bulk-pool-insert-apply-button');
    // 未チェック: disabled
    expect(applyButton).toBeDisabled();

    // チェック → enabled
    fireEvent.click(screen.getByTestId('bulk-pool-insert-confirm-checkbox'));
    expect(applyButton).not.toBeDisabled();
  });

  it('409: エラー toast + 「再計算」ボタンで simulate を再実行する', async () => {
    mocks.simulateAsync.mockResolvedValue(makeSimulateResult());
    mocks.applyAsync.mockRejectedValue(new ApiError(409, 'conflict'));
    render(<BulkPoolInsertDialog {...BASE_PROPS} />);

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );
    expect(mocks.simulateAsync).toHaveBeenCalledTimes(1);

    // チェックして適用
    fireEvent.click(screen.getByTestId('bulk-pool-insert-confirm-checkbox'));
    fireEvent.click(screen.getByTestId('bulk-pool-insert-apply-button'));

    // 409 → エラー toast + previewing に戻る
    await waitFor(() =>
      expect(mockToast.error).toHaveBeenCalledWith(
        'シミュレーション後にスケジュールが変更されました。再計算してください',
      ),
    );

    // 「再計算」ボタンが存在し、押すと simulate 再実行
    const recompute = screen.getByTestId('bulk-pool-insert-recompute-button');
    fireEvent.click(recompute);
    await waitFor(() => expect(mocks.simulateAsync).toHaveBeenCalledTimes(2));
  });
});
