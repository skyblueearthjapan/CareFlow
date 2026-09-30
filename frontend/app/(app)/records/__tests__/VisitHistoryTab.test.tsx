/**
 * `/records` 打刻履歴タブの vitest（visit-history-design-2026-09-30 §4・§5）。
 *
 * 縛る挙動:
 *   1. 既定は今月。期間の切り替え・‹ ›・期間指定で BE への from / to が変わる
 *   2. 絞り込み・並び・検索・ページは BE パラメータに乗り、条件を変えると 1 ページ目へ戻る
 *   3. 看護師別・患者別は見出し行（件数・打刻あり件数）を挟む
 *   4. staff ロールはスタッフ・拠点のセレクトが無効（理由を title に出す）
 *   5. 空状態・読み込み失敗・期間が長すぎるとき
 *   6. 時刻は JST で出す
 *   7. 行クリックで詳細、Excel / A4 はいまの絞り込みで出す
 *   8. 実績の時刻の調整（actual-time-adjust-design-2026-09-30 §8-2）: 「調整」バッジ・
 *      読取時刻の併記・集計帯・絞り込み・見出し行の件数（BE の groups）・詳細からの調整
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver ??= ResizeObserverStub;

const {
  mockUseVisitHistory,
  mockExport,
  mockReport,
  mockAdjust,
  mockReset,
  mockDownload,
  mockToast,
  mockRole,
  mockSessionStatus,
} = vi.hoisted(() => ({
  mockUseVisitHistory: vi.fn(),
  mockExport: vi.fn(),
  mockReport: vi.fn(),
  mockAdjust: vi.fn(),
  mockReset: vi.fn(),
  mockDownload: vi.fn(),
  mockToast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  mockRole: { value: 'admin' as string },
  mockSessionStatus: { value: 'authenticated' as string },
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data:
      mockSessionStatus.value === 'loading'
        ? null
        : { user: { role: mockRole.value, staffId: 'st-1' }, accessToken: 'tok' },
    status: mockSessionStatus.value,
  }),
}));

vi.mock('@/components/ui/sonner', () => ({ toast: mockToast }));

vi.mock('@/lib/queries/visit-history', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useVisitHistory: (...a: unknown[]) => mockUseVisitHistory(...a),
  useVisitHistoryExport: () => ({ mutateAsync: mockExport, isPending: false }),
  useVisitHistoryReport: () => ({ mutateAsync: mockReport, isPending: false }),
  useAdjustVisitActualTime: () => ({ mutateAsync: mockAdjust, isPending: false }),
  useResetVisitActualTime: () => ({ mutateAsync: mockReset, isPending: false }),
}));

vi.mock('@/lib/api/patientsExcel', () => ({
  triggerBlobDownload: (...a: unknown[]) => mockDownload(...a),
}));

vi.mock('@/lib/queries/offices', () => ({
  useOffices: () => ({ offices: [{ id: 'of-1', name: '都賀' }], allOffices: [] }),
}));
vi.mock('@/lib/queries/staff', () => ({
  useStaffList: () => ({ data: [{ id: 'st-1', name: '川名 幸子' }] }),
}));
vi.mock('@/lib/queries/patients', () => ({
  usePatients: () => ({ data: { items: [] }, isLoading: false }),
}));

import { ApiError } from '@/lib/api-client';

import { VisitHistoryTab } from '../_components/VisitHistoryTab';

/** 2026-09-30 (水)。今週 = 9/28(月)〜10/4(日)、今月 = 9/1〜9/30。 */
const NOW = new Date(2026, 8, 30, 15, 10, 0);

function makeRow(over: Record<string, unknown> = {}) {
  return {
    visit_id: 'v-1',
    visit_date: '2026-09-29',
    office_id: 'of-1',
    office_name: '都賀',
    patient_id: 'p-1',
    patient_name: '山田 花子',
    start_time: '13:00:00',
    end_time: '13:40:00',
    planned_staff_id: 'st-1',
    planned_staff_name: '川名 幸子',
    actual_staff_id: 'st-1',
    actual_staff_name: '川名 幸子',
    // 03:56Z = JST 12:56、04:40Z = JST 13:40。
    arrival_at: '2026-09-29T03:56:00Z',
    departure_at: '2026-09-29T04:40:00Z',
    stay_minutes: 44,
    checkin_source: 'qr',
    match_status: 'ok',
    is_substitute: false,
    is_unplanned: false,
    is_cancelled: false,
    state: 'done',
    remarks: [],
    ...over,
  };
}

const SUMMARY = { visits: 40, with_arrival: 10, with_departure: 8, no_departure: 2, none: 30 };

function setData(
  items: unknown[],
  total = items.length,
  summary: Record<string, number> = SUMMARY,
  groups?: unknown[],
) {
  mockUseVisitHistory.mockReturnValue({
    data: { items, total, summary, groups },
    isLoading: false,
  });
}

/**
 * 到着を 10 分さかのぼって合わせた行: 読取 13:06 → 実績 12:56（JST）。
 * 04:06Z = JST 13:06、03:56Z = JST 12:56、04:10Z = JST 13:10。
 */
function makeAdjustedRow(over: Record<string, unknown> = {}) {
  return makeRow({
    arrival_at: '2026-09-29T03:56:00Z',
    arrival_read_at: '2026-09-29T04:06:00Z',
    departure_at: '2026-09-29T04:40:00Z',
    departure_read_at: '2026-09-29T04:40:00Z',
    arrival_adjusted: true,
    departure_adjusted: false,
    departure_manual: false,
    adjust_allowed: true,
    adjustments: [
      {
        kind: 'arrival',
        reason_label: 'インターホン待ち',
        reason_text: null,
        by_name: '川名 幸子',
        created_at: '2026-09-29T04:10:00Z',
      },
    ],
    remarks: ['時刻調整'],
    ...over,
  });
}

/** 詳細ダイアログを開いて、その中を引く。 */
function openDetail(visitId = 'v-1') {
  fireEvent.click(screen.getByTestId(`history-row-${visitId}`));
  return within(screen.getByTestId('history-detail-dialog'));
}

/** 直近の `useVisitHistory` 呼び出し引数。 */
function lastParams(): Record<string, unknown> {
  const calls = mockUseVisitHistory.mock.calls;
  return (calls[calls.length - 1]?.[0] ?? {}) as Record<string, unknown>;
}

