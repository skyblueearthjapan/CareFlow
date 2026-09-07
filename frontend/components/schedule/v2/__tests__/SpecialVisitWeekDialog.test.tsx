/**
 * SpecialVisitWeekDialog — 特別訪問週間の設定モーダル (Wave1 FE) テスト.
 *
 * 検証:
 *   ① 期間未設定 → 作成フォームが出る。期間チップで end_date が計算され
 *      POST /special-visit-periods 相当の mutation が呼ばれる.
 *   ② 期間あり → カレンダーが描かれる (固定訪問カード / ○ / ● / 週合計の
 *      達成・未達の出し分け。判定は data-testid + data-* 属性で行う).
 *   ③ セルクリック = **メニュー** (即実行しない)。追加 / 取消 (確認あり) /
 *      「配置先を決める…」→ 配置モーダル。
 *   ④ 退避トグル → POST displace。配置済みの退避解除は確認ダイアログ後に
 *      force=true 付きで restore が呼ばれる.
 *   ⑤ 凡例 + 未配置件数 / 期間終了は「…」メニュー + 確認.
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

// 配置モーダル (＋訪問) は API を張るので、この画面のテストでは薄く差し替える。
// JST の今日も同じモジュールから来る = 過去日判定を固定できる (週0の月曜)。
vi.mock('../SpecialVisitPlaceLauncher', () => ({
  todayIsoJst: () => '2026-08-03',
  SpecialVisitPlaceLauncher: (props: {
    markId: string | null;
    replacingMarkId?: string | null;
    replacingVisitId?: string | null;
    replacingStartHM?: string | null;
    date: string;
    isoYear: number;
    isoWeek: number;
    weekday: number;
    onOpenChange: (open: boolean) => void;
  }) => (
    <div
      data-testid="svw-place-launcher"
      data-mark-id={props.markId ?? ''}
      data-replacing-mark-id={props.replacingMarkId ?? ''}
      data-replacing-visit-id={props.replacingVisitId ?? ''}
      data-replacing-start={props.replacingStartHM ?? ''}
      data-date={props.date}
      data-iso-week={props.isoWeek}
      data-weekday={props.weekday}
    >
      <button
        type="button"
        data-testid="svw-place-launcher-close"
        onClick={() => props.onOpenChange(false)}
      >
        閉じる
      </button>
    </div>
  ),
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

describe('SpecialVisitWeekDialog — ③ セルメニュー (追加 / 取消 / 配置)', () => {
  beforeEach(() => {
    mocks.periods = [PERIOD];
    mocks.weeks = makeWeeks();
  });

  it('空きセルはメニューの「この日に追加枠を付ける（○）」で作成 mutation が呼ばれる', () => {
    renderDialog();

    fireEvent.click(screen.getByTestId('svw-empty-0-3'));
    expect(mocks.createMark).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('svw-menu-add-0-3'));

    expect(mocks.createMark).toHaveBeenCalledTimes(1);
    expect(mocks.createMark.mock.calls[0]![0]).toEqual({
      periodId: 'period-1',
      payload: { iso_year: 2026, iso_week: 32, weekday: 3 },
    });
  });

  it('○ のクリックはメニューを開くだけで、取消は走らない', () => {
    renderDialog();

    fireEvent.click(screen.getByTestId('svw-mark-0-1'));

    expect(screen.getByTestId('svw-menu-0-1')).toBeInTheDocument();
    expect(screen.getByTestId('svw-menu-place-0-1')).toHaveTextContent('この日の配置先を決める');
    expect(mocks.deleteMark).not.toHaveBeenCalled();
  });

  it('「追加枠を取り消す」は確認ダイアログを挟み、force なしで削除する', () => {
    renderDialog();

    fireEvent.click(screen.getByTestId('svw-mark-0-1'));
    fireEvent.click(screen.getByTestId('svw-menu-cancel-0-1'));

    // 確認を出すまでは削除しない。
    expect(mocks.deleteMark).not.toHaveBeenCalled();
    expect(screen.getByTestId('svw-confirm')).toHaveTextContent('追加枠を取り消しますか？');
    expect(screen.getByTestId('svw-confirm')).toHaveTextContent('プールからも消えます');

    fireEvent.click(screen.getByTestId('svw-confirm-ok'));

    expect(mocks.deleteMark.mock.calls[0]![0]).toEqual({ markId: 'mark-pool', force: false });
  });

  it('確認ダイアログで「やめる」を押したら削除しない', () => {
    renderDialog();

    fireEvent.click(screen.getByTestId('svw-mark-0-1'));
    fireEvent.click(screen.getByTestId('svw-menu-cancel-0-1'));
    fireEvent.click(screen.getByTestId('svw-confirm-cancel'));

    expect(mocks.deleteMark).not.toHaveBeenCalled();
  });

  it('● の「配置を取り消す（訪問も削除）」は確認後に force=true で削除', () => {
    renderDialog();

    fireEvent.click(screen.getByTestId('svw-mark-0-2'));
    expect(screen.getByTestId('svw-menu-cancel-0-2')).toHaveTextContent('配置を取り消す');
    fireEvent.click(screen.getByTestId('svw-menu-cancel-0-2'));

    expect(mocks.deleteMark).not.toHaveBeenCalled();
    expect(screen.getByTestId('svw-confirm')).toHaveTextContent('配置を取り消しますか？');

    fireEvent.click(screen.getByTestId('svw-confirm-ok'));

    expect(mocks.deleteMark.mock.calls[0]![0]).toEqual({ markId: 'mark-placed', force: true });
  });

  it('「この日の配置先を決める…」で配置モーダルがその日・そのマークで開く', () => {
    renderDialog();

    expect(screen.queryByTestId('svw-place-launcher')).toBeNull();

    fireEvent.click(screen.getByTestId('svw-mark-0-1'));
    fireEvent.click(screen.getByTestId('svw-menu-place-0-1'));

    const launcher = screen.getByTestId('svw-place-launcher');
    expect(launcher.getAttribute('data-mark-id')).toBe('mark-pool');
    expect(launcher.getAttribute('data-replacing-mark-id')).toBe('');
    expect(launcher.getAttribute('data-date')).toBe('2026-08-04');
    expect(launcher.getAttribute('data-iso-week')).toBe('32');
    expect(launcher.getAttribute('data-weekday')).toBe('1');
  });

  it('● の「配置を変更する…」は確認のうえ、先に壊さず入れ替えモードで開く', () => {
    renderDialog();

    fireEvent.click(screen.getByTestId('svw-mark-0-2'));
    expect(screen.getByTestId('svw-menu-place-0-2')).toHaveTextContent('配置を変更する');
    fireEvent.click(screen.getByTestId('svw-menu-place-0-2'));

    expect(screen.getByTestId('svw-confirm')).toHaveTextContent('配置を決め直しますか？');
    expect(screen.getByTestId('svw-confirm')).toHaveTextContent(
      '新しい配置を決めてから、いまの訪問を入れ替えます',
    );
    fireEvent.click(screen.getByTestId('svw-confirm-ok'));

    const launcher = screen.getByTestId('svw-place-launcher');
    expect(launcher.getAttribute('data-mark-id')).toBe('');
    expect(launcher.getAttribute('data-replacing-mark-id')).toBe('mark-placed');
    expect(launcher.getAttribute('data-replacing-visit-id')).toBe('visit-9');
    // 同時刻での入れ替えを止めるため、いまの開始時刻も渡す。
    expect(launcher.getAttribute('data-replacing-start')).toBe('14:00');
    // 今の配置はまだ触らない (新しい訪問ができるまで壊さない)。
    expect(mocks.deleteMark).not.toHaveBeenCalled();
    expect(mocks.createMark).not.toHaveBeenCalled();
  });

  it('入れ替えを途中でやめても、いまの配置は消えない', () => {
    renderDialog();

    fireEvent.click(screen.getByTestId('svw-mark-0-2'));
    fireEvent.click(screen.getByTestId('svw-menu-place-0-2'));
    fireEvent.click(screen.getByTestId('svw-confirm-ok'));
    fireEvent.click(screen.getByTestId('svw-place-launcher-close'));

    expect(screen.queryByTestId('svw-place-launcher')).toBeNull();
    expect(mocks.deleteMark).not.toHaveBeenCalled();
    expect(mocks.createMark).not.toHaveBeenCalled();
  });

  it('当日以前の日は「配置先を決める…」を押せない', () => {
    // JST の今日 = 2026-08-03 (モック)。週0の月曜に未配置の ○ を置く。
    const weeks = makeWeeks();
    weeks[0]!.days[0]!.extra_mark = mark({ id: 'mark-past', weekday: 0 });
    mocks.weeks = weeks;
    renderDialog();

    fireEvent.click(screen.getByTestId('svw-mark-0-0'));

    expect(screen.getByTestId('svw-menu-place-0-0')).toBeDisabled();
    expect(screen.getByTestId('svw-menu-past-0-0')).toHaveTextContent('過去日は配置できません');
    // 取消はできる (過去日でも枠は片付けられる)。
    expect(screen.getByTestId('svw-menu-cancel-0-0')).toBeEnabled();
  });
});

describe('SpecialVisitWeekDialog — ④ 退避トグル', () => {
  beforeEach(() => {
    mocks.periods = [PERIOD];
    mocks.weeks = makeWeeks();
  });

  it('「この日の固定訪問をプールへ退避」で displace mutation が呼ばれる', () => {
    renderDialog();

    fireEvent.click(screen.getByTestId('svw-empty-0-0'));
    const toggle = screen.getByTestId('svw-displace-toggle-0-0');
    expect(toggle.getAttribute('data-displaced')).toBe('false');
    expect(toggle.textContent).toContain('プールへ退避');

    fireEvent.click(toggle);

    expect(mocks.displace).toHaveBeenCalledTimes(1);
    expect(mocks.displace.mock.calls[0]![0]).toEqual({
      periodId: 'period-1',
      payload: { iso_year: 2026, iso_week: 32, weekday: 0 },
    });
  });

  it('配置済み退避の解除は確認ダイアログ → force=true で restore', () => {
    renderDialog();

    fireEvent.click(screen.getByTestId('svw-empty-0-4'));
    const toggle = screen.getByTestId('svw-displace-toggle-0-4');
    expect(toggle.getAttribute('data-displaced')).toBe('true');
    expect(toggle.textContent).toContain('固定どおりに戻す');

    fireEvent.click(toggle);

    expect(mocks.restore).not.toHaveBeenCalled();
    expect(screen.getByTestId('svw-confirm')).toHaveTextContent('固定どおりに戻しますか？');

    fireEvent.click(screen.getByTestId('svw-confirm-ok'));

    expect(mocks.restore).toHaveBeenCalledTimes(1);
    expect(mocks.restore.mock.calls[0]![0]).toEqual({
      markId: 'mark-displaced-placed',
      force: true,
    });
  });

  it('確認ダイアログでキャンセルしたら restore は呼ばれない', () => {
    renderDialog();

    fireEvent.click(screen.getByTestId('svw-empty-0-4'));
    fireEvent.click(screen.getByTestId('svw-displace-toggle-0-4'));
    fireEvent.click(screen.getByTestId('svw-confirm-cancel'));

    expect(mocks.restore).not.toHaveBeenCalled();
  });
});

describe('SpecialVisitWeekDialog — ⑤ 凡例 / 期間の終了', () => {
  beforeEach(() => {
    mocks.periods = [PERIOD];
    mocks.weeks = makeWeeks();
  });

  it('凡例と未配置の追加枠の件数を出す', () => {
    renderDialog();

    expect(screen.getByTestId('svw-legend')).toHaveTextContent(
      '○ 未配置（クリックで配置先を決める）',
    );
    // 週0 火の ○ 1 件のみ (● と退避チケットは数えない)。
    expect(screen.getByTestId('svw-pool-count')).toHaveTextContent('未配置の追加枠 1 件');
  });

  it('期間の終了は「…」メニューの中にあり、確認してから実行される', () => {
    renderDialog();

    // 常時表示はしない (誤操作の重い操作)。
    expect(screen.queryByTestId('svw-period-end')).toBeNull();

    fireEvent.click(screen.getByTestId('svw-period-more'));
    fireEvent.click(screen.getByTestId('svw-period-end'));

    expect(mocks.updatePeriod).not.toHaveBeenCalled();
    expect(screen.getByTestId('svw-confirm')).toHaveTextContent('期間を終了しますか？');

    fireEvent.click(screen.getByTestId('svw-confirm-ok'));

    expect(mocks.updatePeriod.mock.calls[0]![0]).toEqual({
      periodId: 'period-1',
      payload: { status: 'ended' },
    });
  });
});
