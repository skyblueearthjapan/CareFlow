/**
 * EventDefaultsCard vitest (staff-event-history-design.md §2 Phase 3 /
 * docs/mockups/event-defaults-bulk-mock.html 変更B)。
 *
 * カバーするシナリオ:
 *   1. groupEventDefaults — 同一内容は曜日をまとめ、内容が違えば別行
 *   2. まとめ表示 — 曜日バッジが昇順に並ぶ / 休み自動不参加の説明
 *   3. 追加ダイアログ — 曜日の複数選択を一括登録 API で作成 (staff_ids=[本人])
 *   4. 🗑 — まとめ行の全曜日を confirm 後に削除
 *   5. ✎ — 外した曜日は DELETE・足した曜日は一括作成
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver ??= ResizeObserverStub;

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock('@/lib/queries/staff-event-defaults', () => ({
  useStaffEventDefaults: vi.fn(),
  useBulkCreateEventDefaults: vi.fn(),
  useDeleteEventDefault: vi.fn(),
}));

import {
  useBulkCreateEventDefaults,
  useDeleteEventDefault,
  useStaffEventDefaults,
} from '@/lib/queries/staff-event-defaults';

import { EventDefaultsCard, groupEventDefaults } from '../EventDefaultsCard';

const STAFF_ID = '00000000-0000-0000-0000-0000000000s1';
const WD_LABELS = ['月', '火', '水', '木', '金', '土'];

function row(over: Record<string, unknown> = {}) {
  const weekday = (over.weekday as number) ?? 0;
  return {
    id: `d${weekday}`,
    staff_id: STAFF_ID,
    weekday,
    weekday_label: WD_LABELS[weekday]!,
    start_time: '09:00',
    end_time: '09:15',
    title: '朝会',
    blocking: false,
    note: null,
    ...over,
  };
}

const bulkMutate = vi.fn();
const deleteMutate = vi.fn();

function setup(rows: unknown[], canEdit = true) {
  (useStaffEventDefaults as unknown as Mock).mockReturnValue({
    data: rows,
    isLoading: false,
    isError: false,
    error: null,
  });
  (useBulkCreateEventDefaults as unknown as Mock).mockReturnValue({
    mutateAsync: bulkMutate,
    isPending: false,
  });
  (useDeleteEventDefault as unknown as Mock).mockReturnValue({
    mutateAsync: deleteMutate,
    isPending: false,
  });
  return render(<EventDefaultsCard staffId={STAFF_ID} canEdit={canEdit} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  bulkMutate.mockResolvedValue({ created: 1, skipped: 0 });
  deleteMutate.mockResolvedValue(undefined);
});

describe('groupEventDefaults', () => {
  it('1. 同一内容 (タイトル/時刻/🔒/備考) は 1 グループ・違えば別グループ', () => {
    const groups = groupEventDefaults([
      row({ weekday: 2 }),
      row({ weekday: 0 }),
      row({ weekday: 1 }),
      row({
        id: 'conf',
        weekday: 2,
        start_time: '13:00',
        end_time: '14:00',
        title: 'カンファレンス',
        blocking: true,
      }),
      // 備考違いは別グループ (内容一致がまとめの条件)。
      row({ id: 'note3', weekday: 3, note: '第1週のみ' }),
    ] as never);

    expect(groups).toHaveLength(3);
    // 開始時刻順 → 09:00 の 2 グループが先、13:00 が最後。
    expect(groups[0]!.entries.map((e) => e.weekday)).toEqual([0, 1, 2]);
    expect(groups[0]!.title).toBe('朝会');
    expect(groups[1]!.entries.map((e) => e.weekday)).toEqual([3]);
    expect(groups[1]!.note).toBe('第1週のみ');
    expect(groups[2]!.title).toBe('カンファレンス');
    expect(groups[2]!.blocking).toBe(true);
  });
});

describe('EventDefaultsCard — まとめ表示', () => {
  it('2. 同じ内容の曜日はバッジでまとめて 1 行に出す', () => {
    setup([row({ weekday: 1 }), row({ weekday: 0 }), row({ weekday: 5 })]);
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(1);
    const badges = items[0]!.querySelector('[aria-label="曜日"]')!;
    expect(badges.textContent).toBe('月火土');
    expect(items[0]!.textContent).toContain('09:00〜09:15');
    expect(items[0]!.textContent).toContain('朝会');
  });

  it('2b. 説明文に「休みの日は自動で不参加になります」がある', () => {
    setup([]);
    expect(screen.getByText(/休みの日は自動で不参加になります/)).toBeTruthy();
  });
});

describe('EventDefaultsCard — 追加', () => {
  it('3. 曜日を複数選んで一括登録 API (staff_ids=[本人]) で作成する', async () => {
    setup([]);
    fireEvent.click(screen.getByTestId('event-default-add-button'));

    // 既定は曜日未選択 → 登録できない。
    expect((screen.getByTestId('event-default-add-confirm') as HTMLButtonElement).disabled).toBe(
      true,
    );

    fireEvent.click(screen.getByTestId('ed-add-shortcut-all'));
    fireEvent.click(screen.getByTestId('ed-add-weekday-5')); // 土を外す
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '朝会' } });
    fireEvent.click(screen.getByTestId('event-default-add-confirm'));

    await waitFor(() => expect(bulkMutate).toHaveBeenCalledTimes(1));
    expect(bulkMutate.mock.calls[0]![0]).toEqual({
      staff_ids: [STAFF_ID],
      weekdays: [0, 1, 2, 3, 4],
      start_time: '09:00',
      end_time: '09:15',
      title: '朝会',
      blocking: false,
    });
  });
});

describe('EventDefaultsCard — まとめ行の削除 / 曜日編集', () => {
  it('4. 🗑 は confirm の後にその型の全曜日を削除する', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    setup([row({ weekday: 0 }), row({ weekday: 1 })]);

    fireEvent.click(screen.getByLabelText('朝会 を削除'));
    await waitFor(() => expect(deleteMutate).toHaveBeenCalledTimes(2));
    expect(deleteMutate.mock.calls.map((c) => c[0])).toEqual(['d0', 'd1']);
    expect(confirmSpy.mock.calls[0]![0]).toContain('この型の全曜日（2件）を削除しますか？');
    confirmSpy.mockRestore();
  });

  it('4b. confirm でキャンセルすると削除しない', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    setup([row({ weekday: 0 })]);
    fireEvent.click(screen.getByLabelText('朝会 を削除'));
    expect(deleteMutate).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('5. ✎ — 外した曜日は DELETE・足した曜日は一括作成', async () => {
    setup([row({ weekday: 0 }), row({ weekday: 1 })]);
    fireEvent.click(screen.getByLabelText('朝会 の曜日を編集'));

    // 月(0) を外し、水(2) を足す。
    fireEvent.click(screen.getByTestId('ed-edit-weekday-0'));
    fireEvent.click(screen.getByTestId('ed-edit-weekday-2'));
    fireEvent.click(screen.getByTestId('event-default-weekdays-save'));

    await waitFor(() => expect(bulkMutate).toHaveBeenCalledTimes(1));
    expect(bulkMutate.mock.calls[0]![0]).toEqual({
      staff_ids: [STAFF_ID],
      weekdays: [2],
      start_time: '09:00',
      end_time: '09:15',
      title: '朝会',
      blocking: false,
      note: null,
    });
    await waitFor(() => expect(deleteMutate).toHaveBeenCalledTimes(1));
    expect(deleteMutate.mock.calls[0]![0]).toBe('d0');
  });
});
