/**
 * ScheduleChangeDialog テスト (W9-FE2 Phase 4).
 *
 * ケース:
 *   1. ラジオボタン初期値 = this_week_only
 *   2. pattern_change 選択 → useFixOrPattern が pattern_change で呼ばれる
 *   3. isSpecialWeek=true で「特別週の固定枠を変更」テキスト表示
 *   4. API 失敗 → toast.error が呼ばれる
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';

// ─── モック ──────────────────────────────────────────────────────────────────

const { mockMutateAsync, mockIsPending, mockToast } = vi.hoisted(() => ({
  mockMutateAsync: vi.fn(),
  mockIsPending: { value: false },
  mockToast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock('@/lib/queries/schedule_fix_or_pattern', () => ({
  useFixOrPattern: () => ({
    mutateAsync: mockMutateAsync,
    get isPending() {
      return mockIsPending.value;
    },
  }),
}));

vi.mock('sonner', () => ({
  toast: mockToast,
}));

// next-auth
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: null, status: 'unauthenticated' }),
}));

// shadcn/ui Dialog — ポータルを避けるためシンプルなラッパーに差し替え
vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({
    open,
    children,
  }: {
    open: boolean;
    onOpenChange?: (v: boolean) => void;
    children: React.ReactNode;
  }) => (open ? <div data-testid="dialog">{children}</div> : null),
  DialogContent: ({ children }: { children: React.ReactNode; 'aria-describedby'?: string }) => (
    <div data-testid="dialog-content">{children}</div>
  ),
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  DialogDescription: ({ children, id }: { children: React.ReactNode; id?: string }) => (
    <p id={id}>{children}</p>
  ),
}));

// shadcn/ui Button
vi.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    onClick,
    disabled,
    variant: _variant,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    variant?: string;
  }) => (
    <button onClick={onClick} disabled={disabled}>
      {children}
    </button>
  ),
}));

// lucide-react
vi.mock('lucide-react', () => ({
  X: () => <span />,
}));

// ─── Import ───────────────────────────────────────────────────────────────────

import { ScheduleChangeDialog } from '../ScheduleChangeDialog';

// ─── 共通 Props ──────────────────────────────────────────────────────────────

const baseProps = {
  open: true,
  visitId: '00000000-0000-0000-0000-000000000001',
  patientName: '山田 太郎',
  isSpecialWeek: false,
  newWeekday: 1,
  newStartTime: '09:00',
  newDurationMin: 60,
  isoYear: 2026,
  isoWeek: 20,
  onClose: vi.fn(),
  onApplied: vi.fn(),
};

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('ScheduleChangeDialog (W9-FE2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockIsPending.value = false;
  });

  it('1. ラジオボタン初期値が this_week_only であること', () => {
    render(<ScheduleChangeDialog {...baseProps} />);

    const thisWeekRadio = screen.getByDisplayValue('this_week_only') as HTMLInputElement;
    const patternRadio = screen.getByDisplayValue('pattern_change') as HTMLInputElement;

    expect(thisWeekRadio.checked).toBe(true);
    expect(patternRadio.checked).toBe(false);
  });

  it('2. pattern_change を選択して適用 → mutateAsync が pattern_change で呼ばれる', async () => {
    mockMutateAsync.mockResolvedValueOnce({});

    render(<ScheduleChangeDialog {...baseProps} />);

    // pattern_change を選択
    const patternRadio = screen.getByDisplayValue('pattern_change');
    fireEvent.click(patternRadio);

    // 適用ボタンを押す
    const applyBtn = screen.getByText('適用');
    fireEvent.click(applyBtn);

    await waitFor(() => {
      expect(mockMutateAsync).toHaveBeenCalledOnce();
      expect(mockMutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({ mode: 'pattern_change' }),
      );
    });

    // 成功 toast
    expect(mockToast.success).toHaveBeenCalledWith('固定枠を更新しました');
  });

  it('3. isSpecialWeek=true かつ pattern_change 選択時に「特別週の固定枠を変更」が表示される', () => {
    render(<ScheduleChangeDialog {...baseProps} isSpecialWeek />);

    // pattern_change を選択
    const patternRadio = screen.getByDisplayValue('pattern_change');
    fireEvent.click(patternRadio);

    expect(screen.getByText('※特別週の固定枠を変更')).toBeInTheDocument();
  });

  it('4. API 失敗 → toast.error が呼ばれる', async () => {
    mockMutateAsync.mockRejectedValueOnce(new Error('サーバーエラー'));

    render(<ScheduleChangeDialog {...baseProps} />);

    const applyBtn = screen.getByText('適用');
    fireEvent.click(applyBtn);

    await waitFor(() => {
      expect(mockToast.error).toHaveBeenCalledWith('サーバーエラー');
    });
  });
});
