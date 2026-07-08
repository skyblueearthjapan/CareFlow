/**
 * TimelineEventAddDialog — D-1 (複数スタッフへの打合せ一括登録) テスト。
 *
 * 検証:
 *   1. 既定選択 (起動元の列の担当) がチェック済みで開く
 *   2. 追加選択して送信 → 選択スタッフごとに mutateAsync が呼ばれる (同一 payload)
 *   3. 0 名選択で送信 → エラーメッセージを出し mutateAsync を呼ばない
 *   4. 部分失敗 → 成功済みを選択から外してダイアログ維持 (再送で二重登録しない)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// Radix Select (種別) が ResizeObserver を要求する (jsdom 未実装) → 最小ポリフィル。
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver ??= ResizeObserverStub;

const { mockToast, mockCreateForStaff } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  mockCreateForStaff: vi.fn(),
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('@/lib/queries/staff-events', () => ({
  useCreateEventForStaff: () => ({ mutateAsync: mockCreateForStaff, isPending: false }),
}));

import { TimelineEventAddDialog } from '../TimelineEventAddDialog';

const STAFF = [
  { id: 's-1', name: '田中 一郎' },
  { id: 's-2', name: '佐藤 花子' },
  { id: 's-3', name: '管理 太郎' },
];

function renderDialog(overrides: Partial<Parameters<typeof TimelineEventAddDialog>[0]> = {}) {
  const onClose = vi.fn();
  render(
    <TimelineEventAddDialog
      open
      onClose={onClose}
      staffOptions={STAFF}
      defaultStaffIds={['s-1']}
      defaultDate="2026-05-04"
      defaultStart="14:00"
      defaultEnd="15:00"
      {...overrides}
    />,
  );
  return { onClose };
}

async function fillTitleAndSubmit(title = '週次打合せ') {
  fireEvent.change(screen.getByLabelText('タイトル'), { target: { value: title } });
  fireEvent.click(screen.getByTestId('tl-event-submit'));
}

describe('TimelineEventAddDialog (D-1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. 既定選択 (列の担当) がチェック済みで開き、件数表示に反映される', () => {
    renderDialog();
    expect(screen.getByTestId('tl-event-staff-count')).toHaveTextContent('1 名選択中');
    // 全スタッフ (管理職含む) が候補に出る。
    expect(screen.getByText('田中 一郎')).toBeInTheDocument();
    expect(screen.getByText('佐藤 花子')).toBeInTheDocument();
    expect(screen.getByText('管理 太郎')).toBeInTheDocument();
  });

  it('2. 追加選択して送信すると選択スタッフごとに同一 payload で作成される', async () => {
    mockCreateForStaff.mockResolvedValue({});
    const { onClose } = renderDialog();
    fireEvent.click(screen.getByTestId('tl-event-staff-s-3'));
    await fillTitleAndSubmit();
    await waitFor(() => expect(mockCreateForStaff).toHaveBeenCalledTimes(2));
    const staffIds = mockCreateForStaff.mock.calls.map((c) => c[0].staffId).sort();
    expect(staffIds).toEqual(['s-1', 's-3']);
    for (const call of mockCreateForStaff.mock.calls) {
      expect(call[0].payload).toEqual(
        expect.objectContaining({
          date: '2026-05-04',
          title: '週次打合せ',
          start_time: '14:00',
          end_time: '15:00',
        }),
      );
    }
    expect(mockToast.success).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('3. 0 名選択で送信するとエラーを出し作成を呼ばない', async () => {
    renderDialog({ defaultStaffIds: [] });
    await fillTitleAndSubmit();
    await waitFor(() =>
      expect(screen.getByTestId('tl-event-staff-error')).toHaveTextContent(
        'スタッフを 1 名以上選択してください',
      ),
    );
    expect(mockCreateForStaff).not.toHaveBeenCalled();
  });

  it('4. 部分失敗時は成功済みを選択から外してダイアログを維持する', async () => {
    mockCreateForStaff.mockImplementation(({ staffId }: { staffId: string }) =>
      staffId === 's-2' ? Promise.reject(new Error('boom')) : Promise.resolve({}),
    );
    const { onClose } = renderDialog({ defaultStaffIds: ['s-1', 's-2'] });
    await fillTitleAndSubmit();
    await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(onClose).not.toHaveBeenCalled();
    // 成功した s-1 は選択から外れ、失敗した s-2 だけ残る (再送で二重登録しない)。
    expect(screen.getByTestId('tl-event-staff-count')).toHaveTextContent('1 名選択中');
    expect(mockToast.error.mock.calls[0]![0]).toContain('佐藤 花子');
  });
});
