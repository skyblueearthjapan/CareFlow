/**
 * ScheduleHealthDialog テスト (Schedule Advisor Phase 1).
 *
 * useScheduleHealth をモックし、以下を施錠する:
 *   1. サマリタイルの値 (移動 / 隙間 / 距離, office 横断合計) と前週比 delta の符号色.
 *   2. コース別バーの 1.5 倍ハイライト (data-high + warning バー).
 *   3. 曜日フィルタで表示コースが切り替わる.
 *   4. 空状態 (offices 空).
 *   5. 前週データなし時のフォールバック表示.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import React from 'react';

import type { ScheduleHealthResponse } from '@/lib/schemas/v2/scheduleHealth';

// ─── モック ──────────────────────────────────────────────────────────────────

const { scheduleHealthMock } = vi.hoisted(() => ({
  scheduleHealthMock: vi.fn(),
}));

vi.mock('@/lib/queries/scheduleHealth', () => ({
  useScheduleHealth: (params: { isoWeek: number }) => scheduleHealthMock(params),
}));

vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? <div data-testid="dialog">{children}</div> : null,
  DialogContent: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="dialog-content">{children}</div>
  ),
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
}));

vi.mock('@/components/ui/button', () => ({
  Button: ({ children, onClick }: { children?: React.ReactNode; onClick?: () => void }) => (
    <button onClick={onClick}>{children}</button>
  ),
}));

vi.mock('lucide-react', () => ({
  HeartPulse: () => <span data-testid="icon-heart-pulse" />,
  Loader2: () => <span />,
  TriangleAlert: () => <span data-testid="icon-triangle-alert" />,
}));

import { ScheduleHealthDialog } from '../ScheduleHealthDialog';

// ─── フィクスチャ ──────────────────────────────────────────────────────────────

function totals(travel: number, gap: number, km: number) {
  return {
    visit_count: 0,
    service_minutes: 0,
    travel_minutes: travel,
    travel_km: km,
    buffer_minutes: 0,
    gap_minutes: gap,
  };
}

function course(code: string, staff: string | null, travel: number, gap: number, visits: number) {
  return {
    course_code: code,
    staff_name: staff,
    visit_count: visits,
    patient_count: visits,
    service_minutes: 0,
    travel_minutes: travel,
    travel_km: 0,
    buffer_minutes: 0,
    gap_minutes: gap,
  };
}

// 当週: week_totals travel=110/gap=50/km=20. weekday0 に A(移動100)+B(移動10),
// weekday1 に C(移動30). A は表示中平均(≈46.7)*1.5 超で high 判定される.
const CURRENT: ScheduleHealthResponse = {
  iso_year: 2026,
  iso_week: 27,
  offices: [
    {
      office_id: 'o1',
      office_name: '本部',
      weekdays: [
        {
          weekday: 0,
          courses: [course('A', '田中', 100, 18, 5), course('B', '佐藤', 10, 4, 2)],
          totals: totals(110, 22, 20),
        },
        {
          weekday: 1,
          courses: [course('C', '鈴木', 30, 8, 3)],
          totals: totals(30, 8, 0),
        },
      ],
      week_totals: totals(110, 50, 20),
    },
  ],
};

// 前週: week_totals travel=90/gap=60/km=25.
const PREV: ScheduleHealthResponse = {
  iso_year: 2026,
  iso_week: 26,
  offices: [
    {
      office_id: 'o1',
      office_name: '本部',
      weekdays: [],
      week_totals: totals(90, 60, 25),
    },
  ],
};

function makeQuery(
  data: ScheduleHealthResponse | undefined,
  opts: { isLoading?: boolean; isError?: boolean; isSuccess?: boolean } = {},
) {
  return {
    data,
    isLoading: opts.isLoading ?? false,
    isError: opts.isError ?? false,
    isSuccess: opts.isSuccess ?? data !== undefined,
  };
}

/** current(週27) / prev(週26) を isoWeek で振り分けるモック実装を仕込む. */
function setupQueries(current: ReturnType<typeof makeQuery>, prev: ReturnType<typeof makeQuery>) {
  scheduleHealthMock.mockImplementation((params: { isoWeek: number }) =>
    params.isoWeek === 27 ? current : prev,
  );
}

function renderDialog() {
  return render(
    <ScheduleHealthDialog
      open
      onClose={() => {}}
      isoYear={2026}
      isoWeek={27}
      officeId="o1"
      weekLabel="2026-W27"
    />,
  );
}

