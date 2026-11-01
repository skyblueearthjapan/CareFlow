/**
 * 「月の実績」タブの部品の vitest。
 *
 * 縛る挙動:
 *   1. 表は差がある行だけ。一致は件数だけ（「一致 N 件は対象外」）
 *   2. 「する／しない」の切り替え。人が見る行は確認の一言を挟む。カイポケに無い／だけの行は切り替えない
 *   3. 反映・予約の前に、月と件数を書いた確認を画面の中で出す
 *   4. 段の決め方（試しが全部進んだら「反映する」）
 *   5. 二度押しで 2 回送らない
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';

import type {
  MonthlyActualCounts,
  MonthlyActualList,
  MonthlyActualRow,
} from '@/lib/schemas/monthly-actual';

const { hooks } = vi.hoisted(() => ({
  hooks: {
    list: { data: undefined as unknown, isLoading: false, isError: false, error: null },
    applyMutateAsync: vi.fn(),
    refreshMutateAsync: vi.fn(),
    updateMutate: vi.fn(),
    statusEnabled: [] as boolean[],
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
}));

import { MonthlyActualRunPanel } from '../_components/monthly-actual/MonthlyActualRunPanel';
import { MonthlyActualTab } from '../_components/monthly-actual/MonthlyActualTab';
import { MonthlyActualTable } from '../_components/monthly-actual/MonthlyActualTable';
import {
  currentStep,
  dryRunAllOk,
  flagChips,
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
    ...over,
  };
}

const COUNTS: MonthlyActualCounts = {
  match: 378,
  apply: 1,
  review: 1,
  missing_in_kaipoke: 1,
  kaipoke_only: 0,
  total: 381,
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
    const ok = makeList({ job: job(), rows: [makeRow({ result_status: 'dry_run_ok' })] });
    expect(dryRunAllOk(ok)).toBe(true);
    expect(currentStep(ok)).toBe(4);
    const ng = makeList({
      job: job(),
      rows: [
        makeRow({ id: 'a', result_status: 'dry_run_ok' }),
        makeRow({ id: 'b', result_status: 'failed', result_reason: 'not_found' }),
      ],
    });
    expect(currentStep(ng)).toBe(3);
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