const button = (name: string) => screen.getByRole('button', { name });

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(NOW);
  mockRole.value = 'admin';
  mockSessionStatus.value = 'authenticated';
  mockUseVisitHistory.mockReset();
  mockExport.mockReset();
  mockReport.mockReset();
  mockAdjust.mockReset();
  mockReset.mockReset();
  mockAdjust.mockResolvedValue({});
  mockReset.mockResolvedValue({});
  mockDownload.mockReset();
  Object.values(mockToast).forEach((fn) => fn.mockReset());
  setData([makeRow()]);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('VisitHistoryTab — 期間', () => {
  it('開いた瞬間は今月 — 月初〜月末・日付順・50 件の窓で問い合わせる', () => {
    render(<VisitHistoryTab />);
    const p = lastParams();
    expect(p.from).toBe('2026-09-01');
    expect(p.to).toBe('2026-09-30');
    expect(p.sort).toBe('date');
    expect(p.limit).toBe(50);
    expect(p.offset).toBe(0);
    expect(p.enabled).toBe(true);
    expect(button('今月')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('history-range-label')).toHaveTextContent(
      '2026/09/01 (火) 〜 09/30 (水)',
    );
  });

  it('今週 / 先週 / 先月 で from / to が変わる', () => {
    render(<VisitHistoryTab />);
    fireEvent.click(button('今週'));
    expect(lastParams()).toMatchObject({ from: '2026-09-28', to: '2026-10-04' });

    fireEvent.click(button('先週'));
    expect(lastParams()).toMatchObject({ from: '2026-09-21', to: '2026-09-27' });

    fireEvent.click(button('先月'));
    expect(lastParams()).toMatchObject({ from: '2026-08-01', to: '2026-08-31' });
  });

  it('‹ › は月なら 1 か月、週なら 7 日ぶん送り、選択表示は一致するプリセットへ移る', () => {
    render(<VisitHistoryTab />);
    fireEvent.click(button('前の期間'));
    expect(lastParams()).toMatchObject({ from: '2026-08-01', to: '2026-08-31' });
    expect(button('先月')).toHaveAttribute('aria-pressed', 'true');
    expect(button('今月')).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(button('前の期間'));
    expect(lastParams()).toMatchObject({ from: '2026-07-01', to: '2026-07-31' });
    expect(button('先月')).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(button('今週'));
    fireEvent.click(button('次の期間'));
    expect(lastParams()).toMatchObject({ from: '2026-10-05', to: '2026-10-11' });
  });

  it('期間指定は日付欄が出て、入力した from / to で問い合わせる（逆転は揃える）', () => {
    render(<VisitHistoryTab />);
    fireEvent.click(button('期間指定'));
    expect(button('期間指定')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: '前の期間' })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('期間の開始日'), { target: { value: '2026-09-18' } });
    fireEvent.change(screen.getByLabelText('期間の終了日'), { target: { value: '2026-09-24' } });
    expect(lastParams()).toMatchObject({ from: '2026-09-18', to: '2026-09-24' });

    // 開始日を終了日より後ろにしたら、終了日を開始日に揃える。
    fireEvent.change(screen.getByLabelText('期間の開始日'), { target: { value: '2026-09-28' } });
    expect(lastParams()).toMatchObject({ from: '2026-09-28', to: '2026-09-28' });
  });

  it('92 日を超える期間は問い合わせず、理由を出して出力も止める', () => {
    render(<VisitHistoryTab />);
    fireEvent.click(button('期間指定'));
    fireEvent.change(screen.getByLabelText('期間の開始日'), { target: { value: '2026-06-01' } });
    expect(lastParams().enabled).toBe(false);
    expect(screen.getByTestId('history-range-too-long')).toHaveTextContent('92 日まで');
    expect(screen.queryByTestId('history-table')).not.toBeInTheDocument();
    expect(screen.getByTestId('history-export-button')).toBeDisabled();
    expect(screen.getByTestId('history-print-button')).toBeDisabled();

    // ちょうど 92 日（両端を含む）は通す。
    fireEvent.change(screen.getByLabelText('期間の開始日'), { target: { value: '2026-07-01' } });
    expect(lastParams()).toMatchObject({ from: '2026-07-01', to: '2026-09-30', enabled: true });
  });
});

describe('VisitHistoryTab — 絞り込み・並び・ページ', () => {
  it('スタッフ・拠点・打刻・並びは BE パラメータに乗る', () => {
    render(<VisitHistoryTab />);
    fireEvent.change(screen.getByLabelText('スタッフ'), { target: { value: 'st-1' } });
    expect(lastParams().staffId).toBe('st-1');

    fireEvent.change(screen.getByLabelText('拠点'), { target: { value: 'of-1' } });
    expect(lastParams().officeId).toBe('of-1');

    fireEvent.change(screen.getByLabelText('打刻'), { target: { value: 'nodep' } });
    expect(lastParams().state).toBe('nodep');

    fireEvent.click(button('患者別'));
    expect(lastParams().sort).toBe('patient');
  });

  it('打刻の選択肢に「時刻の調整あり」があり、state=adjusted で問い合わせる', () => {
    render(<VisitHistoryTab />);
    const labels = within(screen.getByLabelText('打刻'))
      .getAllByRole('option')
      .map((o) => o.textContent);
    expect(labels).toEqual([
      '打刻: すべて',
      '打刻あり',
      '退出なし',
      '打刻なし',
      '時刻の調整あり',
      '代行・予定外',
    ]);
    fireEvent.change(screen.getByLabelText('打刻'), { target: { value: 'adjusted' } });
    expect(lastParams().state).toBe('adjusted');
  });

  it('検索は 300ms デバウンスし、1 文字の間は送らず案内を出す', () => {
    render(<VisitHistoryTab />);
    const box = screen.getByLabelText('打刻履歴を検索');
    fireEvent.change(box, { target: { value: '山' } });
    act(() => {
      vi.advanceTimersByTime(350);
    });
    expect(lastParams().q).toBeNull();
    expect(screen.getByTestId('history-search-hint')).toHaveTextContent('2 文字以上');

    fireEvent.change(box, { target: { value: '山田' } });
    expect(lastParams().q).toBeNull();
    act(() => {
      vi.advanceTimersByTime(350);
    });
    expect(lastParams().q).toBe('山田');
    expect(screen.queryByTestId('history-search-hint')).not.toBeInTheDocument();
  });

  it('50 件を超えるときだけページャを出し、条件を変えると 1 ページ目へ戻る', () => {
    setData([makeRow()], 120);
    render(<VisitHistoryTab />);
    expect(screen.getByTestId('history-pager')).toHaveTextContent('1 / 3 ページ（全120件）');
    fireEvent.click(button('次へ'));
    expect(lastParams().offset).toBe(50);

    fireEvent.change(screen.getByLabelText('打刻'), { target: { value: 'in' } });
    expect(lastParams().offset).toBe(0);

    // 並び・期間でも戻る。
    fireEvent.click(button('次へ'));
    fireEvent.click(button('看護師別'));
    expect(lastParams().offset).toBe(0);
    fireEvent.click(button('次へ'));
    fireEvent.click(button('先月'));
    expect(lastParams().offset).toBe(0);
  });

  it('「絞り込みを解除」は期間と並びを残して絞り込みだけ消す', () => {
    render(<VisitHistoryTab />);
    expect(screen.queryByRole('button', { name: '絞り込みを解除' })).not.toBeInTheDocument();
    fireEvent.click(button('先月'));
    fireEvent.click(button('看護師別'));
    fireEvent.change(screen.getByLabelText('打刻'), { target: { value: 'none' } });
    fireEvent.click(button('絞り込みを解除'));
    expect(lastParams()).toMatchObject({
      from: '2026-08-01',
      to: '2026-08-31',
      sort: 'staff',
      state: null,
      staffId: null,
    });
  });

  it('staff ロールはスタッフ / 拠点のセレクトが無効で、理由を title に出す', () => {
    mockRole.value = 'staff';
    render(<VisitHistoryTab />);
    expect(screen.getByLabelText('スタッフ')).toBeDisabled();
    expect(screen.getByLabelText('拠点')).toBeDisabled();
    expect(screen.getByLabelText('スタッフ')).toHaveAttribute(
      'title',
      '自分の記録のみ表示されます',
    );
    expect(screen.getByLabelText('拠点')).toHaveAttribute('title', '自分の記録のみ表示されます');
    // 打刻・検索は staff でも使える。
    expect(screen.getByLabelText('打刻')).not.toBeDisabled();
    expect(screen.getByLabelText('打刻履歴を検索')).not.toBeDisabled();
  });

  it('セッション取得中はセレクトを無効にしない（管理者に一瞬無効を見せない）', () => {
    mockSessionStatus.value = 'loading';
    render(<VisitHistoryTab />);
    expect(screen.getByLabelText('スタッフ')).not.toBeDisabled();
    expect(screen.getByLabelText('拠点')).not.toBeDisabled();
  });
});

