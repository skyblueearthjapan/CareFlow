/**
 * BulkEventDefaultsDialog vitest (staff-event-history-design.md §2 Phase 3 /
 * docs/mockups/event-defaults-bulk-mock.html 変更A)。
 *
 * カバーするシナリオ:
 *   1. プレビュー — 「N名 × M曜日 = NM件…」/ 未選択時の案内
 *   2. 曜日ショートカット (月〜金) がプレビューに反映される
 *   3. 送信ペイロード — staff_ids × weekdays × 時刻 × タイトル × blocking
 *   4. ひな形セレクト — 選ぶと タイトル / 時刻 が入る (共通ひな形のみ)
 *   5. ☀ 9:00出勤の全員 — 選択中の曜日すべてで 09:00 以前に出勤する人だけ選ぶ
 *   6. isEarlyShiftStaff / shiftStartSummary の単体挙動
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

vi.mock('@/lib/queries/staff', () => ({ useStaffList: vi.fn() }));
vi.mock('@/lib/queries/event-templates', () => ({ useEventTemplates: vi.fn() }));
vi.mock('@/lib/queries/staff-shifts', () => ({ useManyStaffShifts: vi.fn() }));
vi.mock('@/lib/queries/staff-event-defaults', () => ({ useBulkCreateEventDefaults: vi.fn() }));

import { useEventTemplates } from '@/lib/queries/event-templates';
import { useBulkCreateEventDefaults } from '@/lib/queries/staff-event-defaults';
import { useStaffList } from '@/lib/queries/staff';
import { useManyStaffShifts } from '@/lib/queries/staff-shifts';

import {
  BulkEventDefaultsDialog,
  isEarlyShiftStaff,
  shiftStartSummary,
} from '../BulkEventDefaultsDialog';

const A = '00000000-0000-0000-0000-00000000000a';
const B = '00000000-0000-0000-0000-00000000000b';
const C = '00000000-0000-0000-0000-00000000000c';

function shifts(start: string, onWeekdays: number[] = [0, 1, 2, 3, 4, 5]) {
  return Array.from({ length: 7 }, (_, weekday) => ({
    weekday,
    is_on: onWeekdays.includes(weekday),
    start_time: onWeekdays.includes(weekday) ? start : null,
    end_time: onWeekdays.includes(weekday) ? '18:00' : null,
  }));
}

const bulkMutate = vi.fn();

function setup(opts: { templates?: unknown[] } = {}) {
  (useStaffList as unknown as Mock).mockReturnValue({
    data: [
      { id: A, name: '川名 千恵', kana: 'カワナ チエ', code: 's001', status: 'active' },
      { id: B, name: '熊澤 妙子', kana: 'クマザワ タエコ', code: 's002', status: 'active' },
      { id: C, name: '退職 太郎', kana: 'タイショク タロウ', code: 's003', status: 'retired' },
    ],
  });
  (useEventTemplates as unknown as Mock).mockReturnValue({
    data: opts.templates ?? [
      {
        id: 'tpl-asakai',
        staff_id: null,
        title: '朝会',
        event_type: 'event',
        start_time: '09:00',
        end_time: '09:15',
        blocking: false,
        note: null,
        sort_order: 0,
        is_active: true,
        is_shared: true,
      },
      {
        id: 'tpl-conf',
        staff_id: null,
        title: 'カンファレンス',
        event_type: 'event',
        start_time: '13:00',
        end_time: '14:00',
        blocking: false,
        note: null,
        sort_order: 1,
        is_active: true,
        is_shared: true,
      },
      // 個人ひな形はセレクトに出さない。
      {
        id: 'tpl-personal',
        staff_id: A,
        title: '面談 松岡',
        event_type: 'event',
        start_time: null,
        end_time: null,
        blocking: false,
        note: null,
        sort_order: 0,
        is_active: true,
        is_shared: false,
      },
    ],
  });
  (useManyStaffShifts as unknown as Mock).mockReturnValue({
    byStaffId: new Map([
      [A, shifts('09:00')],
      // 熊澤さんは土曜だけ 10:00 出勤 → 月〜土 では対象外・月〜金なら対象。
      [
        B,
        shifts('09:00', [0, 1, 2, 3, 4]).map((s) =>
          s.weekday === 5 ? { weekday: 5, is_on: true, start_time: '10:00', end_time: '18:00' } : s,
        ),
      ],
    ]),
    isLoading: false,
  });
  (useBulkCreateEventDefaults as unknown as Mock).mockReturnValue({
    mutateAsync: bulkMutate,
    isPending: false,
  });
  return render(<BulkEventDefaultsDialog open onOpenChange={() => undefined} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  bulkMutate.mockResolvedValue({ created: 12, skipped: 0 });
});

describe('BulkEventDefaultsDialog — プレビュー', () => {
  it('1. スタッフ未選択なら案内、選ぶと N名 × M曜日 = NM件 を出す', () => {
    setup();
    expect(screen.getByTestId('bulk-preview').textContent).toBe('曜日とスタッフを選んでください');

    fireEvent.click(screen.getByTestId('bulk-select-all'));
    // active な 2 名のみ (退職者は候補に出ない) × 月〜土 6 曜日。
    expect(screen.getByTestId('bulk-preview').textContent).toBe(
      '2名 × 6曜日 = 12件の固定イベントを登録します（既に同じ登録がある分は自動でスキップ）',
    );
  });

  it('2. 「月〜金」ショートカットでプレビューの曜日数が変わる', () => {
    setup();
    fireEvent.click(screen.getByTestId('bulk-select-all'));
    fireEvent.click(screen.getByTestId('bulk-shortcut-weekdays'));
    expect(screen.getByTestId('bulk-preview').textContent).toContain('2名 × 5曜日 = 10件');
  });
});

describe('BulkEventDefaultsDialog — 送信', () => {
  it('3. staff_ids × weekdays × 時刻 × タイトル × blocking を一括登録 API へ送る', async () => {
    setup();
    fireEvent.change(screen.getByLabelText('タイトル'), { target: { value: ' 朝会 ' } });
    fireEvent.click(screen.getByTestId('bulk-shortcut-weekdays'));
    fireEvent.click(screen.getByTestId('bulk-select-all'));
    fireEvent.click(screen.getByLabelText('絶対に潰せないイベントにする'));

    fireEvent.click(screen.getByTestId('bulk-submit'));
    await waitFor(() => expect(bulkMutate).toHaveBeenCalledTimes(1));
    expect(bulkMutate.mock.calls[0]![0]).toEqual({
      staff_ids: [A, B],
      weekdays: [0, 1, 2, 3, 4],
      start_time: '09:00',
      end_time: '09:15',
      title: '朝会',
      blocking: true,
    });
  });

  it('3b. タイトル空 / スタッフ未選択では送信できない', () => {
    setup();
    expect((screen.getByTestId('bulk-submit') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('タイトル'), { target: { value: '朝会' } });
    expect((screen.getByTestId('bulk-submit') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId('bulk-select-all'));
    expect((screen.getByTestId('bulk-submit') as HTMLButtonElement).disabled).toBe(false);
  });
});

describe('BulkEventDefaultsDialog — ひな形 / ☀選択', () => {
  it('4. ひな形セレクトは共通のみを並べ、選ぶとタイトルと時刻が入る', () => {
    setup();
    const select = screen.getByTestId('bulk-template-select') as HTMLSelectElement;
    // 「— 手入力 —」+ 共通2件 (個人ひな形は出ない)。
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      '— 手入力 —',
      '朝会（09:00〜09:15）',
      'カンファレンス（13:00〜14:00）',
    ]);

    fireEvent.change(select, { target: { value: 'tpl-conf' } });
    expect((screen.getByLabelText('タイトル') as HTMLInputElement).value).toBe('カンファレンス');
    expect((screen.getByLabelText('開始') as HTMLInputElement).value).toBe('13:00');
    expect((screen.getByLabelText('終了') as HTMLInputElement).value).toBe('14:00');
  });

  it('5. ☀ は選択中の曜日すべてで 09:00 以前に出勤する人だけを選ぶ', async () => {
    setup();
    // 月〜土 (既定) → 土曜 10:00 出勤の熊澤さんは外れる。
    fireEvent.click(screen.getByTestId('bulk-select-early'));
    expect(screen.getByTestId('bulk-preview').textContent).toContain('1名 × 6曜日 = 6件');

    // 月〜金なら 2 名とも該当。
    fireEvent.click(screen.getByTestId('bulk-shortcut-weekdays'));
    fireEvent.click(screen.getByTestId('bulk-select-early'));
    expect(screen.getByTestId('bulk-preview').textContent).toContain('2名 × 5曜日 = 10件');
  });
});

describe('シフト判定ヘルパ', () => {
  it('6. isEarlyShiftStaff / shiftStartSummary', () => {
    const early = shifts('09:00');
    const late = shifts('10:00');
    const partial = shifts('09:00', [0, 1]);

    expect(isEarlyShiftStaff(early, [0, 1, 2])).toBe(true);
    expect(isEarlyShiftStaff(late, [0])).toBe(false);
    expect(isEarlyShiftStaff(partial, [0, 1])).toBe(true);
    expect(isEarlyShiftStaff(partial, [0, 1, 2])).toBe(false);
    expect(isEarlyShiftStaff(undefined, [0])).toBe(false);

    expect(shiftStartSummary(early, [0, 1])).toBe('09:00出勤');
    expect(shiftStartSummary(partial, [2, 3])).toBe('出勤なし');
    expect(shiftStartSummary(undefined, [0])).toBe('シフト未取得');
    expect(
      shiftStartSummary(
        [
          { weekday: 0, is_on: true, start_time: '08:30', end_time: '17:00' },
          { weekday: 1, is_on: true, start_time: '09:00', end_time: '18:00' },
        ],
        [0, 1],
      ),
    ).toBe('08:30〜09:00出勤');
  });
});
