/**
 * BulkFixToPatternButton テスト (W9-FE2 Phase 4).
 *
 * ケース:
 *   1. canEdit=false (staff ロール相当) → ボタンが描画されない
 *   2. canEdit=true → ボタンをクリック → 確認ダイアログの「実行」で from-week-bulk が呼ばれる
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';

// ─── モック ──────────────────────────────────────────────────────────────────

const { mockMutateAsync, mockToast } = vi.hoisted(() => ({
  mockMutateAsync: vi.fn(),
  mockToast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock('@/lib/queries/patient_fixed_visits', () => ({
  useApplyFromWeekBulk: () => ({
    mutateAsync: mockMutateAsync,
    isPending: false,
  }),
}));

vi.mock('sonner', () => ({
  toast: mockToast,
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: null, status: 'unauthenticated' }),
}));

// shadcn/ui Dialog
vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({
    open,
    onOpenChange,
    children,
  }: {
    open: boolean;
    onOpenChange?: (v: boolean) => void;
    children: React.ReactNode;
  }) =>
    open ? (
      <div data-testid="dialog" onClick={() => onOpenChange?.(false)}>
        {children}
      </div>
    ) : null,
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
    size: _size,
    className: _className,
    'aria-label': ariaLabel,
  }: {
    children?: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    variant?: string;
    size?: string;
    className?: string;
    'aria-label'?: string;
  }) => (
    <button onClick={onClick} disabled={disabled} aria-label={ariaLabel}>
      {children}
    </button>
  ),
}));

// shadcn/ui Checkbox
vi.mock('@/components/ui/checkbox', () => ({
  Checkbox: ({
    id,
    checked,
    onCheckedChange,
  }: {
    id?: string;
    checked?: boolean;
    onCheckedChange?: (v: boolean) => void;
  }) => (
    <input
      id={id}
      type="checkbox"
      checked={checked}
      onChange={(e) => onCheckedChange?.(e.target.checked)}
    />
  ),
}));

// lucide-react
vi.mock('lucide-react', () => ({
  DatabaseZap: () => <span data-testid="icon-database-zap" />,
  X: () => <span />,
}));

// ─── Import ───────────────────────────────────────────────────────────────────

import { BulkFixToPatternButton } from '../BulkFixToPatternButton';

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('BulkFixToPatternButton (W9-FE2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. canEdit=false のとき (staff ロール相当) ボタンが描画されない', () => {
    const { container } = render(
      <BulkFixToPatternButton canEdit={false} isoYear={2026} isoWeek={20} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('2. canEdit=true → ボタン → ダイアログ → 「実行」で from-week-bulk が呼ばれる', async () => {
    mockMutateAsync.mockResolvedValueOnce({ updated_count: 5 });

    render(<BulkFixToPatternButton canEdit isoYear={2026} isoWeek={20} />);

    // ボタンが描画されている
    const openBtn = screen.getByRole('button', { name: '全患者の固定枠を一括保存' });
    expect(openBtn).toBeInTheDocument();

    // ボタンをクリックしてダイアログを開く
    fireEvent.click(openBtn);

    // ダイアログが表示される
    expect(screen.getByTestId('dialog')).toBeInTheDocument();

    // 「実行」ボタンをクリック
    const executeBtn = screen.getByText('実行');
    fireEvent.click(executeBtn);

    await waitFor(() => {
      expect(mockMutateAsync).toHaveBeenCalledOnce();
      expect(mockMutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({ iso_year: 2026, iso_week: 20 }),
      );
    });
  });
});