describe('VisitHistoryTab — 表', () => {
  it('到着・退出は JST、予定は HH:MM–HH:MM、滞在は分で出す', () => {
    render(<VisitHistoryTab />);
    const row = within(screen.getByTestId('history-row-v-1'));
    expect(row.getByText('9/29 (火)')).toBeInTheDocument();
    expect(row.getByText('山田 花子')).toBeInTheDocument();
    expect(row.getByText('13:00–13:40')).toBeInTheDocument();
    expect(row.getByText('12:56')).toBeInTheDocument();
    expect(row.getByText('13:40')).toBeInTheDocument();
    expect(row.getByText('44 分')).toBeInTheDocument();
  });

  it('集計帯は BE の summary をそのまま出す（打刻ありは率つき）', () => {
    render(<VisitHistoryTab />);
    expect(screen.getByTestId('history-kpi-visits')).toHaveTextContent('40件');
    expect(screen.getByTestId('history-kpi-arrival')).toHaveTextContent('10件 ・ 25%');
    expect(screen.getByTestId('history-kpi-nodep')).toHaveTextContent('2件');
    expect(screen.getByTestId('history-kpi-none')).toHaveTextContent('30件');
    // summary.adjusted の無い応答（古い BE）では「時刻の調整」を出さない（0 件と偽らない）。
    expect(screen.queryByTestId('history-kpi-adjusted')).not.toBeInTheDocument();
    expect(screen.getByTestId('history-summary')).not.toHaveTextContent('補正');
  });

  it('打刻なし・これからは備考で言い、予定の担当を括弧で出す', () => {
    setData([
      makeRow({
        visit_id: 'v-none',
        actual_staff_id: null,
        actual_staff_name: null,
        arrival_at: null,
        departure_at: null,
        stay_minutes: null,
        checkin_source: null,
        state: 'none',
      }),
      makeRow({
        visit_id: 'v-future',
        actual_staff_name: null,
        arrival_at: null,
        departure_at: null,
        stay_minutes: null,
        state: 'future',
      }),
    ]);
    render(<VisitHistoryTab />);
    const none = within(screen.getByTestId('history-row-v-none'));
    expect(none.getByText('打刻なし')).toBeInTheDocument();
    expect(none.getByText('（予定: 川名 幸子）')).toBeInTheDocument();
    expect(
      within(screen.getByTestId('history-row-v-future')).getByText('これから'),
    ).toBeInTheDocument();
  });

  it('予定外は予定欄が「—」、代行は予定の担当を添え、備考は BE の語彙から作る', () => {
    setData([
      makeRow({
        visit_id: 'v-unp',
        start_time: null,
        end_time: null,
        planned_staff_id: null,
        planned_staff_name: null,
        is_unplanned: true,
        remarks: ['予定外の訪問'],
      }),
      makeRow({
        visit_id: 'v-sub',
        actual_staff_id: 'st-2',
        actual_staff_name: '中村 彩',
        departure_at: null,
        stay_minutes: null,
        is_substitute: true,
        checkin_source: 'manual',
        match_status: 'review',
        state: 'no_departure',
        remarks: ['退出なし', '代行（予定: 川名 幸子）', 'QRなし', '場所 要確認'],
      }),
    ]);
    render(<VisitHistoryTab />);
    const unp = within(screen.getByTestId('history-row-v-unp'));
    expect(unp.getByText('予定外')).toHaveAttribute('title', '予定外の訪問');
    expect(unp.getAllByText('—')).toHaveLength(1); // 予定欄だけ

    const sub = within(screen.getByTestId('history-row-v-sub'));
    expect(sub.getByText('中村 彩')).toBeInTheDocument();
    expect(sub.getByText('予定: 川名 幸子')).toBeInTheDocument();
    expect(sub.getByText('代行')).toHaveAttribute('title', '代行（予定: 川名 幸子）');
    expect(sub.getByText('退出なし')).toBeInTheDocument();
    expect(sub.getByText('QRなし')).toBeInTheDocument();
    expect(sub.getByText('場所 要確認')).toBeInTheDocument();
  });

  it('日付順は見出し行を挟まない', () => {
    render(<VisitHistoryTab />);
    expect(screen.queryByTestId('history-group-row')).not.toBeInTheDocument();
  });

  it('看護師別は看護師ごとに見出し行（件数・打刻あり件数）を挟む', () => {
    setData([
      makeRow({ visit_id: 'a1', actual_staff_name: '中村 彩', planned_staff_name: '中村 彩' }),
      makeRow({
        visit_id: 'a2',
        // 打刻が無い行は予定の担当で並ぶ（BE の並びキーと同じ）。
        actual_staff_name: null,
        planned_staff_name: '中村 彩',
        arrival_at: null,
        departure_at: null,
        stay_minutes: null,
        state: 'none',
      }),
      makeRow({ visit_id: 'b1' }),
    ]);
    render(<VisitHistoryTab />);
    fireEvent.click(button('看護師別'));
    const heads = screen.getAllByTestId('history-group-row');
    expect(heads).toHaveLength(2);
    expect(heads[0]).toHaveTextContent('中村 彩2 件 ・ 打刻あり 1 件');
    expect(heads[1]).toHaveTextContent('川名 幸子1 件 ・ 打刻あり 1 件');

    // 見出し → その看護師の行、の順に並ぶ。
    // 明細行は role="button" なので、表の行は DOM から拾う。
    const order = Array.from(screen.getByTestId('history-table').querySelectorAll('tbody tr')).map(
      (tr) => tr.getAttribute('data-testid'),
    );
    expect(order).toEqual([
      'history-group-row',
      'history-row-a1',
      'history-row-a2',
      'history-group-row',
      'history-row-b1',
    ]);
  });

  it('患者別は患者ごとに見出し行を挟む', () => {
    setData([
      makeRow({ visit_id: 'a1', patient_name: '井上 和子' }),
      makeRow({ visit_id: 'b1' }),
      makeRow({ visit_id: 'b2' }),
    ]);
    render(<VisitHistoryTab />);
    fireEvent.click(button('患者別'));
    const heads = screen.getAllByTestId('history-group-row');
    expect(heads[0]).toHaveTextContent('井上 和子1 件 ・ 打刻あり 1 件');
    expect(heads[1]).toHaveTextContent('山田 花子2 件 ・ 打刻あり 2 件');
  });

  it('ページをまたぐ見出しは「このページに」と書き分ける', () => {
    setData(
      [makeRow({ visit_id: 'a1', actual_staff_name: '中村 彩' }), makeRow({ visit_id: 'b1' })],
      120,
    );
    render(<VisitHistoryTab />);
    fireEvent.click(button('看護師別'));
    const heads = screen.getAllByTestId('history-group-row');
    // 1 ページ目: 先頭は完結、末尾は次のページへ続きうる。
    expect(heads[0]).toHaveTextContent('中村 彩1 件');
    expect(heads[1]).toHaveTextContent('川名 幸子このページに 1 件');
  });

  it('0 件は空状態（らく助）を出し、表は描かない', () => {
    setData([], 0, { visits: 0, with_arrival: 0, with_departure: 0, no_departure: 0, none: 0 });
    render(<VisitHistoryTab />);
    expect(screen.getByTestId('history-empty')).toHaveTextContent(
      'この条件に当てはまる訪問はありません。',
    );
    expect(screen.queryByTestId('history-table')).not.toBeInTheDocument();
    expect(screen.getByTestId('history-kpi-arrival')).toHaveTextContent('0件 ・ 0%');
  });

  it('読み込み失敗は detail 付きで出し、表も空状態も描かない', () => {
    mockUseVisitHistory.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('BE が落ちています'),
    });
    render(<VisitHistoryTab />);
    expect(screen.getByTestId('history-error')).toHaveTextContent('BE が落ちています');
    expect(screen.queryByTestId('history-table')).not.toBeInTheDocument();
    expect(screen.queryByTestId('history-empty')).not.toBeInTheDocument();
  });
});

