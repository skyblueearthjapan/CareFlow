/**
 * EventEditDialog — 「☆ ひな形にする」(PO 2026-08-25) の vitest。
 *
 * 縛る挙動:
 *   1. フッターの ☆ ボタンで、**いま入力欄にある内容** (保存前の手直し込み) が
 *      ひな形ダイアログへ引き継がれる (元のイベント値ではない)
 *   2. 🔒 (blocking) はフォームではなくローカル state から引き継ぐ
 *   3. 親ダイアログが閉じるとひな形ダイアログも閉じる (取り残しなし)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver ??= ResizeObserverStub;

const { mockCreateTemplate } = vi.hoisted(() => ({ mockCreateTemplate: vi.fn() }));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));
vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock('@/lib/queries/staff-events', () => ({
  useUpdateEvent: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteEvent: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('@/lib/queries/event-templates', () => ({
  useEventTemplates: () => ({ data: [] }),
  useEventTemplateHistorySuggestions: () => ({ data: [] }),
  useCreateEventTemplate: () => ({ mutateAsync: mockCreateTemplate, isPending: false }),
  useUpdateEventTemplate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteEventTemplate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useReorderEventTemplates: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import { EventEditDialog } from '../EventEditDialog';

const STAFF_ID = '11111111-1111-1111-1111-111111111111';

const EVENT = {
  id: 'ev-1',
  staff_id: STAFF_ID,
  date: '2026-08-27',
  start_time: '13:00',
  end_time: '14:00',
  type: 'イベント' as const,
  title: 'カンファレンス',
  note: null,
  source: 'manual',
  blocking: true,
  cancelled_at: null,
};

function renderDialog(open = true) {
  const onOpenChange = vi.fn();
  const utils = render(
    <EventEditDialog staffId={STAFF_ID} event={EVENT} open={open} onOpenChange={onOpenChange} />,
  );
  return { onOpenChange, ...utils };
}

describe('EventEditDialog — ☆ ひな形にする', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCreateTemplate.mockResolvedValue({});
  });

  it('1. 保存前の手直し込みの入力内容がひな形ダイアログへ引き継がれる', () => {
    renderDialog();
    const editDialog = screen.getByRole('dialog', { name: '研修・イベントを編集' });
    // 元のタイトルから手直しする (未保存)
    fireEvent.change(within(editDialog).getByLabelText('タイトル'), {
      target: { value: 'カンファレンス（毎月）' },
    });
    fireEvent.change(within(editDialog).getByLabelText('開始時刻'), {
      target: { value: '12:00' },
    });

    fireEvent.click(screen.getByTestId('event-edit-save-template'));

    const tplDialog = screen.getByRole('dialog', { name: 'ひな形を追加' });
    expect(within(tplDialog).getByLabelText('タイトル')).toHaveValue('カンファレンス（毎月）');
    expect(within(tplDialog).getByLabelText('種別')).toHaveValue('event');
    // 保存先 (共通 / このスタッフの個人) が選べる
    expect(within(tplDialog).getByTestId('event-template-scope')).toBeInTheDocument();
    // 引き継いだ内容で作成される (staff_id=null=共通が既定)
    fireEvent.click(within(tplDialog).getByRole('button', { name: '保存する' }));
    expect(mockCreateTemplate).toHaveBeenCalledTimes(1);
    expect(mockCreateTemplate.mock.calls[0][0]).toMatchObject({
      title: 'カンファレンス（毎月）',
      event_type: 'event',
      start_time: '12:00',
      end_time: '14:00',
      blocking: true,
      staff_id: null,
    });
  });

  it('2. 🔒 を OFF にしてから ☆ を押すと blocking=false で引き継ぐ', () => {
    renderDialog();
    fireEvent.click(screen.getByTestId('event-blocking-toggle'));
    fireEvent.click(screen.getByTestId('event-edit-save-template'));
    const tplDialog = screen.getByRole('dialog', { name: 'ひな形を追加' });
    fireEvent.click(within(tplDialog).getByRole('button', { name: '保存する' }));
    expect(mockCreateTemplate.mock.calls[0][0]).toMatchObject({ blocking: false });
  });

  it('3. 親ダイアログが閉じるとひな形ダイアログも閉じる', () => {
    const { rerender } = renderDialog();
    fireEvent.click(screen.getByTestId('event-edit-save-template'));
    expect(screen.getByRole('dialog', { name: 'ひな形を追加' })).toBeInTheDocument();

    rerender(
      <EventEditDialog staffId={STAFF_ID} event={EVENT} open={false} onOpenChange={vi.fn()} />,
    );
    expect(screen.queryByRole('dialog', { name: 'ひな形を追加' })).toBeNull();
  });
});
