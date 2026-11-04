/**
 * 「月の実績」タブの部品の vitest。
 *
 * 縛る挙動:
 *   1. 表は差がある行だけ。一致は件数だけ（「一致 N 件は対象外」）
 *   2. 「する／しない」の切り替え。人が見る行は確認の一言を挟む。カイポケに無い／だけの行は切り替えない
 *   3. 反映・予約の前に、月と件数を書いた確認を画面の中で出す
 *   4. 段の決め方（試しが全部進んだら「反映する」）
 *   5. 二度押しで 2 回送らない
 *   6. カイポケで確かめる行は流し直さない・反映済み（already_applied）は送る行に入れない
 *   7. 最後の段の看護師別の表（並べ替え）と「A4 で開く」（新しいタブに HTML を開く）
 *   8. 記録書Ⅱ：列（今の時刻 → そろえる時刻）・「記録書Ⅱだけ」の件数と絞り込み・結果の欄
 *      （知らない理由コードもそのまま出す）・記録書Ⅱだけの行の試しと送る行の数え方
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';

import type {
  MonthlyActualCounts,
  MonthlyActualList,
  MonthlyActualRow,
  MonthlyStaffRow,
  MonthlyStaffSummary,
} from '@/lib/schemas/monthly-actual';

const { hooks } = vi.hoisted(() => ({
  hooks: {
    list: { data: undefined as unknown, isLoading: false, isError: false, error: null },
    applyMutateAsync: vi.fn(),
    refreshMutateAsync: vi.fn(),
    updateMutate: vi.fn(),
    statusEnabled: [] as boolean[],
    staffEnabled: [] as boolean[],
    staffSummary: undefined as unknown,
    reportMutateAsync: vi.fn(),
  },
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock('@/lib/queries/monthly-actual', () => ({
  useMonthlyActual: () => hooks.list,
  useMonthlyActualApplyStatus: (_m: string, o: { enabled?: boolean }) => {
    hooks.statusEnabled.push(!!o.enabled);
    return { data: undefined };
  },
  useRefreshMonthlyActual: () => ({ mutateAsync: hooks.refreshMutateAsync, isPending: false }),
  useUpdateMonthlyActualRow: () => ({ mutate: hooks.updateMutate, isPending: false }),
  useApplyMonthlyActual: () => ({ mutateAsync: hooks.applyMutateAsync, isPending: false }),
  useScheduleMonthlyActual: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCancelMonthlyActualSchedule: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useMonthlyActualExport: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useMonthlyStaffSummary: (_m: string, o: { enabled?: boolean }) => {
    hooks.staffEnabled.push(!!o.enabled);
    return { data: hooks.staffSummary, isLoading: false, isError: false, error: null };
  },
  useMonthlyStaffReport: () => ({ mutateAsync: hooks.reportMutateAsync, isPending: false }),
}));

import { MonthlyActualRunPanel } from '../_components/monthly-actual/MonthlyActualRunPanel';
import { MonthlyActualTab } from '../_components/monthly-actual/MonthlyActualTab';
import { MonthlyActualTable } from '../_components/monthly-actual/MonthlyActualTable';
import { MonthlyStaffTable } from '../_components/monthly-actual/MonthlyStaffTable';
import {
  currentStep,
  dryRunAllOk,
  failedRows,
  flagChips,
  pendingRows,
} from '../_components/monthly-actual/monthlyActualFormat';

function makeRow(over: Partial<MonthlyActualRow> = {}): MonthlyActualRow {
  return {
    id: 'r1',
    date: '2026-10-02',
    patient_code: 'P001',
    patient_name: '患者A',
    rk_staff: '職員A',
    rk_start: '15:38',
    rk_end: '16:20',
    rk_stay: 42,
    rk_adjusted: null,
    kp_patient: '患者A',
    kp_start: '15:30',
    kp_end: '16:05',
    kp_minutes: 35,
    kp_staff: '職員A',
    kp_service: null,
    record2_start: null,
    record2_end: null,
    status: 'apply',
    status_label: '反映する',
    flags: [],
    flag_details: [],
    apply: true,
    selectable: true,
    apply_changed_by_user: false,
    new_start: '15:38',
    new_end: '16:20',
    diff_start: 8,
    diff_end: 15,
    result_status: null,
    result_reason: null,
    result_message: null,
    result_dry_run: null,
    needs_kaipoke_check: false,
    dry_run_passed: false,
    needs_actual: true,
    needs_record2: false,
    record2_result_status: null,
    record2_result_reason: null,
    record2_result_message: null,
    record2_result_dry_run: null,
    ...over,
  };
}

const COUNTS: MonthlyActualCounts = {
  match: 378,
  apply: 1,
  k2_only: 0,
  review: 1,
  missing_in_kaipoke: 1,
  kaipoke_only: 0,
  total: 381,
  record2: 0,
};

function makeList(over: Partial<MonthlyActualList> = {}): MonthlyActualList {
  return {
    month: '2026-10',
    kp_fetched_at: '2026-11-01T07:29:00+09:00',
    counts: COUNTS,
    rows: [makeRow()],
    job: undefined,
    is_past_month: true,
    selected_count: 1,
    refreshing: false,
    refresh_error: null,
    verify_result: null,
    evening_warning: null,
    ...over,
  };
}

function job(over: Partial<NonNullable<MonthlyActualList['job']>> = {}) {
  return {
    state: 'judged',
    total: 1,
    success: 1,
    failed: 0,
    record2_success: 0,
    record2_failed: 0,
    dry_run: true,
    scheduled_at: null,
    scheduled_dry_run: false,
    running: false,
    error: null,
    ...over,
  };
}

const ROWS: MonthlyActualRow[] = [
  makeRow({ id: 'm1', status: 'match', apply: false }),
  makeRow({ id: 'a1' }),
  makeRow({
    id: 'v1',
    status: 'review',
    apply: false,
    patient_name: '患者B',
    flags: ['担当が違う'],
    flag_details: [{ code: 'staff_diff', label: '担当が違う', review: true }],
  }),
  makeRow({
    id: 'x1',
    status: 'missing_in_kaipoke',
    apply: false,
    selectable: false,
    patient_name: '患者C',
    kp_start: null,
    kp_end: null,
    diff_start: null,
    diff_end: null,
  }),
];

describe('MonthlyActualTable', () => {
  it('一致の行は出さず、件数だけを書く', () => {
    render(<MonthlyActualTable counts={COUNTS} rows={ROWS} onToggle={vi.fn()} />);
    expect(screen.queryByTestId('ma-row-m1')).toBeNull();
    expect(screen.getByTestId('ma-row-a1')).toBeTruthy();
    expect(screen.getByTestId('ma-match-note').textContent).toContain('一致 378 件は対象外');
  });

  it('差が 5 分を超えると赤（data-big）、印はチップで出る', () => {
    render(<MonthlyActualTable counts={COUNTS} rows={ROWS} onToggle={vi.fn()} />);
    const row = screen.getByTestId('ma-row-a1');
    expect(within(row).getByText('+8').getAttribute('data-big')).toBe('true');
    expect(within(screen.getByTestId('ma-row-v1')).getByText('担当が違う')).toBeTruthy();
    expect(within(screen.getByTestId('ma-row-x1')).getByText('カイポケに無い')).toBeTruthy();
  });

  it('「しない」を押すと onToggle(id, false)', () => {
    const onToggle = vi.fn();
    render(<MonthlyActualTable counts={COUNTS} rows={ROWS} onToggle={onToggle} />);
    fireEvent.click(
      within(screen.getByTestId('ma-row-a1')).getByRole('button', { name: 'しない' }),
    );
    expect(onToggle).toHaveBeenCalledWith('a1', false);
  });

  it('人が見る行を「する」にすると確認が出て、「反映する」で初めて onToggle', () => {
    const onToggle = vi.fn();
    render(<MonthlyActualTable counts={COUNTS} rows={ROWS} onToggle={onToggle} />);
    const row = screen.getByTestId('ma-row-v1');
    fireEvent.click(within(row).getByRole('button', { name: 'する' }));
    expect(onToggle).not.toHaveBeenCalled();
    const dialog = within(row).getByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'やめる' }));
    expect(within(row).queryByRole('alertdialog')).toBeNull();
    expect(onToggle).not.toHaveBeenCalled();

    fireEvent.click(within(row).getByRole('button', { name: 'する' }));
    fireEvent.click(
      within(within(row).getByRole('alertdialog')).getByRole('button', { name: '反映する' }),
    );
    expect(onToggle).toHaveBeenCalledWith('v1', true);
  });

  it('カイポケに無い行は切り替えられず、カイポケで足す案内を出す', () => {
    render(<MonthlyActualTable counts={COUNTS} rows={ROWS} onToggle={vi.fn()} />);
    const row = screen.getByTestId('ma-row-x1');
    expect(within(row).queryByRole('button', { name: 'する' })).toBeNull();
    expect(within(row).getByText('カイポケで足してください')).toBeTruthy();
  });

  it('判定の絞り込み「人が見る」で review の行だけになる', () => {
    render(<MonthlyActualTable counts={COUNTS} rows={ROWS} onToggle={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /^人が見る/ }));
    expect(screen.queryByTestId('ma-row-a1')).toBeNull();
    expect(screen.getByTestId('ma-row-v1')).toBeTruthy();
  });
});

function panelProps(over: Partial<Parameters<typeof MonthlyActualRunPanel>[0]> = {}) {
  return {
    step: 4 as const,
    month: '2026-10',
    isPastMonth: true,
    selectedCount: 12,
    failedCount: 0,
    job: job(),
    progress: null,
    refreshing: false,
    busy: false,
    onRefresh: vi.fn(),
    onDryRun: vi.fn(),
    onApply: vi.fn(),
    onSchedule: vi.fn(),
    onCancelSchedule: vi.fn(),
    onRetryFailed: vi.fn(),
    onExport: vi.fn(),
    ...over,
  };
}

describe('MonthlyActualRunPanel', () => {
  it('すぐ反映する → 月と件数の確認 → 「反映する」で onApply', () => {
    const p = panelProps();
    render(<MonthlyActualRunPanel {...p} />);
    fireEvent.click(screen.getByRole('button', { name: '12 件をすぐ反映する' }));
    expect(p.onApply).not.toHaveBeenCalled();
    const dialog = screen.getByRole('alertdialog');
    expect(dialog.textContent).toContain('2026 年 10 月の 12 件');
    fireEvent.click(within(dialog).getByRole('button', { name: '反映する' }));
    expect(p.onApply).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('今夜 22 時に流す → 確認 → 「やめる」なら予約しない', () => {
    const p = panelProps();
    render(<MonthlyActualRunPanel {...p} />);
    fireEvent.click(screen.getByRole('button', { name: '今夜 22 時に流す' }));
    const dialog = screen.getByRole('alertdialog');
    expect(dialog.textContent).toContain('今夜 22 時');
    fireEvent.click(within(dialog).getByRole('button', { name: 'やめる' }));
    expect(p.onSchedule).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '今夜 22 時に流す' }));
    fireEvent.click(
      within(screen.getByRole('alertdialog')).getByRole('button', { name: '反映する' }),
    );
    expect(p.onSchedule).toHaveBeenCalledTimes(1);
  });

  it('終わっていない月は反映のボタンが押せない', () => {
    render(<MonthlyActualRunPanel {...panelProps({ isPastMonth: false })} />);
    expect(screen.getByTestId('ma-not-past')).toBeTruthy();
    expect(
      (screen.getByRole('button', { name: '12 件をすぐ反映する' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('予約済みなら予約の時刻と取り消しを出す', () => {
    const p = panelProps({ job: job({ scheduled_at: '2026-11-01T22:00:00+09:00' }) });
    render(<MonthlyActualRunPanel {...p} />);
    expect(screen.getByTestId('ma-scheduled')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '予約を取り消す' }));
    expect(p.onCancelSchedule).toHaveBeenCalled();
  });

  it('動いている間は進み具合を出す', () => {
    render(
      <MonthlyActualRunPanel
        {...panelProps({
          job: job({ running: true, dry_run: false }),
          progress: { done: 3, total: 12, success: 3, failed: 0 },
        })}
      />,
    );
    expect(screen.getByTestId('ma-progress').textContent).toContain('3 / 12 件');
  });
});

describe('monthlyActualFormat', () => {
  it('段：読み取り前 1・まだ流していない 2・試しが全部進んだ 4・一部だけなら 3', () => {
    expect(currentStep(makeList({ kp_fetched_at: null }))).toBe(1);
    expect(currentStep(makeList())).toBe(2);
    const ok = makeList({
      job: job(),
      rows: [makeRow({ result_status: 'dry_run_ok', dry_run_passed: true })],
    });
    expect(dryRunAllOk(ok)).toBe(true);
    expect(currentStep(ok)).toBe(4);
    const ng = makeList({
      job: job(),
      rows: [
        makeRow({ id: 'a', result_status: 'dry_run_ok', dry_run_passed: true }),
        makeRow({ id: 'b', result_status: 'failed', result_reason: 'not_found' }),
      ],
    });
    expect(currentStep(ng)).toBe(3);
  });

  it('試しの後に時刻が変わった行（dry_run_passed でない）は、試しが全部進んだとみなさない', () => {
    const stale = makeList({
      job: job(),
      rows: [makeRow({ result_status: 'dry_run_ok', dry_run_passed: false })],
    });
    expect(dryRunAllOk(stale)).toBe(false);
  });

  it('反映済み（already_applied）は送る行に入れず、試しが全部進んだに数える', () => {
    const view = makeList({
      job: job(),
      rows: [
        makeRow({ id: 'a', result_status: 'dry_run_ok', dry_run_passed: true }),
        makeRow({
          id: 'b',
          result_status: 'skipped',
          result_reason: 'already_applied',
          result_dry_run: true,
        }),
      ],
    });
    expect(pendingRows(view.rows).map((r) => r.id)).toEqual(['a']);
    expect(dryRunAllOk(view)).toBe(true);
  });

  it('カイポケで確かめる行は流し直しにも送る行にも入れず、印を出す', () => {
    const rows = [
      makeRow({
        id: 'f',
        result_status: 'failed',
        result_reason: 'not_found',
        result_dry_run: false,
      }),
      makeRow({
        id: 'v',
        result_status: 'failed',
        result_reason: 'verify_failed',
        result_dry_run: false,
        needs_kaipoke_check: true,
      }),
      makeRow({
        id: 'u',
        result_status: 'unknown',
        result_dry_run: false,
        needs_kaipoke_check: true,
      }),
      makeRow({
        id: 'e',
        result_status: 'error',
        result_dry_run: false,
        needs_kaipoke_check: true,
      }),
    ];
    expect(failedRows(rows).map((r) => r.id)).toEqual(['f']);
    expect(pendingRows(rows).map((r) => r.id)).toEqual(['f']);
    const chips = flagChips(rows[1]!);
    expect(chips.some((c) => c.label === 'カイポケで確かめてから')).toBe(true);
  });

  it('段：反映が終われば 5、確かめたら 6', () => {
    expect(currentStep(makeList({ job: job({ dry_run: false, state: 'applied' }) }))).toBe(5);
    expect(currentStep(makeList({ job: job({ dry_run: false, state: 'verified' }) }))).toBe(6);
  });

  it('印のコードは短い言葉にする（30 分未満→到着＋35）', () => {
    const chips = flagChips(
      makeRow({
        flag_details: [
          { code: 'short_stay', label: 'QR の滞在 20 分 → 到着＋35 分', review: false },
        ],
      }),
    );
    expect(chips[0]?.label).toBe('30 分未満→到着＋35');
  });
});

describe('MonthlyActualTab', () => {
  beforeEach(() => {
    hooks.applyMutateAsync.mockReset();
    hooks.statusEnabled = [];
    hooks.list = { data: makeList(), isLoading: false, isError: false, error: null };
  });

  it('二度押ししても試しに流すのは 1 回だけ（行は「する」の分）', async () => {
    let resolve: (v: unknown) => void = () => {};
    hooks.applyMutateAsync.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<MonthlyActualTab />);
    const button = screen.getByRole('button', { name: 'この 1 件で試しに流す' });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(hooks.applyMutateAsync).toHaveBeenCalledTimes(1);
    expect(hooks.applyMutateAsync).toHaveBeenCalledWith({
      month: expect.any(String),
      dryRun: true,
      rowIds: ['r1'],
    });
    await act(async () => resolve({}));
  });

  it('反映が動いていないときは進み具合を読まない・動いていれば読む', () => {
    render(<MonthlyActualTab />);
    expect(hooks.statusEnabled.every((e) => e === false)).toBe(true);
    hooks.list = {
      data: makeList({ job: job({ running: true, dry_run: false }) }),
      isLoading: false,
      isError: false,
      error: null,
    };
    hooks.statusEnabled = [];
    render(<MonthlyActualTab />);
    expect(hooks.statusEnabled).toContain(true);
  });
});

function staffRow(over: Partial<MonthlyStaffRow> = {}): MonthlyStaffRow {
  return {
    staff_id: 's1',
    name: '看護師A',
    office_short: '本',
    visits: 40,
    days: 18,
    per_day: 2.22,
    patients: 20,
    weekly_patients_avg: 12.5,
    accompany_visits: 3,
    provided_min_total: 1800,
    provided_samples: 40,
    avg_stay_min: 45,
    short_stay_count: 2,
    no_departure_count: 1,
    no_arrival_count: 0,
    km_total: 120.4,
    km_per_day: 6.69,
    km_per_visit: 3.01,
    skipped_legs: 0,
    weeks: [],
    ...over,
  };
}

function staffSummary(staff: MonthlyStaffRow[]): MonthlyStaffSummary {
  return {
    month: '2026-10',
    date_from: '2026-10-01',
    date_to: '2026-10-31',
    short_stay_minutes: 30,
    short_stay_end_minutes: 35,
    weeks: [],
    totals: {
      staff_count: staff.length,
      visits: 0,
      accompany_visits: 0,
      provided_min_total: 0,
      short_stay_count: 0,
      no_departure_count: 0,
      no_arrival_count: 0,
      km_total: 0,
      skipped_legs: 0,
    },
    staff,
  };
}

describe('MonthlyStaffTable', () => {
  const rows = [
    staffRow({ staff_id: 'a', name: '看護師A', visits: 40, km_total: 120.4 }),
    staffRow({
      staff_id: 'b',
      name: '看護師B',
      visits: 55,
      km_total: 80,
      weekly_patients_avg: null,
    }),
    staffRow({ staff_id: 'c', name: '看護師C', visits: 10, km_total: 200 }),
  ];
  const names = () =>
    screen.getAllByTestId('ma-staff-row').map((tr) => tr.querySelector('td')?.textContent);

  it('最初は BE の並び。見出しを押すと大きい順、もう一度で小さい順', () => {
    render(<MonthlyStaffTable summary={staffSummary(rows)} />);
    expect(names()).toEqual(['看護師A本', '看護師B本', '看護師C本']);
    fireEvent.click(screen.getByTestId('ma-staff-sort-visits'));
    expect(names()).toEqual(['看護師B本', '看護師A本', '看護師C本']);
    fireEvent.click(screen.getByTestId('ma-staff-sort-visits'));
    expect(names()).toEqual(['看護師C本', '看護師A本', '看護師B本']);
    fireEvent.click(screen.getByTestId('ma-staff-sort-km_total'));
    expect(names()).toEqual(['看護師C本', '看護師A本', '看護師B本']);
  });

  it('空の値は並べ替えても下・「—」で出す。到着＋35 は退出なしを添える', () => {
    render(<MonthlyStaffTable summary={staffSummary(rows)} />);
    fireEvent.click(screen.getByTestId('ma-staff-sort-weekly_patients_avg'));
    expect(names()[2]).toBe('看護師B本');
    fireEvent.click(screen.getByTestId('ma-staff-sort-weekly_patients_avg'));
    expect(names()[2]).toBe('看護師B本');
    const row = screen.getAllByTestId('ma-staff-row')[2]!;
    expect(row.textContent).toContain('—');
    expect(row.textContent).toContain('3（うち退出なし 1）');
  });

  it('月の途中は「今日まで」の注記を出す。月が終わっていれば出さない', () => {
    const { unmount } = render(
      <MonthlyStaffTable summary={{ ...staffSummary(rows), counted_until: '2026-10-15' }} />,
    );
    expect(screen.getByTestId('ma-staff-until').textContent).toContain('今日（10/15）まで');
    unmount();
    render(<MonthlyStaffTable summary={staffSummary(rows)} />);
    expect(screen.queryByTestId('ma-staff-until')).toBeNull();
  });

  it('訪問が無い月は表を出さない', () => {
    render(<MonthlyStaffTable summary={staffSummary([])} />);
    expect(screen.getByTestId('ma-staff-empty')).toBeTruthy();
  });
});

describe('MonthlyActualTab 一覧を出す', () => {
  beforeEach(() => {
    hooks.staffEnabled = [];
    hooks.reportMutateAsync.mockReset();
    hooks.staffSummary = staffSummary([staffRow()]);
    hooks.list = {
      data: makeList({ job: job({ dry_run: false, state: 'verified' }) }),
      isLoading: false,
      isError: false,
      error: null,
    };
  });

  it('最後の段で看護師別の表を読み、出す。前の段では読まない', () => {
    render(<MonthlyActualTab />);
    expect(hooks.staffEnabled).toContain(true);
    expect(screen.getByTestId('ma-staff-table')).toBeTruthy();
    hooks.list = { data: makeList(), isLoading: false, isError: false, error: null };
    hooks.staffEnabled = [];
    render(<MonthlyActualTab />);
    expect(hooks.staffEnabled.every((e) => e === false)).toBe(true);
  });

  it('「A4 で開く」は click の中で新しいタブを開き、届いた HTML をそのタブに出す', async () => {
    const win = { location: { href: '' }, opener: {}, close: vi.fn() };
    const open = vi.spyOn(window, 'open').mockReturnValue(win as unknown as Window);
    const createUrl = vi.fn(() => 'blob:report');
    const origCreate = URL.createObjectURL;
    const origRevoke = URL.revokeObjectURL;
    URL.createObjectURL = createUrl;
    URL.revokeObjectURL = vi.fn();
    hooks.reportMutateAsync.mockResolvedValue('<!doctype html><title>x</title>');
    try {
      render(<MonthlyActualTab />);
      await act(async () => {
        fireEvent.click(screen.getByTestId('ma-open-report'));
      });
      expect(open).toHaveBeenCalledWith('', '_blank');
      expect(hooks.reportMutateAsync).toHaveBeenCalledWith(expect.any(String));
      expect(win.location.href).toBe('blob:report');
      expect(win.opener).toBeNull();
    } finally {
      open.mockRestore();
      URL.createObjectURL = origCreate;
      URL.revokeObjectURL = origRevoke;
    }
  });
});

describe('記録書Ⅱ', () => {
  const K2_ROWS: MonthlyActualRow[] = [
    makeRow({
      id: 'b1',
      record2_start: '15:30',
      record2_end: '16:05',
      needs_record2: true,
    }),
    makeRow({
      id: 'k1',
      status: 'k2_only',
      status_label: '記録書Ⅱだけ',
      patient_name: '患者K',
      kp_start: '15:38',
      kp_end: '16:20',
      diff_start: 0,
      diff_end: 0,
      record2_start: '15:18',
      record2_end: '16:20',
      needs_actual: false,
      needs_record2: true,
    }),
    makeRow({ id: 'n1', patient_name: '患者N' }),
  ];
  const K2_COUNTS: MonthlyActualCounts = { ...COUNTS, apply: 2, k2_only: 1, record2: 2 };

  it('記録書Ⅱの列に今の時刻とそろえる時刻、無い行は「記録書Ⅱなし」', () => {
    render(<MonthlyActualTable counts={K2_COUNTS} rows={K2_ROWS} onToggle={vi.fn()} />);
    const k2 = screen.getByTestId('ma-record2-k1');
    expect(k2.textContent).toContain('15:18〜16:20');
    expect(k2.textContent).toContain('→ 15:38〜16:20 にそろえる');
    expect(within(screen.getByTestId('ma-row-n1')).getByText('記録書Ⅱなし')).toBeTruthy();
    expect(screen.getByTestId('ma-match-note').textContent).toContain('2 件');
  });

  it('「記録書Ⅱだけ」の件数と絞り込み', () => {
    render(<MonthlyActualTable counts={K2_COUNTS} rows={K2_ROWS} onToggle={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /^記録書Ⅱだけ（1）/ }));
    expect(screen.getByTestId('ma-row-k1')).toBeTruthy();
    expect(screen.queryByTestId('ma-row-b1')).toBeNull();
  });

  it('結果は実績と記録書Ⅱを分けて出し、知らない理由コードもそのまま出す', () => {
    const rows = [
      makeRow({
        id: 'b1',
        record2_start: '15:30',
        record2_end: '16:05',
        needs_record2: true,
        result_status: 'success',
        result_dry_run: false,
        record2_result_status: 'failed',
        record2_result_reason: 'new_reason_from_rpa',
        record2_result_dry_run: false,
      }),
      makeRow({
        id: 'k1',
        status: 'k2_only',
        needs_actual: false,
        needs_record2: true,
        record2_start: '15:18',
        record2_end: '16:20',
        record2_result_status: 'skipped',
        record2_result_reason: 'no_care_record',
      }),
    ];
    render(<MonthlyActualTable counts={K2_COUNTS} rows={rows} onToggle={vi.fn()} />);
    expect(screen.getByTestId('ma-result-b1').textContent).toContain('反映した');
    const b1 = screen.getByTestId('ma-record2-result-b1').textContent ?? '';
    expect(b1).toContain('失敗');
    expect(b1).toContain('new_reason_from_rpa');
    expect(screen.getByTestId('ma-record2-result-k1').textContent).toContain('記録書Ⅱが無い');
    // 実績は送らない行なので、実績の結果は出さない。
    expect(screen.queryByTestId('ma-result-k1')).toBeNull();
  });

  it('実績は反映済みで記録書Ⅱが失敗した行は、送る行と流し直しに入る', () => {
    const row = makeRow({
      id: 'b1',
      needs_record2: true,
      result_status: 'success',
      result_dry_run: false,
      record2_result_status: 'failed',
      record2_result_dry_run: false,
    });
    expect(pendingRows([row]).map((r) => r.id)).toEqual(['b1']);
    expect(failedRows([row]).map((r) => r.id)).toEqual(['b1']);
    const done = { ...row, record2_result_status: 'success' };
    expect(pendingRows([done])).toEqual([]);
  });

  it('試しは実績と記録書Ⅱの両方が進んで初めて「全部進んだ」', () => {
    const base = {
      dry_run_passed: true,
      result_status: 'dry_run_ok',
      result_dry_run: true,
      needs_record2: true,
    };
    const ng = makeRow({ ...base, record2_result_status: 'failed' });
    const ok = makeRow({ ...base, record2_result_status: 'dry_run_ok' });
    const k2only = makeRow({
      id: 'k1',
      status: 'k2_only',
      needs_actual: false,
      needs_record2: true,
      dry_run_passed: true,
      record2_result_status: 'dry_run_ok',
    });
    const view = (rows: MonthlyActualRow[]) =>
      makeList({ rows, job: job({ dry_run: true, running: false }) });
    expect(dryRunAllOk(view([ng]))).toBe(false);
    expect(dryRunAllOk(view([ok, k2only]))).toBe(true);
    expect(currentStep(view([ok, k2only]))).toBe(4);
  });
});