describe('VisitHistoryTab — 詳細・出力', () => {
  it('行クリックで詳細が開く。調整の無い訪問に読取時刻の併記・調整の履歴は出さない', () => {
    setData([
      makeRow({
        actual_staff_name: '中村 彩',
        is_substitute: true,
        remarks: ['代行（予定: 川名 幸子）'],
      }),
    ]);
    render(<VisitHistoryTab />);
    expect(screen.queryByTestId('history-detail-dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('history-row-v-1'));

    const dlg = within(screen.getByTestId('history-detail-dialog'));
    expect(dlg.getByText('山田 花子 様')).toBeInTheDocument();
    expect(dlg.getByText(/2026\/09\/29 \(火\).*予定 13:00–13:40.*都賀/)).toBeInTheDocument();
    expect(dlg.getByText('12:56')).toBeInTheDocument();
    expect(dlg.getByText('13:40')).toBeInTheDocument();
    expect(dlg.getByText('予定 40 分')).toBeInTheDocument();
    expect(dlg.getByText('QR 読み取り')).toBeInTheDocument();
    expect(dlg.getByTestId('history-detail-remarks')).toHaveTextContent('代行（予定: 川名 幸子）');
    expect(dlg.getByText('QR 読取時刻')).toBeInTheDocument();
    expect(screen.getByTestId('history-detail-dialog')).not.toHaveTextContent('調整後');
    expect(dlg.queryByTestId('history-detail-adjustments')).not.toBeInTheDocument();
  });

  it('Excel で出力は、いまの絞り込み・並びで取得して保存する', async () => {
    const blob = new Blob(['x']);
    mockExport.mockResolvedValue({ blob, filename: 'visit-history_2026-08-01_2026-08-31.xlsx' });
    render(<VisitHistoryTab />);
    fireEvent.click(button('先月'));
    fireEvent.click(button('看護師別'));
    fireEvent.change(screen.getByLabelText('打刻'), { target: { value: 'in' } });
    fireEvent.click(screen.getByTestId('history-export-button'));

    await waitFor(() => expect(mockDownload).toHaveBeenCalled());
    expect(mockExport).toHaveBeenCalledWith(
      expect.objectContaining({ from: '2026-08-01', to: '2026-08-31', sort: 'staff', state: 'in' }),
    );
    expect(mockDownload).toHaveBeenCalledWith(blob, 'visit-history_2026-08-01_2026-08-31.xlsx');
  });

  it('Excel の失敗はトーストで伝え、保存しない', async () => {
    mockExport.mockRejectedValue(new Error('期間が長すぎます'));
    render(<VisitHistoryTab />);
    fireEvent.click(screen.getByTestId('history-export-button'));
    await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(mockToast.error.mock.calls[0]?.[0]).toContain('期間が長すぎます');
    expect(mockDownload).not.toHaveBeenCalled();
  });

  it('A4 で印刷は、並び・改ページ・打刻なしを選んでから開く', async () => {
    const win = { location: { href: '' }, opener: {} as unknown, close: vi.fn() };
    const open = vi.spyOn(window, 'open').mockReturnValue(win as unknown as Window);
    URL.createObjectURL = vi.fn(() => 'blob:report');
    URL.revokeObjectURL = vi.fn();
    mockReport.mockResolvedValue('<html><body>A4</body></html>');

    render(<VisitHistoryTab />);
    fireEvent.change(screen.getByLabelText('打刻'), { target: { value: 'in' } });
    fireEvent.click(screen.getByTestId('history-print-button'));

    const dlg = within(screen.getByTestId('history-print-dialog'));
    // 一覧が日付順のときは看護師別が初期値。既定は BE と同じ（改ページなし・打刻ありだけ）。
    expect(dlg.getByLabelText('印刷の並び')).toHaveValue('staff');
    expect(dlg.getByLabelText('看護師・患者ごとに改ページ')).not.toBeChecked();
    expect(dlg.getByLabelText('打刻のない予定も載せる')).not.toBeChecked();
    expect(mockReport).not.toHaveBeenCalled();

    fireEvent.click(dlg.getByLabelText('看護師・患者ごとに改ページ'));
    fireEvent.click(dlg.getByLabelText('打刻のない予定も載せる'));
    fireEvent.click(dlg.getByTestId('history-print-open'));

    await waitFor(() => expect(win.location.href).toBe('blob:report'));
    expect(open).toHaveBeenCalledWith('', '_blank');
    expect(mockReport).toHaveBeenCalledWith({
      filters: expect.objectContaining({ from: '2026-09-01', to: '2026-09-30', state: 'in' }),
      options: { group: 'staff', includeNone: true, pageBreak: true },
    });
    await waitFor(() =>
      expect(screen.queryByTestId('history-print-dialog')).not.toBeInTheDocument(),
    );
  });

  it('A4 の並びは一覧の並びを引き継ぎ、日付順では改ページを選べない', () => {
    render(<VisitHistoryTab />);
    fireEvent.click(button('患者別'));
    fireEvent.click(screen.getByTestId('history-print-button'));
    const dlg = within(screen.getByTestId('history-print-dialog'));
    expect(dlg.getByLabelText('印刷の並び')).toHaveValue('patient');

    fireEvent.click(dlg.getByLabelText('看護師・患者ごとに改ページ'));
    expect(dlg.getByLabelText('看護師・患者ごとに改ページ')).toBeChecked();
    fireEvent.change(dlg.getByLabelText('印刷の並び'), { target: { value: 'date' } });
    expect(dlg.getByLabelText('看護師・患者ごとに改ページ')).toBeDisabled();
    expect(dlg.getByLabelText('看護師・患者ごとに改ページ')).not.toBeChecked();
  });

  it('ポップアップがブロックされたら BE にレポートを作らせない', () => {
    vi.spyOn(window, 'open').mockReturnValue(null);
    render(<VisitHistoryTab />);
    fireEvent.click(screen.getByTestId('history-print-button'));
    fireEvent.click(screen.getByTestId('history-print-open'));
    expect(mockReport).not.toHaveBeenCalled();
    expect(mockToast.warning).toHaveBeenCalled();
  });

  it('A4 の失敗は開いたタブを閉じ、ダイアログは残す', async () => {
    const win = { location: { href: '' }, opener: {} as unknown, close: vi.fn() };
    vi.spyOn(window, 'open').mockReturnValue(win as unknown as Window);
    mockReport.mockRejectedValue(new Error('作れませんでした'));
    render(<VisitHistoryTab />);
    fireEvent.click(screen.getByTestId('history-print-button'));
    fireEvent.click(screen.getByTestId('history-print-open'));
    await waitFor(() => expect(win.close).toHaveBeenCalled());
    expect(mockToast.error.mock.calls[0]?.[0]).toContain('作れませんでした');
    expect(screen.getByTestId('history-print-dialog')).toBeInTheDocument();
  });
});

describe('VisitHistoryTab — 時刻の調整の表示', () => {
  it('調整のある行は「調整」バッジと、到着欄の下に読取時刻を出す', () => {
    setData([makeAdjustedRow()]);
    render(<VisitHistoryTab />);
    const row = within(screen.getByTestId('history-row-v-1'));
    // 到着は実績時刻（合わせた後）。読取時刻は下に小さく。
    expect(row.getByText('12:56')).toBeInTheDocument();
    expect(screen.getByTestId('history-arrival-read-v-1')).toHaveTextContent('読取 13:06');
    // 退出は調整していないので併記しない。
    expect(screen.queryByTestId('history-departure-read-v-1')).not.toBeInTheDocument();
    // 備考「時刻調整」は「調整」に縮め、注意色にはしない。
    const badge = row.getByText('調整');
    expect(badge).toHaveAttribute('title', '時刻調整');
    expect(badge.className).toContain('text-brand-primary-hover');
    expect(badge.className).not.toContain('text-warning');
  });

  it('調整の無い行には読取時刻もバッジも出さない', () => {
    setData([makeRow({ arrival_read_at: '2026-09-29T03:56:00Z', arrival_adjusted: false })]);
    render(<VisitHistoryTab />);
    expect(screen.queryByTestId('history-arrival-read-v-1')).not.toBeInTheDocument();
    expect(within(screen.getByTestId('history-row-v-1')).queryByText('調整')).toBeNull();
  });

  it('退出の調整は退出欄の下に読取時刻、読み取りの無い退出は「手入力」', () => {
    setData([
      makeRow({
        visit_id: 'v-dep',
        // 04:35Z = JST 13:35（合わせた後）、読取は 13:40。
        departure_at: '2026-09-29T04:35:00Z',
        departure_read_at: '2026-09-29T04:40:00Z',
        departure_adjusted: true,
      }),
      makeRow({
        visit_id: 'v-manual',
        departure_at: '2026-09-29T04:35:00Z',
        departure_read_at: null,
        departure_adjusted: false,
        departure_manual: true,
      }),
    ]);
    render(<VisitHistoryTab />);
    expect(screen.getByTestId('history-departure-read-v-dep')).toHaveTextContent('読取 13:40');
    expect(screen.getByTestId('history-departure-read-v-manual')).toHaveTextContent('手入力');
    // BE の備考に無くても、行のフラグから「調整」を出す。
    expect(
      within(screen.getByTestId('history-row-v-manual')).getByText('調整'),
    ).toBeInTheDocument();
  });

  it('集計帯「時刻の調整」は BE の summary.adjusted を出す', () => {
    setData([makeAdjustedRow()], 1, { ...SUMMARY, adjusted: 7 });
    render(<VisitHistoryTab />);
    const kpi = screen.getByTestId('history-kpi-adjusted');
    expect(kpi).toHaveTextContent('時刻の調整');
    expect(kpi).toHaveTextContent('7件');
  });

  it('見出し行の件数は BE の groups（絞り込み結果全体）から出し、「このページに」と書かない', () => {
    setData(
      [makeRow({ visit_id: 'a1', actual_staff_name: '中村 彩' }), makeRow({ visit_id: 'b1' })],
      120,
      SUMMARY,
      [
        { label: '中村 彩', count: 4, with_arrival: 3 },
        { label: '川名 幸子', count: 116, with_arrival: 32 },
      ],
    );
    render(<VisitHistoryTab />);
    fireEvent.click(button('看護師別'));
    const heads = screen.getAllByTestId('history-group-row');
    expect(heads[0]).toHaveTextContent('中村 彩4 件 ・ 打刻あり 3 件');
    // 次のページへ続く見出しでも、全体の件数なので書き分けない。
    expect(heads[1]).toHaveTextContent('川名 幸子116 件 ・ 打刻あり 32 件');
    expect(heads[1]).not.toHaveTextContent('このページに');
  });

  it('groups に無い見出しは、表示中の行から数える（従来どおり）', () => {
    setData([makeRow({ visit_id: 'b1' })], 120, SUMMARY, [
      { label: '別の看護師', count: 9, with_arrival: 9 },
    ]);
    render(<VisitHistoryTab />);
    fireEvent.click(button('看護師別'));
    expect(screen.getByTestId('history-group-row')).toHaveTextContent(
      '川名 幸子このページに 1 件 ・ 打刻あり 1 件',
    );
  });
});

describe('VisitHistoryTab — 詳細から実績の時刻を合わせる', () => {
  it('調整のある訪問は、読取時刻・調整の履歴・いまの理由を出す', () => {
    setData([makeAdjustedRow()]);
    render(<VisitHistoryTab />);
    const dlg = openDetail();
    expect(dlg.getByText('調整後（読取 13:06）')).toBeInTheDocument();
    const history = dlg.getByTestId('history-detail-adjustments');
    expect(history).toHaveTextContent('9/29 13:10');
    expect(history).toHaveTextContent('川名 幸子');
    expect(history).toHaveTextContent('到着 読取 13:06 → 12:56');
    expect(history).toHaveTextContent('理由: インターホン待ち');

    expect(dlg.getByText('実績の時刻を合わせる')).toBeInTheDocument();
    expect(dlg.getByLabelText('到着の時刻')).toHaveValue('12:56');
    expect(dlg.getByLabelText('到着の理由')).toHaveValue('intercom_wait');
    expect(dlg.getByLabelText('退出の時刻')).toHaveValue('13:40');
    expect(dlg.getByTestId('history-adjust-note-arrival')).toHaveTextContent('読取 13:06');
    // 退出は調整していないので「読取時刻に戻す」は出さない。
    expect(dlg.getByTestId('history-adjust-reset-arrival')).toHaveTextContent('読取時刻に戻す');
    expect(dlg.queryByTestId('history-adjust-reset-departure')).not.toBeInTheDocument();
  });

  it('文言に「直す」「修正」「補正」を使わない。理由は決まった 4 つの表示名', () => {
    setData([makeAdjustedRow()]);
    render(<VisitHistoryTab />);
    const dlg = openDetail();
    const text = screen.getByTestId('history-detail-dialog').textContent ?? '';
    expect(text).not.toMatch(/直す|直し|修正|補正/);
    expect(screen.getByTestId('history-tab').textContent ?? '').not.toMatch(/直す|修正|補正/);

    const labels = (name: string) =>
      within(dlg.getByLabelText(name))
        .getAllByRole('option')
        .map((o) => o.textContent);
    expect(labels('到着の理由')).toEqual(['インターホン待ち', '読み取りが後になった', 'その他']);
    expect(labels('退出の理由')).toEqual(['読み取りが後になった', '読み取りなし', 'その他']);
  });

  it('到着の時刻と理由を選んで保存すると、契約の項目で調整する', async () => {
    setData([makeRow({ adjust_allowed: true, arrival_read_at: '2026-09-29T03:56:00Z' })]);
    render(<VisitHistoryTab />);
    const dlg = openDetail();
    const save = dlg.getByTestId('history-adjust-save-arrival');
    // 何も変えていない間は保存できない。
    expect(save).toBeDisabled();

    fireEvent.change(dlg.getByLabelText('到着の時刻'), { target: { value: '12:46' } });
    fireEvent.change(dlg.getByLabelText('到着の理由'), { target: { value: 'read_later' } });
    expect(save).not.toBeDisabled();
    fireEvent.click(save);

    await waitFor(() => expect(mockToast.success).toHaveBeenCalled());
    expect(mockAdjust).toHaveBeenCalledWith({
      visitId: 'v-1',
      kind: 'arrival',
      time: '12:46',
      reasonCode: 'read_later',
      reasonText: '',
    });
    expect(mockToast.success.mock.calls[0]?.[0]).toBe('到着を 12:46 に合わせました');
    expect(mockReset).not.toHaveBeenCalled();
  });

  it('「その他」は自由記述を添えて送る', async () => {
    setData([makeRow({ adjust_allowed: true })]);
    render(<VisitHistoryTab />);
    const dlg = openDetail();
    expect(dlg.queryByLabelText('到着の理由（自由記述）')).not.toBeInTheDocument();
    fireEvent.change(dlg.getByLabelText('到着の時刻'), { target: { value: '12:50' } });
    fireEvent.change(dlg.getByLabelText('到着の理由'), { target: { value: 'other' } });
    fireEvent.change(dlg.getByLabelText('到着の理由（自由記述）'), {
      target: { value: '駐車場が遠かった' },
    });
    fireEvent.click(dlg.getByTestId('history-adjust-save-arrival'));
    await waitFor(() => expect(mockAdjust).toHaveBeenCalled());
    expect(mockAdjust).toHaveBeenCalledWith(
      expect.objectContaining({ reasonCode: 'other', reasonText: '駐車場が遠かった' }),
    );
  });

  it('退出の読み取りが無い訪問には退出時刻を入れられる（理由の初期値は「読み取りなし」）', async () => {
    setData([
      makeRow({
        adjust_allowed: true,
        departure_at: null,
        departure_read_at: null,
        stay_minutes: null,
        state: 'no_departure',
        remarks: ['退出なし'],
      }),
    ]);
    render(<VisitHistoryTab />);
    const dlg = openDetail();
    expect(dlg.getByLabelText('退出の時刻')).toHaveValue('');
    expect(dlg.getByLabelText('退出の理由')).toHaveValue('no_read');
    expect(dlg.getByTestId('history-adjust-note-departure')).toHaveTextContent(
      '読み取りなし ・ 退出時刻を入れられます',
    );
    expect(dlg.getByTestId('history-adjust-save-departure')).toBeDisabled();

    fireEvent.change(dlg.getByLabelText('退出の時刻'), { target: { value: '13:35' } });
    fireEvent.click(dlg.getByTestId('history-adjust-save-departure'));
    await waitFor(() => expect(mockAdjust).toHaveBeenCalled());
    expect(mockAdjust).toHaveBeenCalledWith({
      visitId: 'v-1',
      kind: 'departure',
      time: '13:35',
      reasonCode: 'no_read',
      reasonText: '',
    });
  });

  it('手で入れた退出は「手入力」と分かり、消せる', async () => {
    setData([
      makeRow({
        adjust_allowed: true,
        departure_at: '2026-09-29T04:35:00Z',
        departure_read_at: null,
        departure_manual: true,
        adjustments: [
          {
            kind: 'departure',
            reason_label: '読み取りなし',
            by_name: '管理 太郎',
            created_at: '2026-09-30T01:00:00Z',
          },
        ],
      }),
    ]);
    render(<VisitHistoryTab />);
    const dlg = openDetail();
    expect(dlg.getByText('手入力（読み取りなし）')).toBeInTheDocument();
    expect(dlg.getByTestId('history-detail-adjustments')).toHaveTextContent(
      '退出 読み取りなし → 13:35',
    );
    fireEvent.click(dlg.getByTestId('history-adjust-reset-departure'));
    await waitFor(() => expect(mockReset).toHaveBeenCalled());
    expect(mockReset).toHaveBeenCalledWith({ visitId: 'v-1', kind: 'departure' });
  });

  it('「読取時刻に戻す」は DELETE を呼ぶ。読取時刻と同じ時刻で保存しても同じ', async () => {
    setData([makeAdjustedRow()]);
    render(<VisitHistoryTab />);
    const dlg = openDetail();
    fireEvent.click(dlg.getByTestId('history-adjust-reset-arrival'));
    await waitFor(() => expect(mockToast.success).toHaveBeenCalledWith('読取時刻に戻しました'));
    expect(mockReset).toHaveBeenCalledWith({ visitId: 'v-1', kind: 'arrival' });

    fireEvent.change(dlg.getByLabelText('到着の時刻'), { target: { value: '13:06' } });
    fireEvent.click(dlg.getByTestId('history-adjust-save-arrival'));
    await waitFor(() => expect(mockReset).toHaveBeenCalledTimes(2));
    expect(mockAdjust).not.toHaveBeenCalled();
  });

  it('時刻が同じでも、いまの調整の理由を変えるなら保存できる', async () => {
    setData([makeAdjustedRow()]);
    render(<VisitHistoryTab />);
    const dlg = openDetail();
    const save = dlg.getByTestId('history-adjust-save-arrival');
    expect(save).toBeDisabled();
    fireEvent.change(dlg.getByLabelText('到着の理由'), { target: { value: 'read_later' } });
    expect(save).not.toBeDisabled();
    fireEvent.click(save);
    await waitFor(() => expect(mockAdjust).toHaveBeenCalled());
    expect(mockAdjust).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'arrival', time: '12:56', reasonCode: 'read_later' }),
    );
  });

  it('サーバの 422 / 403 / 409 の detail はそのまま出し、成功のトーストは出さない', async () => {
    setData([makeRow({ adjust_allowed: true })]);
    render(<VisitHistoryTab />);
    const dlg = openDetail();
    for (const [status, detail] of [
      [422, '到着は読取時刻（12:56）より後にはできません'],
      [403, '合わせられるのは 7 日前までの訪問です'],
      [409, '到着の記録がありません'],
    ] as const) {
      mockAdjust.mockRejectedValueOnce(new ApiError(`API ${status}`, status, { detail }));
      fireEvent.change(dlg.getByLabelText('到着の時刻'), { target: { value: '12:59' } });
      fireEvent.click(dlg.getByTestId('history-adjust-save-arrival'));
      await waitFor(() =>
        expect(dlg.getByTestId('history-adjust-error')).toHaveTextContent(detail),
      );
      expect(dlg.getByTestId('history-adjust-error').textContent).toBe(detail);
      // 次の回のために時刻を戻す（同じ値だと change が飛ばない）。
      fireEvent.change(dlg.getByLabelText('到着の時刻'), { target: { value: '12:58' } });
    }
    expect(mockToast.success).not.toHaveBeenCalled();
  });

  it('adjust_allowed=false は枠を隠さず無効化し、理由を title と本文に出す', () => {
    setData([makeAdjustedRow({ adjust_allowed: false })]);
    render(<VisitHistoryTab />);
    const dlg = openDetail();
    expect(dlg.getByTestId('history-adjust-box')).toBeInTheDocument();
    expect(dlg.getByTestId('history-adjust-na')).toHaveTextContent('合わせられません');
    for (const el of [
      dlg.getByLabelText('到着の時刻'),
      dlg.getByLabelText('到着の理由'),
      dlg.getByLabelText('退出の時刻'),
      dlg.getByTestId('history-adjust-save-arrival'),
      dlg.getByTestId('history-adjust-save-departure'),
      dlg.getByTestId('history-adjust-reset-arrival'),
    ]) {
      expect(el).toBeDisabled();
      expect(el.getAttribute('title')).toContain('合わせられません');
    }
    fireEvent.click(dlg.getByTestId('history-adjust-reset-arrival'));
    expect(mockReset).not.toHaveBeenCalled();
  });

  it('adjust_allowed の無い応答（古い BE）でも操作させない', () => {
    setData([makeRow()]);
    render(<VisitHistoryTab />);
    const dlg = openDetail();
    expect(dlg.getByLabelText('到着の時刻')).toBeDisabled();
    expect(dlg.getByTestId('history-adjust-save-arrival')).toBeDisabled();
  });

  it('到着の記録が無い訪問には枠を出さない（到着を後から入れるのは対象外）', () => {
    setData([
      makeRow({
        adjust_allowed: true,
        actual_staff_name: null,
        arrival_at: null,
        departure_at: null,
        stay_minutes: null,
        state: 'none',
      }),
    ]);
    render(<VisitHistoryTab />);
    const dlg = openDetail();
    expect(dlg.queryByTestId('history-adjust-box')).not.toBeInTheDocument();
  });

  it('保存後に取り直した一覧の値で、開いたままの詳細を差し替える', () => {
    setData([makeRow({ adjust_allowed: true, arrival_read_at: '2026-09-29T03:56:00Z' })]);
    const view = render(<VisitHistoryTab />);
    const dlg = openDetail();
    expect(dlg.getByLabelText('到着の時刻')).toHaveValue('12:56');

    // 失効 → 取り直しで、同じ訪問が調整後の値になって返ってくる。
    setData([
      makeAdjustedRow({
        arrival_at: '2026-09-29T03:46:00Z',
        arrival_read_at: '2026-09-29T03:56:00Z',
        stay_minutes: 54,
      }),
    ]);
    view.rerender(<VisitHistoryTab />);
    expect(dlg.getByText('調整後（読取 12:56）')).toBeInTheDocument();
    expect(dlg.getByLabelText('到着の時刻')).toHaveValue('12:46');
    expect(dlg.getByTestId('history-adjust-reset-arrival')).toBeInTheDocument();
  });
});

