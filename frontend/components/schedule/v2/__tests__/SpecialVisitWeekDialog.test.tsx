/**
 * SpecialVisitWeekDialog — 特別訪問週間の設定モーダル (Wave1 FE) テスト.
 *
 * 検証:
 *   ① 期間未設定 → 作成フォームが出る。期間チップで end_date が計算され
 *      POST /special-visit-periods 相当の mutation が呼ばれる.
 *   ② 期間あり → カレンダーが描かれる (固定訪問カード / ○ / ● / 週合計の
 *      達成・未達の出し分け。判定は data-testid + data-* 属性で行う).
 *   ③ 空きセルクリック → POST marks 相当の mutation が呼ばれる.
 *   ④ 退避トグル → POST displace。配置済みの退避解除は確認ダイアログ後に
 *      force=true 付きで restore が呼ばれる.
 *
 * モックの流儀は KaipokeConsole.test.tsx を踏襲 (vi.mock でクエリモジュールを
 * まるごと差し替え)。BE は並行実装中なので通信は一切行わない。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import type {
  SpecialCalendarWeek,
  SpecialVisitMark,
  SpecialVisitPeriod,
} from '@/lib/schemas/specialVisitWeek';

// ─── hoisted mocks ───────────────────────────────────────────────────────────

const { mocks, mockToast } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  mocks: {
    periods: [] as unknown[],
    periodsLoading: false,
    periodsError: false,
    weeks: [] as unknown[],
    createPeriod: vi.fn(),
    updatePeriod: vi.fn(),
    createMark: vi.fn(),
    deleteMark: vi.fn(),
    displace: vi.fn(),
    restore: vi.fn(),
  },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

// Radix Dialog は portal + focus trap を伴うため、描画契約の検証では素の div に置換する
// (PatientScheduleDetailDialog-pool-proposal.test.tsx と同じ流儀)。
vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogContent: ({ children, ...rest }: React.HTMLAttributes<HTMLDivElement>) => (
    <div {...rest}>{children}</div>
  ),
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/lib/queries/specialVisitWeek', () => ({
  useSpecialVisitPeriods: () => ({
    data: mocks.periods,
    isLoading: mocks.periodsLoading,
    isError: mocks.periodsError,
  }),
  useSpecialVisitCalendar: () => ({
    data: { period: mocks.periods[0] ?? null, weeks: mocks.weeks },
    isLoading: false,
    isError: false,
  }),
  useCreateSpecialVisitPeriod: () => ({ mutate: mocks.createPeriod, isPending: false }),
  useUpdateSpecialVisitPeriod: () => ({ mutate: mocks.updatePeriod, isPending: false }),
  useCreateSpecialVisitMark: () => ({ mutate: mocks.createMark, isPending: false }),
  useDeleteSpecialVisitMark: () => ({ mutate: mocks.deleteMark, isPending: false }),
  useDisplaceSpecialVisit: () => ({ mutate: mocks.displace, isPending: false }),
  useRestoreSpecialVisitMark: () => ({ mutate: mocks.restore, isPending: false }),
}));

import { SpecialVisitWeekDialog, computeEndDate } from '../SpecialVisitWeekDialog';

// ─── fixtures ────────────────────────────────────────────────────────────────

const PATIENT_ID = '11111111-1111-4111-8111-111111111111';

const PERIOD: SpecialVisitPeriod = {
  id: 'period-1',
  patient_id: PATIENT_ID,
  start_date: '2026-08-03',
  end_date: '2026-08-22',
  weekly_target: 5,
  note: null,
  status: 'active',
  created_at: null,
  updated_at: null,
};

function mark(over: Partial<SpecialVisitMark> = {}): SpecialVisitMark {
  return {
    id: 'mark-1',
    period_id: PERIOD.id,
    patient_id: PATIENT_ID,
    iso_year: 2026,
    iso_week: 32,
    weekday: 0,
    kind: 'extra',
    status: 'pool',
    placed_visit_id: null,
    placed_summary: null,
    ...over,
  };
}

function emptyDay(weekday: number, date: string) {
  return {
    weekday,
    date,
    fixed_visits: [],
    extra_mark: null,
    displaced_mark: null,
    preferred: [],
  };
}

/**
 * 週 0 (2026-W32):
 *   月 = 固定訪問あり (退避なし) / 火 = ○未配置 / 水 = ●配置済み /
 *   木 = 空き / 金 = 固定訪問 + 配置済みの退避チケット / 土 = 空き
 * 週 1 (2026-W33): すべて空き・未達 (4回 / 目標5)
 */
