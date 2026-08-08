/**
 * BulkWeekPinAllButton テスト (PO 決定 2026-08-08)。
 *
 * 赤ピンの一括ボタン (BulkPinAllPfvsButton) と対になる青ピン版:
 *   1. 「今週全件固定」 click → dry_run で件数取得 → 2 段階 dialog → 実行で pinned=true
 *   2. 対象 0 件なら toast.info を出して dialog を開かない
 *   3. 「今週全件解除」の確認文に「次の週生成で…戻ります」の警告が入る
 *   4. canEdit=false ではボタンが disabled
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';

// ─── モック ──────────────────────────────────────────────────────────────────

const { mockMutateAsync, mockToast } = vi.hoisted(() => ({
  mockMutateAsync: vi.fn(),
  mockToast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock('@/lib/queries/visit_week_pin', () => ({
  useBulkVisitWeekPin: () => ({
    mutateAsync: mockMutateAsync,
    isPending: false,
  }),
}));

vi.mock('sonner', () => ({
  toast: mockToast,
}));

import { BulkWeekPinAllButton } from '../BulkWeekPinAllButton';

const WEEK = { isoYear: 2026, isoWeek: 32 };

beforeEach(() => {
  vi.clearAllMocks();
});

describe('BulkWeekPinAllButton', () => {
  it('今週全件固定: dry_run で件数 → 2 段階確認 → pinned=true で実行', async () => {
    mockMutateAsync
      .mockResolvedValueOnce({ target_count: 76, updated_count: 0 }) // dry_run
      .mockResolvedValueOnce({ target_count: 76, updated_count: 76 }); // 実行

    render(<BulkWeekPinAllButton canEdit {...WEEK} />);
    fireEvent.click(screen.getByTestId('bulk-week-pin-all-lock-button'));

    // dry_run が dryRun=true で呼ばれる。
    await waitFor(() =>
      expect(mockMutateAsync).toHaveBeenCalledWith({
        isoYear: 2026,
        isoWeek: 32,
        pinned: true,
        dryRun: true,
      }),
    );
    // 1 段目: 件数提示。
    expect(await screen.findByTestId('bulk-week-pin-confirm-dialog')).toHaveTextContent('76 件');
    fireEvent.click(screen.getByTestId('bulk-week-pin-step1-confirm-button'));
    // 2 段目 → 実行。
    fireEvent.click(await screen.findByTestId('bulk-week-pin-step2-confirm-button'));

    await waitFor(() =>
      expect(mockMutateAsync).toHaveBeenCalledWith({ isoYear: 2026, isoWeek: 32, pinned: true }),
    );
    await waitFor(() =>
      expect(mockToast.success).toHaveBeenCalledWith(expect.stringContaining('76 件を今週固定')),
    );
  });

  it('対象 0 件なら toast.info を出して dialog を開かない', async () => {
    mockMutateAsync.mockResolvedValueOnce({ target_count: 0, updated_count: 0 });

    render(<BulkWeekPinAllButton canEdit {...WEEK} />);
    fireEvent.click(screen.getByTestId('bulk-week-pin-all-lock-button'));

    await waitFor(() => expect(mockToast.info).toHaveBeenCalled());
    expect(screen.queryByTestId('bulk-week-pin-confirm-dialog')).not.toBeInTheDocument();
  });

  it('今週全件解除: 確認文に「次の週生成で戻る」警告が入り、pinned=false で実行される', async () => {
    mockMutateAsync
      .mockResolvedValueOnce({ target_count: 12, updated_count: 0 }) // dry_run
      .mockResolvedValueOnce({ target_count: 12, updated_count: 12 });

    render(<BulkWeekPinAllButton canEdit {...WEEK} />);
    fireEvent.click(screen.getByTestId('bulk-week-pin-all-unlock-button'));

    const dialog = await screen.findByTestId('bulk-week-pin-confirm-dialog');
    // 解除の影響 (次の週生成で型の時刻に戻る) を必ず伝える。
    expect(dialog).toHaveTextContent(
      '次に週生成を実行したとき固定訪問スケジュールの時刻に戻ります',
    );

    fireEvent.click(screen.getByTestId('bulk-week-pin-step1-confirm-button'));
    fireEvent.click(await screen.findByTestId('bulk-week-pin-step2-confirm-button'));

    await waitFor(() =>
      expect(mockMutateAsync).toHaveBeenCalledWith({ isoYear: 2026, isoWeek: 32, pinned: false }),
    );
    await waitFor(() =>
      expect(mockToast.success).toHaveBeenCalledWith(
        expect.stringContaining('次の週生成で固定訪問スケジュールの時刻に戻ります'),
      ),
    );
  });

  it('canEdit=false ではボタンが disabled (全ロール同一表示・操作は権限どおり)', () => {
    render(<BulkWeekPinAllButton canEdit={false} {...WEEK} />);
    expect(screen.getByTestId('bulk-week-pin-all-lock-button')).toBeDisabled();
    expect(screen.getByTestId('bulk-week-pin-all-unlock-button')).toBeDisabled();
  });
});