// ── コードレビューの指摘（2026-09-30）──────────────────────────────────────

describe('VisitHistoryTab — 絞り込み中に合わせた行が一覧から外れる（M-4）', () => {
  it('読取時刻に戻して絞り込みから外れたら、古い値を出し続けずに詳細を閉じる', async () => {
    setData([makeAdjustedRow()]);
    const view = render(<VisitHistoryTab />);
    fireEvent.change(screen.getByLabelText('打刻'), { target: { value: 'adjusted' } });
    const dlg = openDetail();
    fireEvent.click(dlg.getByTestId('history-adjust-reset-arrival'));
    await waitFor(() => expect(mockToast.success).toHaveBeenCalledWith('読取時刻に戻しました'));
    // 取り直しが届くまでは開いたまま。
    expect(screen.getByTestId('history-detail-dialog')).toBeInTheDocument();

    // 取り直した一覧に、その訪問はもう居ない（「時刻の調整あり」から外れた）。
    setData([]);
    view.rerender(<VisitHistoryTab />);
    await waitFor(() =>
      expect(screen.queryByTestId('history-detail-dialog')).not.toBeInTheDocument(),
    );
    expect(mockToast.info).toHaveBeenCalledWith('絞り込みの条件から外れたため、詳細を閉じました');
  });

  it('合わせた行が一覧に残っていれば、閉じずに新しい値を見せる', async () => {
    setData([makeRow({ adjust_allowed: true, arrival_read_at: '2026-09-29T03:56:00Z' })]);
    const view = render(<VisitHistoryTab />);
    const dlg = openDetail();
    fireEvent.change(dlg.getByLabelText('到着の時刻'), { target: { value: '12:46' } });
    fireEvent.click(dlg.getByTestId('history-adjust-save-arrival'));
    await waitFor(() => expect(mockToast.success).toHaveBeenCalled());

    setData([
      makeAdjustedRow({
        arrival_at: '2026-09-29T03:46:00Z',
        arrival_read_at: '2026-09-29T03:56:00Z',
      }),
    ]);
    view.rerender(<VisitHistoryTab />);
    expect(screen.getByTestId('history-detail-dialog')).toBeInTheDocument();
    expect(dlg.getByLabelText('到着の時刻')).toHaveValue('12:46');
    expect(mockToast.info).not.toHaveBeenCalled();
  });

  it('前の結果を出している間（次の結果を待っている）は、まだ閉じない', async () => {
    setData([makeAdjustedRow()]);
    const view = render(<VisitHistoryTab />);
    const dlg = openDetail();
    fireEvent.click(dlg.getByTestId('history-adjust-reset-arrival'));
    await waitFor(() => expect(mockToast.success).toHaveBeenCalled());

    mockUseVisitHistory.mockReturnValue({
      data: { items: [], total: 0, summary: SUMMARY },
      isLoading: false,
      isPlaceholderData: true,
    });
    view.rerender(<VisitHistoryTab />);
    expect(screen.getByTestId('history-detail-dialog')).toBeInTheDocument();
  });

  it('合わせずに見ているだけの詳細は、一覧が変わっても閉じない', () => {
    setData([makeAdjustedRow()]);
    const view = render(<VisitHistoryTab />);
    openDetail();
    setData([]);
    view.rerender(<VisitHistoryTab />);
    expect(screen.getByTestId('history-detail-dialog')).toBeInTheDocument();
  });

  it('保存できなかったときは閉じない', async () => {
    setData([makeRow({ adjust_allowed: true })]);
    const view = render(<VisitHistoryTab />);
    const dlg = openDetail();
    mockAdjust.mockRejectedValueOnce(new ApiError('API 422', 422, { detail: '範囲の外です' }));
    fireEvent.change(dlg.getByLabelText('到着の時刻'), { target: { value: '12:59' } });
    fireEvent.click(dlg.getByTestId('history-adjust-save-arrival'));
    await waitFor(() => expect(dlg.getByTestId('history-adjust-error')).toBeInTheDocument());

    setData([]);
    view.rerender(<VisitHistoryTab />);
    expect(screen.getByTestId('history-detail-dialog')).toBeInTheDocument();
  });
});

