/**
 * TimelineEventAddDialog — D-1 (複数スタッフへの打合せ一括登録) テスト。
 *
 * 検証:
 *   1. 既定選択 (起動元の列の担当) がチェック済みで開く
 *   2. 追加選択して送信 → 選択スタッフごとに mutateAsync が呼ばれる (同一 payload)
 *   3. 0 名選択で送信 → エラーメッセージを出し mutateAsync を呼ばない
 *   4. 部分失敗 → 成功済みを選択から外してダイアログ維持 (再送で二重登録しない)
 *
 * Wave 2-D (staff-event-history-design.md §2 Phase 2/3):
 *   5. 📋 ひな形を選ぶとフォームが埋まる (🔒 も引き継ぐ)
 *   6. 個人ひな形グループは「ちょうど 1 名選択中」のときだけ出る
 *   7. ☆ ひな形に保存 → 登録成功後に createTemplate が呼ばれる
 *   8. 📌 毎週固定 → 登録成功後に bulk 作成が呼ばれる (選択スタッフ全員 × 曜日)
 *   9. 本体が 1 件も成功しなければ ☆/📌 は走らない
 *  10. 部分失敗の再送でも ☆/📌 は二重に走らない
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

const {
  mockToast,
  mockCreateForStaff,
  mockCreateTemplate,
  mockBulkDefaults,
  mockTemplates,
  mockSessionRole,
} = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  mockCreateForStaff: vi.fn(),
  mockCreateTemplate: vi.fn(),
  mockBulkDefaults: vi.fn(),
  mockTemplates: { value: [] as unknown[] },
  mockSessionRole: { value: 'admin' as string },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { role: mockSessionRole.value } }, status: 'authenticated' }),
}));

vi.mock('@/lib/queries/staff-events', () => ({
  useCreateEventForStaff: () => ({ mutateAsync: mockCreateForStaff, isPending: false }),
}));

vi.mock('@/lib/queries/event-templates', () => ({
  useEventTemplates: () => ({ data: mockTemplates.value }),
  useCreateEventTemplate: () => ({ mutateAsync: mockCreateTemplate, isPending: false }),
}));

vi.mock('@/lib/queries/staff-event-defaults', () => ({
  useBulkCreateEventDefaults: () => ({ mutateAsync: mockBulkDefaults, isPending: false }),
}));

import { TimelineEventAddDialog } from '../TimelineEventAddDialog';

const STAFF = [
  { id: 's-1', name: '田中 一郎' },
  { id: 's-2', name: '佐藤 花子' },
  { id: 's-3', name: '管理 太郎' },
];

const SHARED_TEMPLATE = {
  id: 'tpl-shared',
  staff_id: null,
  title: 'サービス担当者会議',
  event_type: 'event' as const,
  start_time: '13:00',
  end_time: '14:00',
  blocking: true,
  note: '外せない会議',
  sort_order: 0,
  is_active: true,
  is_shared: true,
};

const PERSONAL_TEMPLATE = {
  id: 'tpl-personal',
  staff_id: 's-1',
  title: '面談 松岡',
  event_type: 'training' as const,
  start_time: null,
  end_time: null,
  blocking: false,
  note: null,
  sort_order: 0,
  is_active: true,
  is_shared: false,
};

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
    mockTemplates.value = [SHARED_TEMPLATE, PERSONAL_TEMPLATE];
    mockSessionRole.value = 'admin';
    mockCreateTemplate.mockResolvedValue({});
    mockBulkDefaults.mockResolvedValue({ created: 6, skipped: 0 });
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

describe('TimelineEventAddDialog — ひな形 / ☆保存 / 📌固定 (Wave 2-D)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockTemplates.value = [SHARED_TEMPLATE, PERSONAL_TEMPLATE];
    mockSessionRole.value = 'admin';
    mockCreateForStaff.mockResolvedValue({});
    mockCreateTemplate.mockResolvedValue({});
    mockBulkDefaults.mockResolvedValue({ created: 6, skipped: 0 });
  });

  it('5. ひな形を選ぶとタイトル/時刻/備考/🔒 がフォームに入る', async () => {
    renderDialog();
    fireEvent.change(screen.getByTestId('tl-event-template-select'), {
      target: { value: 'tpl-shared' },
    });
    expect(screen.getByTestId('tl-event-template-applied')).toHaveTextContent(
      'ひな形の内容を反映しました',
    );
    expect(screen.getByTestId('tl-event-template-applied')).toHaveTextContent('🔒付き');
    expect(screen.getByLabelText('タイトル')).toHaveValue('サービス担当者会議');

    fireEvent.click(screen.getByTestId('tl-event-submit'));
    await waitFor(() => expect(mockCreateForStaff).toHaveBeenCalledTimes(1));
    expect(mockCreateForStaff.mock.calls[0]![0].payload).toEqual(
      expect.objectContaining({
        title: 'サービス担当者会議',
        start_time: '13:00',
        end_time: '14:00',
        note: '外せない会議',
        blocking: true,
      }),
    );
  });

  it('5b. 時刻が null のひな形はフォームの現在値を保持する', async () => {
    renderDialog();
    fireEvent.change(screen.getByTestId('tl-event-template-select'), {
      target: { value: 'tpl-personal' },
    });
    fireEvent.click(screen.getByTestId('tl-event-submit'));
    await waitFor(() => expect(mockCreateForStaff).toHaveBeenCalledTimes(1));
    expect(mockCreateForStaff.mock.calls[0]![0].payload).toEqual(
      expect.objectContaining({ title: '面談 松岡', start_time: '14:00', end_time: '15:00' }),
    );
  });

  it('6. 個人ひな形グループは 1 名選択中のときだけ出る', () => {
    renderDialog();
    const select = screen.getByTestId('tl-event-template-select');
    expect(select.querySelector('optgroup[label="共通"]')).not.toBeNull();
    expect(select.querySelector('optgroup[label="田中 一郎さんの個人ひな形"]')).not.toBeNull();
    // 2 名目を選ぶと個人グループは消える (共通のみ)。
    fireEvent.click(screen.getByTestId('tl-event-staff-s-2'));
    expect(
      screen.getByTestId('tl-event-template-select').querySelector('optgroup[label="共通"]'),
    ).not.toBeNull();
    expect(
      screen
        .getByTestId('tl-event-template-select')
        .querySelector('optgroup[label="田中 一郎さんの個人ひな形"]'),
    ).toBeNull();
  });

  it('7. ☆ ひな形に保存: 登録成功後に共通ひな形として作成される', async () => {
    renderDialog();
    fireEvent.click(screen.getByTestId('tl-event-save-template'));
    await fillTitleAndSubmit('カンファレンス');
    await waitFor(() => expect(mockCreateTemplate).toHaveBeenCalledTimes(1));
    expect(mockCreateTemplate.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        staff_id: null,
        title: 'カンファレンス',
        event_type: 'event',
        start_time: '14:00',
        end_time: '15:00',
        blocking: false,
      }),
    );
  });

  it('7b. 保存先に個人を選ぶと staff_id が入る', async () => {
    renderDialog();
    fireEvent.click(screen.getByTestId('tl-event-save-template'));
    fireEvent.change(screen.getByTestId('tl-event-template-scope'), {
      target: { value: 'personal' },
    });
    await fillTitleAndSubmit('面談');
    await waitFor(() => expect(mockCreateTemplate).toHaveBeenCalledTimes(1));
    expect(mockCreateTemplate.mock.calls[0]![0]).toEqual(
      expect.objectContaining({ staff_id: 's-1', title: '面談' }),
    );
  });

  it('8. 📌 毎週固定: 選択スタッフ全員 × 曜日で bulk 作成し件数を toast する', async () => {
    mockBulkDefaults.mockResolvedValue({ created: 10, skipped: 2 });
    renderDialog();
    fireEvent.click(screen.getByTestId('tl-event-staff-s-2'));
    fireEvent.click(screen.getByTestId('tl-event-fix-weekly'));
    // 既定曜日 = 入力中の日付 (2026-05-04 = 月) の曜日。
    expect(screen.getByTestId('tl-event-weekday-0')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByTestId('tl-event-weekday-all'));
    await fillTitleAndSubmit('朝会');
    await waitFor(() => expect(mockBulkDefaults).toHaveBeenCalledTimes(1));
    expect(mockBulkDefaults.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        staff_ids: ['s-1', 's-2'],
        weekdays: [0, 1, 2, 3, 4, 5],
        start_time: '14:00',
        end_time: '15:00',
        title: '朝会',
      }),
    );
    const msgs = mockToast.success.mock.calls.map((c) => String(c[0]));
    expect(msgs.some((m) => m.includes('固定イベントを 10件 登録しました'))).toBe(true);
    expect(msgs.some((m) => m.includes('2件は登録済みのためスキップ'))).toBe(true);
  });

  it('9. 本体が 1 件も成功しないと ☆/📌 は走らない', async () => {
    mockCreateForStaff.mockRejectedValue(new Error('boom'));
    renderDialog();
    fireEvent.click(screen.getByTestId('tl-event-save-template'));
    fireEvent.click(screen.getByTestId('tl-event-fix-weekly'));
    await fillTitleAndSubmit();
    await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(mockCreateTemplate).not.toHaveBeenCalled();
    expect(mockBulkDefaults).not.toHaveBeenCalled();
  });

  it('10. 部分失敗の再送でも ☆/📌 は二重に走らない', async () => {
    mockCreateForStaff.mockImplementation(({ staffId }: { staffId: string }) =>
      staffId === 's-2' ? Promise.reject(new Error('boom')) : Promise.resolve({}),
    );
    renderDialog({ defaultStaffIds: ['s-1', 's-2'] });
    fireEvent.click(screen.getByTestId('tl-event-save-template'));
    fireEvent.click(screen.getByTestId('tl-event-fix-weekly'));
    await fillTitleAndSubmit();
    await waitFor(() => expect(mockCreateTemplate).toHaveBeenCalledTimes(1));
    expect(mockBulkDefaults).toHaveBeenCalledTimes(1);

    // 再送 (s-2 のみ成功させる)。
    mockCreateForStaff.mockResolvedValue({});
    fireEvent.click(screen.getByTestId('tl-event-submit'));
    await waitFor(() => expect(mockCreateForStaff).toHaveBeenCalledTimes(3));
    expect(mockCreateTemplate).toHaveBeenCalledTimes(1);
    expect(mockBulkDefaults).toHaveBeenCalledTimes(1);
  });

  it('11. admin 以外は ☆/📌 のチェックが無効化される', () => {
    mockSessionRole.value = 'staff';
    renderDialog();
    expect(screen.getByTestId('tl-event-save-template')).toBeDisabled();
    expect(screen.getByTestId('tl-event-fix-weekly')).toBeDisabled();
  });
});