function makeWeeks(): SpecialCalendarWeek[] {
  const week0: SpecialCalendarWeek = {
    iso_year: 2026,
    iso_week: 32,
    week_monday: '2026-08-03',
    total: 5,
    target_met: true,
    days: [
      {
        ...emptyDay(0, '2026-08-03'),
        fixed_visits: [
          {
            visit_id: 'visit-1',
            start_time: '10:00',
            end_time: '11:00',
            course_label: '稲毛A',
            staff_name: '佐藤',
            generated: true,
          },
        ],
      },
      { ...emptyDay(1, '2026-08-04'), extra_mark: mark({ id: 'mark-pool', weekday: 1 }) },
      {
        ...emptyDay(2, '2026-08-05'),
        extra_mark: mark({
          id: 'mark-placed',
          weekday: 2,
          status: 'placed',
          placed_visit_id: 'visit-9',
          placed_summary: { start_time: '14:00', course_label: '都賀B' },
        }),
      },
      emptyDay(3, '2026-08-06'),
      {
        ...emptyDay(4, '2026-08-07'),
        fixed_visits: [
          {
            visit_id: 'visit-2',
            start_time: '09:30',
            end_time: '10:30',
            course_label: '稲毛B',
            staff_name: null,
            generated: true,
          },
        ],
        displaced_mark: mark({
          id: 'mark-displaced-placed',
          weekday: 4,
          kind: 'displaced',
          status: 'placed',
        }),
      },
      emptyDay(5, '2026-08-08'),
    ],
  };

  const week1: SpecialCalendarWeek = {
    iso_year: 2026,
    iso_week: 33,
    week_monday: '2026-08-10',
    total: 4,
    target_met: false,
    days: [0, 1, 2, 3, 4, 5].map((wd) =>
      emptyDay(wd, `2026-08-${String(10 + wd).padStart(2, '0')}`),
    ),
  };

  return [week0, week1];
}