describe('VisitHistoryTab — 理由の初期値は reason_code から（L-11）', () => {
  it('表示名が違っていても、reason_code で選択欄を合わせる', () => {
    setData([
      makeAdjustedRow({
        adjustments: [
          { kind: 'arrival', reason_code: 'read_later', reason_label: '表示名が変わった' },
        ],
      }),
    ]);
    render(<VisitHistoryTab />);
    const dlg = openDetail();
    expect(dlg.getByLabelText('到着の理由')).toHaveValue('read_later');
  });

  it('reason_code の無い応答は、表示名からの逆引きに落とす', () => {
    setData([
      makeAdjustedRow({
        adjustments: [{ kind: 'arrival', reason_label: '読み取りが後になった' }],
      }),
    ]);
    render(<VisitHistoryTab />);
    const dlg = openDetail();
    expect(dlg.getByLabelText('到着の理由')).toHaveValue('read_later');
  });
});

describe('VisitHistoryTab — 前の結果を保つ・検索欄の上限（L-12 / L-13）', () => {
  it('次の結果を待つ間も、集計帯・件数・ページ送り・表を消さない（表は薄くする）', () => {
    mockUseVisitHistory.mockReturnValue({
      data: { items: [makeRow()], total: 120, summary: SUMMARY },
      isLoading: false,
      isPlaceholderData: true,
    });
    render(<VisitHistoryTab />);
    expect(screen.getByTestId('history-summary')).toBeInTheDocument();
    expect(screen.getByTestId('history-count')).toHaveTextContent('120件');
    expect(screen.getByTestId('history-pager')).toBeInTheDocument();
    const wrap = screen.getByTestId('history-table').parentElement;
    expect(wrap?.className).toContain('opacity-60');
    expect(wrap).toHaveAttribute('aria-busy', 'true');
  });

  it('結果が届いたら、表を薄くしない', () => {
    render(<VisitHistoryTab />);
    const wrap = screen.getByTestId('history-table').parentElement;
    expect(wrap?.className).not.toContain('opacity-60');
    expect(wrap).not.toHaveAttribute('aria-busy');
  });

  it('検索欄は 100 字まで（BE の上限と同じ。超えると 422 になる）', () => {
    render(<VisitHistoryTab />);
    expect(screen.getByLabelText('打刻履歴を検索')).toHaveAttribute('maxLength', '100');
  });
});

