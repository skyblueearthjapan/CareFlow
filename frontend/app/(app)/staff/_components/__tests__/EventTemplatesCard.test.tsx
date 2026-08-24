/**
 * EventTemplatesCard vitest (staff-event-history-design.md §2 Phase 2 /
 * docs/mockups/event-templates-mock.html)。
 *
 * カバーするシナリオ:
 *   1. 共通スコープの一覧 — is_shared で切り分け・sort_order 順・時刻なしは
 *      「時間はその場で入力」
 *   2. 個人スコープは is_shared=false の行だけを出す
 *   3. 無効行 — 打ち消し + 「無効（プルダウンに出ない）」・👁 で is_active 反転
 *   4. 並べ替え — ↑↓ で組んだ並びを ordered_ids として送る
 *   5. 履歴から追加 — 回数/直近の表示・＋で直近の時刻を引き継いで作成 →
 *      「✓ 追加しました」
 *   6. staff (canEdit=false) は閲覧のみ (操作ボタンが出ない)
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

vi.mock('@/lib/queries/event-templates', () => ({
  useEventTemplates: vi.fn(),
  useEventTemplateHistorySuggestions: vi.fn(),
  useCreateEventTemplate: vi.fn(),
  useUpdateEventTemplate: vi.fn(),
  useDeleteEventTemplate: vi.fn(),
  useReorderEventTemplates: vi.fn(),
}));

import {
  useCreateEventTemplate,
  useDeleteEventTemplate,
  useEventTemplateHistorySuggestions,
  useEventTemplates,
  useReorderEventTemplates,
  useUpdateEventTemplate,
} from '@/lib/queries/event-templates';

import { EventTemplatesCard, SHARED_TEMPLATE_SCOPE } from '../EventTemplatesCard';

const STAFF_ID = '00000000-0000-0000-0000-0000000000s1';

function tpl(over: Record<string, unknown> = {}) {
  return {
    id: 't1',
    staff_id: null,
    title: 'カンファレンス',
    event_type: 'event',
    start_time: '13:00',
    end_time: '14:00',
    blocking: false,
    note: null,
    sort_order: 0,
    is_active: true,
    is_shared: true,
    ...over,
  };
}

const createMutate = vi.fn();
const updateMutate = vi.fn();
const reorderMutate = vi.fn();

function setup(rows: unknown[], history: unknown[] = [], canEdit = true, scope = SHARED_TEMPLATE_SCOPE) {
  (useEventTemplates as unknown as Mock).mockReturnValue({
    data: rows,
    isLoading: false,
    isError: false,
    error: null,
  });
  (useEventTemplateHistorySuggestions as unknown as Mock).mockReturnValue({
    data: history,
    isLoading: false,
    isError: false,
    error: null,
  });
  (useCreateEventTemplate as unknown as Mock).mockReturnValue({
    mutateAsync: createMutate,
    isPending: false,
  });
  (useUpdateEventTemplate as unknown as Mock).mockReturnValue({
    mutateAsync: updateMutate,
    isPending: false,
  });
  (useDeleteEventTemplate as unknown as Mock).mockReturnValue({
    mutateAsync: vi.fn(),
    isPending: false,
  });
  (useReorderEventTemplates as unknown as Mock).mockReturnValue({
    mutateAsync: reorderMutate,
    isPending: false,
  });
  return render(<EventTemplatesCard scope={scope} canEdit={canEdit} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  createMutate.mockResolvedValue({});
  updateMutate.mockResolvedValue({});
  reorderMutate.mockResolvedValue([]);
});

describe('EventTemplatesCard — 一覧', () => {
  it('1. 共通スコープは is_shared=true の行だけを sort_order 順に出す', () => {
    setup([
      tpl({ id: 'b', title: '社内研修', event_type: 'training', sort_order: 1 }),
      tpl({ id: 'a', title: 'カンファレンス', sort_order: 0 }),
      tpl({ id: 'p', title: '面談 松岡', staff_id: STAFF_ID, is_shared: false, sort_order: 0 }),
    ]);
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(items[0]!.textContent).toContain('カンファレンス');
    expect(items[0]!.textContent).toContain('13:00〜14:00');
    expect(items[1]!.textContent).toContain('社内研修');
    expect(screen.queryByText('面談 松岡')).toBeNull();
  });

  it('1b. 時刻が無いひな形は「時間はその場で入力」と出る', () => {
    setup([tpl({ start_time: null, end_time: null, blocking: true })]);
    expect(screen.getByText('時間はその場で入力')).toBeTruthy();
    expect(screen.getByTitle('提案エンジンでも潰されません')).toBeTruthy();
  });

  it('2. 個人スコープは is_shared=false の行だけを出す', () => {
    setup(
      [
        tpl({ id: 'a', title: 'カンファレンス' }),
        tpl({ id: 'p', title: '面談 松岡', staff_id: STAFF_ID, is_shared: false }),
      ],
      [],
      true,
      STAFF_ID,
    );
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(1);
    expect(items[0]!.textContent).toContain('面談 松岡');
  });
});

describe('EventTemplatesCard — 無効化', () => {
  it('3. 無効行は打ち消し表示 + 注記が出て、👁 で is_active を反転する', async () => {
    setup([tpl({ id: 'off1', title: '意見交換会', is_active: false })]);
    expect(screen.getByText('無効（プルダウンに出ない）')).toBeTruthy();
    expect(screen.getByText('意見交換会').className).toContain('line-through');

    fireEvent.click(screen.getByTestId('event-template-toggle-off1'));
    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));
    expect(updateMutate.mock.calls[0]![0]).toEqual({
      id: 'off1',
      payload: { title: '意見交換会', is_active: true },
    });
  });
});

describe('EventTemplatesCard — 並べ替え', () => {
  it('4. ↓ で 1 つ下げた並びを ordered_ids として送る', async () => {
    setup([
      tpl({ id: 'a', title: 'A', sort_order: 0 }),
      tpl({ id: 'b', title: 'B', sort_order: 1 }),
      tpl({ id: 'c', title: 'C', sort_order: 2 }),
    ]);
    // 先頭は ↑ が無効・末尾は ↓ が無効。
    expect((screen.getByTestId('event-template-up-a') as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId('event-template-down-c') as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByTestId('event-template-down-a'));
    await waitFor(() => expect(reorderMutate).toHaveBeenCalledTimes(1));
    expect(reorderMutate.mock.calls[0]![0]).toEqual({
      staffId: null,
      orderedIds: ['b', 'a', 'c'],
    });
  });
});

describe('EventTemplatesCard — 履歴から追加', () => {
  const HISTORY = [
    {
      title: '面談 松岡',
      count: 5,
      last_date: '2026-09-01',
      last_start_time: '14:00',
      last_end_time: '15:00',
      event_type: 'event',
    },
    {
      title: '打合せ：川名',
      count: 2,
      last_date: '2026-08-10',
      last_start_time: null,
      last_end_time: null,
      event_type: 'event',
    },
  ];

  it('5. 「履歴から追加」を開くと回数と直近が並び、＋で直近の時刻を引き継いで作成する', async () => {
    setup([], HISTORY);
    expect(screen.queryByTestId('event-template-history-panel')).toBeNull();

    fireEvent.click(screen.getByTestId('event-template-history-toggle'));
    expect(screen.getByTestId('event-template-history-panel')).toBeTruthy();
    const rows = screen.getAllByTestId('event-template-history-row');
    expect(rows).toHaveLength(2);
    expect(rows[0]!.textContent).toContain('× 5回　直近 9/1 14:00〜15:00');

    fireEvent.click(screen.getByTestId('event-template-history-add-面談 松岡'));
    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    expect(createMutate.mock.calls[0]![0]).toEqual({
      staff_id: null,
      title: '面談 松岡',
      event_type: 'event',
      start_time: '14:00',
      end_time: '15:00',
    });
    // 追加済みの行は「✓ 追加しました」に変わる。
    expect(await screen.findByText('✓ 追加しました')).toBeTruthy();
  });

  it('5b. 時刻の無い履歴は時刻 null で作成する', async () => {
    setup([], HISTORY);
    fireEvent.click(screen.getByTestId('event-template-history-toggle'));
    fireEvent.click(screen.getByTestId('event-template-history-add-打合せ：川名'));
    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    expect(createMutate.mock.calls[0]![0]).toMatchObject({
      title: '打合せ：川名',
      start_time: null,
      end_time: null,
    });
  });
});

describe('EventTemplatesCard — 権限', () => {
  it('6. canEdit=false なら一覧は見えるが操作ボタンは出ない', () => {
    setup([tpl({ id: 'a' })], [], false);
    expect(screen.getByText('カンファレンス')).toBeTruthy();
    expect(screen.queryByTestId('event-template-edit-a')).toBeNull();
    expect(screen.queryByTestId('event-template-toggle-a')).toBeNull();
    expect((screen.getByTestId('event-template-add') as HTMLButtonElement).disabled).toBe(true);
  });
});
