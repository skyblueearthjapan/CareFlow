/**
 * WeeklyRitualGuideDialog — smoke test (P3-⑥).
 *
 * 検証:
 *   1. open=true でダイアログが描画される
 *   2. open=false では描画されない
 *   3. 3ステップが全て描画される
 *   4. 「閉じる」ボタンで onClose が呼ばれる
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

// ─── モック ──────────────────────────────────────────────────────────────────

vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? <div data-testid="dialog">{children}</div> : null,
  DialogContent: ({
    children,
    ...rest
  }: {
    children: React.ReactNode;
    [k: string]: unknown;
  }) => (
    <div data-testid="dialog-content" {...rest}>
      {children}
    </div>
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
    ...rest
  }: {
    children?: React.ReactNode;
    onClick?: () => void;
    [k: string]: unknown;
  }) => (
    <button onClick={onClick} {...rest}>
      {children}
    </button>
  ),
}));

vi.mock('lucide-react', () => ({
  ListChecks: () => <span data-testid="icon-list-checks" />,
}));

// ─── Subject under test ─────────────────────────────────────────────────────

import { WeeklyRitualGuideDialog } from '../WeeklyRitualGuideDialog';

// ─── Tests ──────────────────────────────────────────────────────────────────

describe('WeeklyRitualGuideDialog (P3-⑥)', () => {
  it('1. open=true でダイアログが描画される', () => {
    render(<WeeklyRitualGuideDialog open={true} onClose={vi.fn()} />);
    expect(screen.getByTestId('weekly-ritual-guide-dialog')).toBeInTheDocument();
  });

  it('2. open=false では描画されない', () => {
    render(<WeeklyRitualGuideDialog open={false} onClose={vi.fn()} />);
    expect(screen.queryByTestId('weekly-ritual-guide-dialog')).not.toBeInTheDocument();
  });

  it('3. 3ステップが全て描画される', () => {
    render(<WeeklyRitualGuideDialog open={true} onClose={vi.fn()} />);
    expect(screen.getByTestId('weekly-ritual-steps')).toBeInTheDocument();
    expect(screen.getByTestId('weekly-ritual-step-1')).toBeInTheDocument();
    expect(screen.getByTestId('weekly-ritual-step-2')).toBeInTheDocument();
    expect(screen.getByTestId('weekly-ritual-step-3')).toBeInTheDocument();
  });

  it('4. 「閉じる」ボタンで onClose が呼ばれる', () => {
    const onClose = vi.fn();
    render(<WeeklyRitualGuideDialog open={true} onClose={onClose} />);
    const closeButton = screen.getByTestId('weekly-ritual-guide-close');
    fireEvent.click(closeButton);
    expect(onClose).toHaveBeenCalledOnce();
  });
});