describe('VisitHistoryTab — 合わせられない理由の文言（L-14）', () => {
  const na = () => openDetail().getByTestId('history-adjust-na').textContent ?? '';

  it('管理者: 合わせられないのは削除済みのときだけ — 担当や 7 日の話をしない', () => {
    mockRole.value = 'admin';
    setData([makeAdjustedRow({ adjust_allowed: false, is_cancelled: true })]);
    render(<VisitHistoryTab />);
    const text = na();
    expect(text).toBe('この訪問は削除されているため、実績の時刻は合わせられません');
    expect(text).not.toMatch(/7 日|担当/);
  });

  it('スタッフ: 担当・7 日前まで・削除済みを案内する', () => {
    mockRole.value = 'staff';
    setData([makeAdjustedRow({ adjust_allowed: false })]);
    render(<VisitHistoryTab />);
    const text = na();
    expect(text).toContain('自分が担当または記録した訪問で、7 日前までのものです');
    expect(text).toContain('削除された訪問も合わせられません');
  });

  it('adjust_allowed の無い応答（古い BE）は、理由を決めつけない', () => {
    mockRole.value = 'admin';
    setData([makeRow()]);
    render(<VisitHistoryTab />);
    const text = na();
    expect(text).toBe('この訪問の実績は、いまは合わせられません');
    expect(text).not.toContain('削除');
  });

  it('どの文言にも「直す」「修正」「補正」を使わない', () => {
    for (const [role, row] of [
      ['admin', makeAdjustedRow({ adjust_allowed: false })],
      ['staff', makeAdjustedRow({ adjust_allowed: false })],
      ['admin', makeRow()],
    ] as const) {
      mockRole.value = role;
      setData([row]);
      const view = render(<VisitHistoryTab />);
      expect(na()).not.toMatch(/直す|直し|修正|補正/);
      view.unmount();
    }
  });
});
