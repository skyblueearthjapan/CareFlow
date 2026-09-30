/** /dashboard: 管理者 = スタッフ別の実績 (B カード → C 1 人) + 今日の運用 / staff = 今までの数字だけ。
 *
 * 設計: docs/plans/dashboard-staff-performance-design-2026-09-30.md §4〜§6。
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

const useSession = vi.fn();
vi.mock('next-auth/react', () => ({
  useSession: () => useSession(),
}));

const useDashboardKpi = vi.fn();
const useDashboardTrend = vi.fn();
const useStaffPerformance = vi.fn();
vi.mock('@/lib/queries/dashboard', () => ({
  useDashboardKpi: () => useDashboardKpi(),
  useDashboardTrend: (...a: unknown[]) => useDashboardTrend(...a),
  useStaffPerformance: (...a: unknown[]) => useStaffPerformance(...a),
}));

import type { PerformanceMetrics, StaffPerformanceResponse } from '@/lib/schemas/dashboard';

import DashboardPage from '../page';

beforeAll(() => {
  // recharts の ResponsiveContainer が使う (jsdom には無い)。
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

function metrics(over: Partial<PerformanceMetrics> = {}): PerformanceMetrics {
  return {
    days: 4,
    visits: 20,
    patients: 12,
    per_day: 5,
    plan_min: 36,
    actual_min: null,
    actual_samples: 0,
    arrival_only: 0,
    qr_ratio: 0,
    km_total: 100,
    km_per_day: 25,
    km_per_visit: 5,
    skipped_legs: 0,
    visit_min_per_day: 180,
    travel_min_per_day: 75,
    meeting_min_per_day: 30,
    idle_min_per_day: 60,
    meeting_min_total: 120,
    no_show_count: 0,
    staff_count: null,
    ...over,
  };
}

const OFFICE_A = '11111111-1111-4111-8111-111111111111';
const OFFICE_B = '22222222-2222-4222-8222-222222222222';
const S1 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const S2 = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';

const PERF: StaffPerformanceResponse = {
  date_from: '2026-09-01',
  date_to: '2026-09-13',
  office_id: null,
  travel_speed_kmh: 20,
  min_actual_samples: 5,
  weeks: [
    { start: '2026-09-01', end: '2026-09-06' },
    { start: '2026-09-07', end: '2026-09-13' },
  ],
  offices: [
    { id: OFFICE_A, name: '第一ステーション', short_label: '一' },
    { id: OFFICE_B, name: '第二ステーション', short_label: '二' },
  ],
  team: {
    period: metrics({ visits: 40, days: 8, staff_count: 2, qr_ratio: 0.1, actual_samples: 4 }),
    weeks: [metrics({ staff_count: 2 }), metrics({ staff_count: 2 })],
  },
  staff: [
    {
      staff_id: S1,
      name: '佐藤 花子',
      office_id: OFFICE_A,
      office_short: '一',
      is_manager: true,
      is_trainee: false,
      qualification: '准看護師',
      period: metrics({ per_day: 5.5, actual_min: 41, actual_samples: 6, qr_ratio: 0.3 }),
      weeks: [metrics({ per_day: 5 }), metrics({ per_day: 6 })],
    },
    {
      staff_id: S2,
      name: '鈴木 次郎',
      office_id: OFFICE_B,
      office_short: '二',
      is_manager: false,
      is_trainee: true,
      qualification: '看護師',
      period: metrics({ per_day: 4.5, arrival_only: 3, no_show_count: 2 }),
      weeks: [metrics({ per_day: 4 }), metrics({ days: 0, visits: 0, per_day: null })],
    },
  ],
};

const KPI = {
  today_visits: 12,
  today_completed: 3,
  today_unassigned: 1,
  today_overlapping: 0,
  this_week_visits: 48,
  this_week_completion_rate: 0.5,
};

beforeEach(() => {
  useDashboardKpi.mockReturnValue({ data: KPI, isLoading: false, isError: false });
  useDashboardTrend.mockReturnValue({
    data: { items: [], days: 7, start_date: '2026-09-24', end_date: '2026-09-30' },
    isLoading: false,
    isError: false,
  });
  useStaffPerformance.mockReset();
  useStaffPerformance.mockReturnValue({ data: PERF, isLoading: false, isError: false });
});

function asRole(role: 'admin' | 'staff') {
  useSession.mockReturnValue({ data: { user: { role } }, status: 'authenticated' });
}

describe('管理者', () => {
  it('チーム全体の数字・QR の注意書き・今日の運用・注意の 3 点を出す', () => {
    asRole('admin');
    render(<DashboardPage />);
    expect(
      screen.getByText('スタッフごとの訪問の実績を、件数・時間・移動で見ます。'),
    ).toBeInTheDocument();
    expect(screen.getByText('1 人 1 日あたり')).toBeInTheDocument();
    expect(screen.getByText('QR で時間が取れた訪問')).toBeInTheDocument();
    expect(screen.getByText('2 人・のべ 8 日（2 名訪問はそれぞれに数えます）')).toBeInTheDocument();
    // 管理者の画面では 7 日のトレンドを呼ばない。
    expect(useDashboardTrend).toHaveBeenLastCalledWith(7, { enabled: false });
    expect(screen.getByRole('note')).toHaveTextContent('QR の到着と退出が揃った訪問（10%）');
    // 今日の運用は小さく残す。
    const ops = screen.getByTestId('ops-summary');
    expect(within(ops).getByText('今日の訪問')).toBeInTheDocument();
    expect(within(ops).getByText('12 件')).toBeInTheDocument();
    const caveats = screen.getByTestId('perf-caveats');
    expect(caveats).toHaveTextContent('実績の時間は参考値です。');
    expect(caveats).toHaveTextContent('役割の札');
    expect(caveats).toHaveTextContent('距離は直線です。');
    // 実績は「今月」から始まる。
    const params = useStaffPerformance.mock.calls.at(-1)?.[0] as { from: string; enabled: boolean };
    expect(params.from).toMatch(/^\d{4}-\d{2}-01$/);
    expect(params.enabled).toBe(true);
  });

  it('B: 1 人 1 枚のカードに役割の札と実績を出す', () => {
    asRole('admin');
    render(<DashboardPage />);
    const cards = screen.getAllByTestId('perf-staff-card');
    expect(cards).toHaveLength(2);
    const first = within(cards[0]!);
    expect(first.getByText('佐藤 花子')).toBeInTheDocument();
    for (const tag of ['一', '管理者', '准看護師', '出勤 4 日']) {
      expect(first.getByText(tag)).toBeInTheDocument();
    }
    expect(first.getByText('実績 41 分・QR 6 件')).toBeInTheDocument();
    const second = within(cards[1]!);
    expect(second.getByText('新人')).toBeInTheDocument();
    expect(second.queryByText('准看護師')).not.toBeInTheDocument();
    expect(second.getByText('実績 —（到着のみ 3 件）')).toBeInTheDocument();
    expect(second.getByText('未訪問の記録 2 件')).toBeInTheDocument();
    expect(first.queryByText(/未訪問の記録/)).not.toBeInTheDocument();
    // カードは article・押す所は名前付きのボタン。
    expect(cards[0]!.tagName).toBe('ARTICLE');
    expect(
      screen.getByRole('button', { name: '佐藤 花子の実績をくわしく見る' }),
    ).toBeInTheDocument();
  });

  it('チームの実績の時間は「全員の QR 記録の平均」と書く', () => {
    asRole('admin');
    useStaffPerformance.mockReturnValue({
      data: {
        ...PERF,
        team: { ...PERF.team, period: { ...PERF.team.period, actual_min: 38, actual_samples: 9 } },
      },
      isLoading: false,
      isError: false,
    });
    render(<DashboardPage />);
    expect(screen.getByText('実績 38 分（全員の QR 記録の平均・9 件）')).toBeInTheDocument();
  });

  it('カードを押すと C (1 人を深く見る) が開き、戻れる', () => {
    asRole('admin');
    render(<DashboardPage />);
    fireEvent.click(screen.getByRole('button', { name: '鈴木 次郎の実績をくわしく見る' }));
    const deep = screen.getByTestId('perf-deep-view');
    expect(within(deep).getAllByTestId('perf-line-chart')).toHaveLength(4);
    expect(within(deep).getByText('1 日あたりの訪問件数')).toBeInTheDocument();
    expect(within(deep).getByText('1 日あたりの訪問の合間')).toBeInTheDocument();
    // 凡例 (系列が 2 本以上)。
    expect(within(deep).getAllByText('チーム平均').length).toBeGreaterThan(0);
    // 凡例と週の表の見出しの両方。
    expect(within(deep).getAllByText('実績（QR）')).toHaveLength(2);
    // 1 日の内訳: 会議・研修などを合間と分けて出す。
    const breakdown = screen.getByTestId('perf-breakdown');
    expect(breakdown).toHaveTextContent('会議・研修など 0:30');
    expect(breakdown).toHaveTextContent('訪問の合間 1:00');
    expect(breakdown).toHaveTextContent('時速 20km');
    expect(deep).toHaveTextContent('未訪問の記録 2 件（件数には含めています）');
    expect(within(deep).getByText('会議・研修など（週計）')).toBeInTheDocument();
    // 週ごとの表 (訪問の無い週は「—」)。
    const rows = within(screen.getByTestId('perf-week-table')).getAllByRole('row');
    expect(rows).toHaveLength(3);
    expect(rows[2]).toHaveTextContent('9/7 の週');
    expect(rows[2]).toHaveTextContent('—');

    fireEvent.click(screen.getByRole('button', { name: /全員のカードに戻る/ }));
    expect(screen.queryByTestId('perf-deep-view')).not.toBeInTheDocument();
    expect(screen.getAllByTestId('perf-staff-card')).toHaveLength(2);
  });

  describe('期間 (今日 = 2026-10-01 JST)', () => {
    beforeEach(() => {
      vi.useFakeTimers({ toFake: ['Date'] });
      vi.setSystemTime(new Date('2026-10-01T03:00:00Z'));
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    const last = () => useStaffPerformance.mock.calls.at(-1)?.[0] as Record<string, unknown>;

    it('今月は今日まで・先月は 9/1〜9/30・拠点で絞り込む', () => {
      asRole('admin');
      render(<DashboardPage />);
      expect(last()).toMatchObject({ from: '2026-10-01', to: '2026-10-01', officeId: null });
      fireEvent.click(screen.getByRole('button', { name: '第二ステーション' }));
      expect(last().officeId).toBe(OFFICE_B);
      fireEvent.click(screen.getByRole('button', { name: '先月' }));
      expect(last()).toMatchObject({ from: '2026-09-01', to: '2026-09-30', enabled: true });
      expect(screen.getByText('9/1〜9/30')).toBeInTheDocument();
    });

    it('任意は 93 日までにする', () => {
      asRole('admin');
      render(<DashboardPage />);
      fireEvent.click(screen.getByRole('button', { name: '任意' }));
      fireEvent.change(screen.getByLabelText('期間の始め'), { target: { value: '2026-05-01' } });
      fireEvent.change(screen.getByLabelText('期間の終わり'), { target: { value: '2026-09-30' } });
      expect(screen.getByText('期間は 93 日以内で選んでください。')).toBeInTheDocument();
      expect(last().enabled).toBe(false);
      fireEvent.change(screen.getByLabelText('期間の始め'), { target: { value: '2026-07-01' } });
      expect(last()).toMatchObject({ from: '2026-07-01', to: '2026-09-30', enabled: true });
    });
  });

  it('QR の割合が取れないときは % を付けない', () => {
    asRole('admin');
    useStaffPerformance.mockReturnValue({
      data: { ...PERF, team: { ...PERF.team, period: { ...PERF.team.period, qr_ratio: null } } },
      isLoading: false,
      isError: false,
    });
    render(<DashboardPage />);
    const tile = screen.getByText('QR で時間が取れた訪問').parentElement!;
    expect(tile).toHaveTextContent('—');
    expect(tile).not.toHaveTextContent('%');
  });
});

describe('権限が分かる前', () => {
  it('枠だけ出し、staff の画面も実績も出さない', () => {
    useSession.mockReturnValue({ data: null, status: 'loading' });
    render(<DashboardPage />);
    expect(screen.getByTestId('dashboard-loading')).toBeInTheDocument();
    expect(screen.queryByText('今週完了率')).not.toBeInTheDocument();
    expect(useStaffPerformance).not.toHaveBeenCalled();
    expect(useDashboardTrend).toHaveBeenLastCalledWith(7, { enabled: false });
  });
});

describe('staff ロール', () => {
  it('今までの数字だけを出し、スタッフ別の実績は呼ばない・出さない', () => {
    asRole('staff');
    render(<DashboardPage />);
    expect(screen.getByText('本日の概要と直近 7 日間の訪問トレンド')).toBeInTheDocument();
    expect(screen.getByText('今週完了率')).toBeInTheDocument();
    expect(screen.getByText('訪問トレンド (直近 7 日)')).toBeInTheDocument();
    expect(useStaffPerformance).not.toHaveBeenCalled();
    expect(useDashboardTrend).toHaveBeenLastCalledWith(7, { enabled: true });
    expect(screen.queryByTestId('perf-staff-card')).not.toBeInTheDocument();
    expect(screen.queryByTestId('perf-caveats')).not.toBeInTheDocument();
    expect(screen.queryByText('QR で時間が取れた訪問')).not.toBeInTheDocument();
  });
});