// ─── テスト ──────────────────────────────────────────────────────────────────

describe('ScheduleHealthDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. サマリタイルの値と前週比 delta の符号色', () => {
    setupQueries(makeQuery(CURRENT), makeQuery(PREV));
    renderDialog();

    const travelTile = screen.getByTestId('schedule-health-tile-travel');
    expect(within(travelTile).getByText('110分')).toBeInTheDocument();
    // 移動: 110 - 90 = +20 → 増加 = warning トーン.
    const travelDelta = screen.getByTestId('schedule-health-tile-travel-delta');
    expect(travelDelta).toHaveTextContent('+20分');
    expect(travelDelta).toHaveClass('text-warning');

    // 隙間: 50 - 60 = -10 → 減少 = success トーン.
    const gapTile = screen.getByTestId('schedule-health-tile-gap');
    expect(within(gapTile).getByText('50分')).toBeInTheDocument();
    const gapDelta = screen.getByTestId('schedule-health-tile-gap-delta');
    expect(gapDelta).toHaveTextContent('−10分');
    expect(gapDelta).toHaveClass('text-success');

    // 距離: 20.0 - 25.0 = -5.0 → 減少 = success.
    const kmTile = screen.getByTestId('schedule-health-tile-km');
    expect(within(kmTile).getByText('20.0km')).toBeInTheDocument();
    expect(screen.getByTestId('schedule-health-tile-km-delta')).toHaveClass('text-success');
  });

  it('2. コース別バーの 1.5 倍ハイライト', () => {
    setupQueries(makeQuery(CURRENT), makeQuery(PREV));
    renderDialog();

    const bars = screen.getAllByTestId('schedule-health-bar');
    // 全曜日集約: A(100) / C(30) / B(10) の 3 本 (移動降順).
    expect(bars).toHaveLength(3);
    // A は平均*1.5 超 → high.
    const aBar = bars.find((b) => within(b).queryByText(/A・田中/));
    expect(aBar).toHaveAttribute('data-high', 'true');
    expect(within(aBar as HTMLElement).getByTestId('icon-triangle-alert')).toBeInTheDocument();
    // B は high ではない.
    const bBar = bars.find((b) => within(b).queryByText(/B・佐藤/));
    expect(bBar).toHaveAttribute('data-high', 'false');
  });

  it('3. 曜日フィルタで表示コースが切り替わる', () => {
    setupQueries(makeQuery(CURRENT), makeQuery(PREV));
    renderDialog();

    // 初期 (全曜日) は 3 本.
    expect(screen.getAllByTestId('schedule-health-bar')).toHaveLength(3);

    // 火曜 (weekday=1) を選択 → C のみ.
    fireEvent.click(screen.getByText('火'));
    const bars = screen.getAllByTestId('schedule-health-bar');
    expect(bars).toHaveLength(1);
    expect(within(bars[0]).getByText(/C・鈴木/)).toBeInTheDocument();
  });

  it('4. 空状態 (offices 空)', () => {
    const empty: ScheduleHealthResponse = { iso_year: 2026, iso_week: 27, offices: [] };
    setupQueries(makeQuery(empty), makeQuery(PREV));
    renderDialog();

    expect(screen.getByTestId('schedule-health-empty')).toHaveTextContent(
      'この週の訪問データがありません',
    );
  });

  it('5. 前週データなし時は delta の代わりにフォールバック表示', () => {
    const prevEmpty: ScheduleHealthResponse = { iso_year: 2026, iso_week: 26, offices: [] };
    setupQueries(makeQuery(CURRENT), makeQuery(prevEmpty));
    renderDialog();

    expect(screen.getByTestId('schedule-health-tile-travel-delta-none')).toHaveTextContent(
      '前週データなし',
    );
    expect(screen.queryByTestId('schedule-health-tile-travel-delta')).not.toBeInTheDocument();
  });

  it('6. ローディング中は Skeleton を表示', () => {
    setupQueries(makeQuery(undefined, { isLoading: true }), makeQuery(undefined));
    renderDialog();
    expect(screen.getByTestId('schedule-health-loading')).toBeInTheDocument();
  });

  it('7. 取得エラー時はエラーメッセージを表示', () => {
    setupQueries(makeQuery(undefined, { isError: true }), makeQuery(undefined));
    renderDialog();
    expect(screen.getByTestId('schedule-health-error')).toBeInTheDocument();
  });
});