function renderDialog() {
  return render(
    <SpecialVisitWeekDialog
      patientId={PATIENT_ID}
      patientName="山田 太郎"
      open
      onOpenChange={vi.fn()}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.periods = [];
  mocks.periodsLoading = false;
  mocks.periodsError = false;
  mocks.weeks = [];
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ─── tests ───────────────────────────────────────────────────────────────────

describe('SpecialVisitWeekDialog — ① 期間未設定 (作成フォーム)', () => {
  it('作成フォームが表示される (カレンダーは出ない)', () => {
    renderDialog();
    expect(screen.getByTestId('svw-create-form')).toBeInTheDocument();
    expect(screen.queryByTestId('svw-calendar')).not.toBeInTheDocument();
  });

  it('期間チップで end_date が計算され、作成 mutation が呼ばれる', () => {
    renderDialog();

    fireEvent.change(screen.getByTestId('svw-start-date'), { target: { value: '2026-08-03' } });
    fireEvent.click(screen.getByTestId('svw-preset-3週間'));

    // 開始日を含む 21 日間 → 8/3 + 20 日 = 8/23
    expect(screen.getByTestId('svw-computed-range').textContent).toContain('2026-08-23');

    fireEvent.click(screen.getByTestId('svw-create-button'));

    expect(mocks.createPeriod).toHaveBeenCalledTimes(1);
    expect(mocks.createPeriod.mock.calls[0]![0]).toEqual({
      patient_id: PATIENT_ID,
      start_date: '2026-08-03',
      end_date: '2026-08-23',
      weekly_target: 5,
      note: null,
    });
  });

  it('computeEndDate: 月指定は「翌月同日の前日」になる', () => {
    expect(computeEndDate('2026-08-03', { label: '1ヶ月', months: 1 })).toBe('2026-09-02');
    expect(computeEndDate('2026-08-03', { label: '2ヶ月', months: 2 })).toBe('2026-10-02');
    expect(computeEndDate('2026-08-03', { label: '1週間', days: 7 })).toBe('2026-08-09');
  });
});

describe('SpecialVisitWeekDialog — ② カレンダー表示', () => {
  beforeEach(() => {
    mocks.periods = [PERIOD];
    mocks.weeks = makeWeeks();
  });

  it('固定訪問カード・○・● が描かれる', () => {
    renderDialog();

    expect(screen.getByTestId('svw-calendar')).toBeInTheDocument();

    // 月曜: 固定訪問カード (時刻 + コースラベル)
    const fixed = screen.getByTestId('svw-fixed-0-0-0');
    expect(fixed.textContent).toContain('10:00');
    expect(fixed.textContent).toContain('稲毛A');
    expect(fixed.getAttribute('data-displaced')).toBe('false');

    // 火曜: ○ (未配置)
    const poolMark = screen.getByTestId('svw-mark-0-1');
    expect(poolMark.getAttribute('data-status')).toBe('pool');
    expect(poolMark.textContent).toContain('○');

    // 水曜: ● (配置済み・配置先時刻の小書き)
    const placedMark = screen.getByTestId('svw-mark-0-2');
    expect(placedMark.getAttribute('data-status')).toBe('placed');
    expect(placedMark.textContent).toContain('●');
    expect(placedMark.textContent).toContain('14:00');
    expect(placedMark.textContent).toContain('都賀B');

    // 木曜: ○ が無いので空きスペースボタン
    expect(screen.getByTestId('svw-empty-0-3')).toBeInTheDocument();
  });

  it('週合計は達成/未達で出し分けられる', () => {
    renderDialog();

    const met = screen.getByTestId('svw-total-0');
    expect(met.getAttribute('data-met')).toBe('true');
    expect(met.textContent).toContain('5回');
    expect(met.className).toContain('bg-success-bg');

    const notMet = screen.getByTestId('svw-total-1');
    expect(notMet.getAttribute('data-met')).toBe('false');
    expect(notMet.textContent).toContain('4回');
    expect(notMet.textContent).toContain('目標5');
    expect(notMet.className).toContain('bg-error-bg');
  });

  it('退避中の固定訪問は打ち消し線 + 「プールへ退避中」バッジになる', () => {
    renderDialog();

    const displacedCard = screen.getByTestId('svw-fixed-0-4-0');
    expect(displacedCard.getAttribute('data-displaced')).toBe('true');
    expect(displacedCard.className).toContain('line-through');
    expect(screen.getByTestId('svw-displaced-badge-0-4').textContent).toContain('プールへ退避中');
  });
});

describe('SpecialVisitWeekDialog — ③ ○ の追加 / 取消', () => {
  beforeEach(() => {
    mocks.periods = [PERIOD];
    mocks.weeks = makeWeeks();
  });

  it('空きセルクリックで marks の作成 mutation が呼ばれる', () => {
    renderDialog();

    fireEvent.click(screen.getByTestId('svw-empty-0-3'));

    expect(mocks.createMark).toHaveBeenCalledTimes(1);
    expect(mocks.createMark.mock.calls[0]![0]).toEqual({
      periodId: 'period-1',
      payload: { iso_year: 2026, iso_week: 32, weekday: 3 },
    });
  });

  it('未配置 ○ のクリックは確認なしで削除 (force なし)', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderDialog();

    fireEvent.click(screen.getByTestId('svw-mark-0-1'));

    expect(confirmSpy).not.toHaveBeenCalled();
    expect(mocks.deleteMark.mock.calls[0]![0]).toEqual({ markId: 'mark-pool', force: false });
  });

  it('配置済み ● のクリックは確認後に force=true で削除', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderDialog();

    fireEvent.click(screen.getByTestId('svw-mark-0-2'));

    expect(confirmSpy).toHaveBeenCalled();
    expect(mocks.deleteMark.mock.calls[0]![0]).toEqual({ markId: 'mark-placed', force: true });
  });
});

describe('SpecialVisitWeekDialog — ④ 退避トグル', () => {
  beforeEach(() => {
    mocks.periods = [PERIOD];
    mocks.weeks = makeWeeks();
  });

  it('「この日はプールへ退避」で displace mutation が呼ばれる', () => {
    renderDialog();

    const toggle = screen.getByTestId('svw-displace-toggle-0-0');
    expect(toggle.getAttribute('data-displaced')).toBe('false');
    expect(toggle.textContent).toContain('この日はプールへ退避');

    fireEvent.click(toggle);

    expect(mocks.displace).toHaveBeenCalledTimes(1);
    expect(mocks.displace.mock.calls[0]![0]).toEqual({
      periodId: 'period-1',
      payload: { iso_year: 2026, iso_week: 32, weekday: 0 },
    });
  });

  it('配置済み退避の解除は確認ダイアログ → force=true で restore', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderDialog();

    const toggle = screen.getByTestId('svw-displace-toggle-0-4');
    expect(toggle.getAttribute('data-displaced')).toBe('true');
    expect(toggle.textContent).toContain('固定どおりに戻す');

    fireEvent.click(toggle);

    expect(confirmSpy).toHaveBeenCalled();
    expect(mocks.restore).toHaveBeenCalledTimes(1);
    expect(mocks.restore.mock.calls[0]![0]).toEqual({
      markId: 'mark-displaced-placed',
      force: true,
    });
  });

  it('確認ダイアログでキャンセルしたら restore は呼ばれない', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderDialog();

    fireEvent.click(screen.getByTestId('svw-displace-toggle-0-4'));

    expect(mocks.restore).not.toHaveBeenCalled();
  });
});
