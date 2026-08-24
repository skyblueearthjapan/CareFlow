/**
 * EventAddDialog (スタッフマスタ・1名宛) — Wave 2-D の追加分。
 *
 * 検証:
 *   1. 📋 ひな形バーに共通 + このスタッフの個人グループが出る
 *   2. ひな形を選ぶとフォームが埋まり、🔒 も payload に載る
 *   3. 時刻 null のひな形は現在値を保持する
 *   4. ☆ ひな形に保存 (共通 / 個人)
 *   5. 📌 毎週固定 → このスタッフのみ × 曜日で bulk 作成
 *   6. 本体の登録が失敗したら ☆/📌 は走らない
 *   7. admin 以外は ☆/📌 が無効
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver ??= ResizeObserverStub;

const { mockToast, mockCreate, mockCreateTemplate, mockBulkDefaults, mockTemplates, mockRole } =
  vi.hoisted(() => ({
    mockToast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
    mockCreate: vi.fn(),
    mockCreateTemplate: vi.fn(),
    mockBulkDefaults: vi.fn(),
    mockTemplates: { value: [] as unknown[] },
    mockRole: { value: 'admin' as string },
  }));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { role: mockRole.value } }, status: 'authenticated' }),
}));

vi.mock('@/lib/queries/staff-events', () => ({
  useCreateEvent: () => ({ mutateAsync: mockCreate, isPending: false }),
}));

vi.mock('@/lib/queries/event-templates', () => ({
  useEventTemplates: () => ({ data: mockTemplates.value }),
  useCreateEventTemplate: () => ({ mutateAsync: mockCreateTemplate, isPending: false }),
}));

vi.mock('@/lib/queries/staff-event-defaults', () => ({
  useBulkCreateEventDefaults: () => ({ mutateAsync: mockBulkDefaults, isPending: false }),
}));

import { EventAddDialog } from '../EventAddDialog';

const STAFF_ID = '11111111-1111-1111-1111-111111111111';

const SHARED_TEMPLATE = {
  id: 'tpl-shared',
  staff_id: null,
  title: '社内研修（Zoom）',
  event_type: 'training' as const,
  start_time: '14:00',
  end_time: '15:30',
  blocking: false,
  note: null,
  sort_order: 0,
  is_active: true,
  is_shared: true,
};

const PERSONAL_TEMPLATE = {
  id: 'tpl-personal',
  staff_id: STAFF_ID,
  title: 'サービス担当者会議',
  event_type: 'event' as const,
  start_time: null,
  end_time: null,
  blocking: true,
  note: '外せない会議',
  sort_order: 0,
  is_active: true,
  is_shared: false,
};

function renderDialog() {
  const onOpenChange = vi.fn();
  render(
    <EventAddDialog
      staffId={STAFF_ID}
      open
      onOpenChange={onOpenChange}
      defaultDate="2026-05-06"
      defaultStart="09:00"
      defaultEnd="10:00"
    />,
  );
  return { onOpenChange };
}

function submit(title?: string) {
  if (title !== undefined) {
    fireEvent.change(screen.getByLabelText('タイトル'), { target: { value: title } });
  }
  fireEvent.click(screen.getByRole('button', { name: '保存' }));
}

describe('EventAddDialog — ひな形 / ☆保存 / 📌固定 (Wave 2-D)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockTemplates.value = [SHARED_TEMPLATE, PERSONAL_TEMPLATE];
    mockRole.value = 'admin';
    mockCreate.mockResolvedValue({});
    mockCreateTemplate.mockResolvedValue({});
    mockBulkDefaults.mockResolvedValue({ created: 6, skipped: 0 });
  });

  it('1. 共通 + このスタッフの個人グループが出る', () => {
    renderDialog();
    const select = screen.getByTestId('staff-event-template-select');
    expect(select.querySelector('optgroup[label="共通"]')).not.toBeNull();
    expect(select.querySelector('optgroup[label="このスタッフの個人ひな形"]')).not.toBeNull();
  });

  it('2. ひな形を選ぶとフォームが埋まり 🔒 も payload に載る', async () => {
    renderDialog();
    fireEvent.change(screen.getByTestId('staff-event-template-select'), {
      target: { value: 'tpl-personal' },
    });
    expect(screen.getByTestId('staff-event-template-applied')).toHaveTextContent('🔒付き');
    expect(screen.getByLabelText('タイトル')).toHaveValue('サービス担当者会議');
    submit();
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        title: 'サービス担当者会議',
        // 時刻 null のひな形 → フォームの現在値を保持。
        start_time: '09:00',
        end_time: '10:00',
        note: '外せない会議',
        blocking: true,
      }),
    );
  });

  it('3. 時刻ありのひな形は時刻も入る', async () => {
    renderDialog();
    fireEvent.change(screen.getByTestId('staff-event-template-select'), {
      target: { value: 'tpl-shared' },
    });
    submit();
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        title: '社内研修（Zoom）',
        start_time: '14:00',
        end_time: '15:30',
        type: '研修',
        blocking: false,
      }),
    );
  });

  it('4. ☆ ひな形に保存 (既定=共通 / 個人も選べる)', async () => {
    renderDialog();
    fireEvent.click(screen.getByTestId('staff-event-save-template'));
    submit('カンファレンス');
    await waitFor(() => expect(mockCreateTemplate).toHaveBeenCalledTimes(1));
    expect(mockCreateTemplate.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        staff_id: null,
        title: 'カンファレンス',
        event_type: 'training',
        start_time: '09:00',
        end_time: '10:00',
      }),
    );
  });

  it('4b. 保存先に個人を選ぶと staff_id が入る', async () => {
    renderDialog();
    fireEvent.click(screen.getByTestId('staff-event-save-template'));
    fireEvent.change(screen.getByTestId('staff-event-template-scope'), {
      target: { value: 'personal' },
    });
    submit('個人面談');
    await waitFor(() => expect(mockCreateTemplate).toHaveBeenCalledTimes(1));
    expect(mockCreateTemplate.mock.calls[0]![0]).toEqual(
      expect.objectContaining({ staff_id: STAFF_ID, title: '個人面談' }),
    );
  });

  it('5. 📌 毎週固定はこのスタッフのみ × 選択曜日で bulk 作成する', async () => {
    mockBulkDefaults.mockResolvedValue({ created: 5, skipped: 1 });
    renderDialog();
    fireEvent.click(screen.getByTestId('staff-event-fix-weekly'));
    // 2026-05-06 は水曜 → 既定は 水 (index 2)。
    expect(screen.getByTestId('staff-event-weekday-2')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByTestId('staff-event-weekday-all'));
    submit('朝会');
    await waitFor(() => expect(mockBulkDefaults).toHaveBeenCalledTimes(1));
    expect(mockBulkDefaults.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        staff_ids: [STAFF_ID],
        weekdays: [0, 1, 2, 3, 4, 5],
        title: '朝会',
      }),
    );
    const msgs = mockToast.success.mock.calls.map((c) => String(c[0]));
    expect(msgs.some((m) => m.includes('固定イベントを 5件 登録しました'))).toBe(true);
    expect(msgs.some((m) => m.includes('1件は登録済みのためスキップ'))).toBe(true);
  });

  it('6. 本体の登録が失敗したら ☆/📌 は走らない', async () => {
    mockCreate.mockRejectedValue(new Error('boom'));
    const { onOpenChange } = renderDialog();
    fireEvent.click(screen.getByTestId('staff-event-save-template'));
    fireEvent.click(screen.getByTestId('staff-event-fix-weekly'));
    submit('失敗する予定');
    await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(mockCreateTemplate).not.toHaveBeenCalled();
    expect(mockBulkDefaults).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it('7. admin 以外は ☆/📌 が無効', () => {
    mockRole.value = 'staff';
    renderDialog();
    expect(screen.getByTestId('staff-event-save-template')).toBeDisabled();
    expect(screen.getByTestId('staff-event-fix-weekly')).toBeDisabled();
  });
});
