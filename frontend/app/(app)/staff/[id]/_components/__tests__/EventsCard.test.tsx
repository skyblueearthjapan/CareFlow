/**
 * EventsCard (詳細 / 編集ページ共有の「研修日 / イベント」カード) vitest。
 *
 * PO 2026-08-25 の 3 点を縛る:
 *   1. 開いた瞬間は「今週」タブ — BE へは今週の月〜日 (from/to) と asc で問い合わせる
 *   2. 行の「☆ ひな形」1 クリックでひな形ダイアログが開き、タイトル/時刻/種別が引き継がれる
 *   3. 「⋯」メニューには 📌 だけが残る (☆ は直接ボタンへ昇格)
 *   4. canEdit=false では ☆ / ⋯ が出ず、編集ボタンは無効
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver ??= ResizeObserverStub;

const { mockUseStaffEvents, mockEvents } = vi.hoisted(() => ({
  mockUseStaffEvents: vi.fn(),
  mockEvents: { value: [] as unknown[] },
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));
vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { role: 'admin' } }, status: 'authenticated' }),
}));

vi.mock('@/lib/queries/staff-events', () => ({
  useStaffEvents: mockUseStaffEvents,
  useCreateEvent: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateEvent: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteEvent: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('@/lib/queries/event-templates', () => ({
  useEventTemplates: () => ({ data: [] }),
  useEventTemplateHistorySuggestions: () => ({ data: [] }),
  useCreateEventTemplate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateEventTemplate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteEventTemplate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useReorderEventTemplates: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('@/lib/queries/staff-event-defaults', () => ({
  useBulkCreateEventDefaults: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteEventDefault: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useStaffEventDefaults: () => ({ data: [], isLoading: false, isError: false, error: null }),
}));

import { EventsCard } from '../EventsCard';

const STAFF_ID = '11111111-1111-1111-1111-111111111111';

/** 2026-08-27 (木)。今週 = 8/24(月)〜8/30(日)。 */
const NOW = new Date(2026, 7, 27, 10, 0, 0);

const EVENT_MEETING = {
  id: 'ev-1',
  staff_id: STAFF_ID,
  date: '2026-08-27',
  start_time: '13:00',
  end_time: '14:00',
  type: 'イベント',
  title: 'カンファレンス',
  note: '第2会議室',
  source: 'manual',
  blocking: false,
  cancelled_at: null,
};

const EVENT_TRAINING = {
  id: 'ev-2',
  staff_id: STAFF_ID,
  date: '2026-08-28',
  start_time: '09:30',
  end_time: '11:00',
  type: '研修',
  title: '感染対策研修',
  note: null,
  source: 'manual',
  blocking: true,
  cancelled_at: null,
};

function renderCard(canEdit = true) {
  return render(<EventsCard staffId={STAFF_ID} canEdit={canEdit} />);
}

describe('EventsCard — 既定タブ / ☆ ひな形 / ⋯ (PO 2026-08-25)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(NOW);
    mockEvents.value = [EVENT_MEETING, EVENT_TRAINING];
    mockUseStaffEvents.mockImplementation(() => ({
      data: mockEvents.value,
      isLoading: false,
      isError: false,
      error: null,
    }));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('1. 開いた瞬間は「今週」タブで、BE へ今週の月〜日を asc で問い合わせる', () => {
    renderCard();
    expect(screen.getByRole('tab', { name: '今週' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: '今後' })).toHaveAttribute('aria-selected', 'false');

    // 1 本目 = 絞り込み付き / 2 本目 = 期間タブのみ (件数の分母)。どちらも今週の範囲。
    const calls = mockUseStaffEvents.mock.calls;
    expect(calls.length).toBeGreaterThanOrEqual(2);
    for (const [staffId, range, filters] of calls) {
      expect(staffId).toBe(STAFF_ID);
      expect(range).toEqual({ from: '2026-08-24', to: '2026-08-30' });
      expect(filters).toMatchObject({ order: 'asc' });
    }
    // 今後タブへ切り替えると from=今日 / to=+180日 になる
    fireEvent.click(screen.getByRole('tab', { name: '今後' }));
    const last = mockUseStaffEvents.mock.calls.at(-1)!;
    expect(last[1]).toEqual({ from: '2026-08-27', to: '2027-02-23' });
  });

  it('2. 行の「☆ ひな形」1 クリックでひな形ダイアログが開き、内容が引き継がれる', () => {
    renderCard();
    expect(screen.queryByRole('dialog')).toBeNull();

    fireEvent.click(screen.getByTestId('event-row-save-template-ev-2'));

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('ひな形を追加')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('タイトル')).toHaveValue('感染対策研修');
    expect(within(dialog).getByLabelText('種別')).toHaveValue('training');
    // 保存先 (共通 / このスタッフの個人) を選べる
    expect(screen.getByTestId('event-template-scope')).toBeInTheDocument();
  });

  it('3. 「⋯」メニューには 📌 毎週固定にする… だけが残る', () => {
    renderCard();
    fireEvent.click(screen.getByTestId('event-row-menu-ev-1'));
    expect(screen.getByTestId('event-row-pin-ev-1')).toHaveTextContent('📌 毎週固定にする');
    // ☆ はメニューではなく行の直接ボタン (1 クリック)
    expect(screen.getByTestId('event-row-save-template-ev-1')).toHaveTextContent('☆ ひな形');
  });

  it('4. canEdit=false では ☆ / ⋯ が出ず、編集・追加は無効化される', () => {
    renderCard(false);
    expect(screen.queryByTestId('event-row-save-template-ev-1')).toBeNull();
    expect(screen.queryByTestId('event-row-menu-ev-1')).toBeNull();
    for (const btn of screen.getAllByRole('button', { name: '編集' })) {
      expect(btn).toBeDisabled();
    }
    expect(screen.getByRole('button', { name: '追加' })).toBeDisabled();
  });
});
