/**
 * ClearWeekButton — 「この週を空にする」 (BP-4 / PO 2026-10-12)。
 * 押す → 件数 (dry_run) → 確認ダイアログ (消す N・カイポケへ送った M・残す K) →
 * 実行 → 「元に戻す」リンク → 押すと snapshot_id で undo。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const { clearFn, undoFn, snapsFn, mockToast } = vi.hoisted(() => ({
  snapsFn: vi.fn(),
  clearFn: vi.fn(),
  undoFn: vi.fn(),
  mockToast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
vi.mock('sonner', () => ({ toast: mockToast }));
vi.mock('@/lib/queries/integrations', () => ({ useInboundSnapshots: () => snapsFn() }));
vi.mock('@/lib/queries/week_clear', () => ({
  useClearWeek: () => ({ mutateAsync: clearFn, isPending: false }),
  useUndoClearWeek: () => ({ mutateAsync: undoFn, isPending: false }),
}));

import { ClearWeekButton } from '../ClearWeekButton';

describe('ClearWeekButton', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    snapsFn.mockReturnValue({ data: undefined });
    clearFn.mockImplementation(async ({ dryRun }: { dryRun: boolean }) =>
      dryRun
        ? { to_delete: 42, kaipoke_linked: 30, kept_checked_in: 5 }
        : { to_delete: 42, kaipoke_linked: 30, kept_checked_in: 5, snapshot_id: 'snap-1' },
    );
    undoFn.mockResolvedValue({});
  });

  it('確認ダイアログに消す件数・カイポケへ送った件数・残す件数を落ち着いた文で出す', async () => {
    render(<ClearWeekButton canEdit isoYear={2026} isoWeek={42} />);
    fireEvent.click(screen.getByTestId('clear-week-button'));

    const text = await screen.findByTestId('clear-week-text');
    expect(text).toHaveTextContent('この週の予定 42 件を消します。');
    expect(text).toHaveTextContent('うちカイポケへ送った訪問 30 件は、次の送信で削除になります');
    expect(text).toHaveTextContent('残す 5 件: 打刻・実績あり');
    expect(clearFn).toHaveBeenCalledWith({ isoYear: 2026, isoWeek: 42, dryRun: true });
    // まだ実行していない
    expect(clearFn).toHaveBeenCalledTimes(1);
  });

  it('カイポケへ送った訪問が 0 件ならその文を出さない', async () => {
    clearFn.mockResolvedValueOnce({ to_delete: 3, kaipoke_linked: 0, kept_checked_in: 0 });
    render(<ClearWeekButton canEdit isoYear={2026} isoWeek={42} />);
    fireEvent.click(screen.getByTestId('clear-week-button'));
    const text = await screen.findByTestId('clear-week-text');
    expect(text).not.toHaveTextContent('次の送信で削除');
  });

  it('消す予定が 0 件ならダイアログを出さない', async () => {
    clearFn.mockResolvedValueOnce({ to_delete: 0, kaipoke_linked: 0, kept_checked_in: 2 });
    render(<ClearWeekButton canEdit isoYear={2026} isoWeek={42} />);
    fireEvent.click(screen.getByTestId('clear-week-button'));
    await waitFor(() => expect(mockToast.info).toHaveBeenCalled());
    expect(screen.queryByTestId('clear-week-dialog')).not.toBeInTheDocument();
  });

  it('実行すると「元に戻す」が出て、押すと snapshot_id で戻す', async () => {
    render(<ClearWeekButton canEdit isoYear={2026} isoWeek={42} />);
    fireEvent.click(screen.getByTestId('clear-week-button'));
    await screen.findByTestId('clear-week-text');

    expect(screen.queryByTestId('clear-week-undo')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('clear-week-confirm'));

    const undo = await screen.findByTestId('clear-week-undo');
    expect(clearFn).toHaveBeenLastCalledWith({ isoYear: 2026, isoWeek: 42, dryRun: false });

    fireEvent.click(undo);
    await waitFor(() =>
      expect(undoFn).toHaveBeenCalledWith({ isoYear: 2026, isoWeek: 42, snapshotId: 'snap-1' }),
    );
    await waitFor(() => expect(screen.queryByTestId('clear-week-undo')).not.toBeInTheDocument());
  });

  it('編集権限がなければ押せない', () => {
    render(<ClearWeekButton canEdit={false} isoYear={2026} isoWeek={42} />);
    expect(screen.getByTestId('clear-week-button')).toBeDisabled();
  });

  it('リロード後も、24 時間以内の未実行の「空にする」があれば元に戻せる', async () => {
    snapsFn.mockReturnValue({
      data: {
        snapshots: [
          { id: 'old', kind: 'clear_week', createdAt: '2020-01-01T00:00:00Z', restoredAt: null },
          { id: 'done', kind: 'clear_week', createdAt: new Date().toISOString(), restoredAt: 'x' },
          { id: 'snap-9', kind: 'clear_week', createdAt: new Date().toISOString(), restoredAt: null },
        ],
      },
    });
    render(<ClearWeekButton canEdit isoYear={2026} isoWeek={42} />);
    fireEvent.click(screen.getByTestId('clear-week-undo'));
    await waitFor(() =>
      expect(undoFn).toHaveBeenCalledWith({ isoYear: 2026, isoWeek: 42, snapshotId: 'snap-9' }),
    );
  });

  it('戻せる保存が古い・戻し済みなら「元に戻す」を出さない', () => {
    snapsFn.mockReturnValue({
      data: {
        snapshots: [
          { id: 'old', kind: 'clear_week', createdAt: '2020-01-01T00:00:00Z', restoredAt: null },
          { id: 'cp', kind: 'copy_week', createdAt: new Date().toISOString(), restoredAt: null },
        ],
      },
    });
    render(<ClearWeekButton canEdit isoYear={2026} isoWeek={42} />);
    expect(screen.queryByTestId('clear-week-undo')).not.toBeInTheDocument();
  });
});
