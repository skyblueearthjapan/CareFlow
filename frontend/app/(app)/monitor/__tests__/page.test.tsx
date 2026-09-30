/** /monitor ページ: RBAC リダイレクト + 基本描画 + 行の下に開くパネル (行 = 職員)。
 *
 * Leaflet は jsdom 非対応のため MonitorMap (dynamic import 本体) をモックする。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

const replace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace }),
}));

const useSession = vi.fn();
vi.mock('next-auth/react', () => ({
  useSession: () => useSession(),
}));

const useMonitor = vi.fn();
const useNearbyPatients = vi.fn(() => ({ data: { items: [] } }));
const mutate = vi.fn();
const useReviewVisit = vi.fn(() => ({ mutate, isPending: false }));
const useUnreviewVisit = vi.fn(() => ({ mutate, isPending: false }));
vi.mock('@/lib/queries/monitor', () => ({
  useMonitor: (...a: unknown[]) => useMonitor(...a),
  useNearbyPatients: (...a: unknown[]) => useNearbyPatients(...a),
  useReviewVisit: () => useReviewVisit(),
  useUnreviewVisit: () => useUnreviewVisit(),
}));

// Leaflet を引き込む地図はモック。
vi.mock('@/components/monitor/MonitorMap', () => ({
  MonitorMap: () => <div data-testid="mock-map" />,
}));

// M-4a/b: カード視覚言語用の FE join フック (QueryClient 不要の noop)。
vi.mock('@/lib/queries/patients', () => ({
  usePatients: () => ({ data: { items: [] }, isLoading: false }),
}));
vi.mock('@/lib/queries/staff', () => ({
  useStaffList: () => ({ data: [], isLoading: false }),
}));
// 「🎙 記録を見る」(詳細パネル) が引く音声記録。ここでは記録なしに固定する。
vi.mock('@/lib/queries/visit-recordings', () => ({
  useVisitRecordings: () => ({ data: { items: [], total: 0 }, isLoading: false }),
  useVisitRecording: () => ({ data: null, isLoading: false }),
  useUpdateRecording: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRetryRecording: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteRecording: () => ({ mutateAsync: vi.fn(), isPending: false }),
  recordingAudioUrl: (id: string) => `/api/v1/visit-recordings/${id}/audio`,
}));
vi.mock('@/lib/queries/staff-events', () => ({
  useWeekStaffEvents: () => ({ data: [], isLoading: false }),
  buildStaffEventsMap: () => new Map(),
}));

import { makeRow, makeVisit } from '@/components/monitor/__tests__/fixtures';
import type { MonitorResponse, MonitorStaffRow } from '@/lib/schemas/monitor';

import MonitorPage from '../page';

beforeEach(() => {
  replace.mockClear();
  useMonitor.mockReturnValue({ data: null, isLoading: false, isError: false });
});

const THRESHOLDS = {
  match_m: 100,
  review_m: 300,
  accuracy_m: 50,
  no_show_grace_min: 20,
  late_min: 15,
  max_inprogress_min: 240,
};

function renderWithRows(
  rows: MonitorStaffRow[],
  offices: MonitorResponse['offices'] = [],
  officeOrder?: string[],
) {
  useSession.mockReturnValue({ data: { user: { role: 'admin' } }, status: 'authenticated' });
  useMonitor.mockReturnValue({
    data: {
      date: '2026-06-30',
      now: '2026-06-30T04:30:00Z',
      thresholds: THRESHOLDS,
      offices,
      ...(officeOrder ? { office_order: officeOrder } : {}),
      staff: rows,
    },
    isLoading: false,
    isError: false,
  });
  return render(<MonitorPage />);
}

// ─── 行 = 職員・行の下に開くパネル (monitor-staff-rows-design-2026-09-30.md §4) ───

describe('MonitorPage — 行の下に開くパネル', () => {
  const INAGE = '00000000-0000-0000-0000-00000000aaaa';
  const TSUGA = '00000000-0000-0000-0000-00000000bbbb';
  const offices = [
    { id: INAGE, name: '稲毛' },
    { id: TSUGA, name: '都賀' },
  ];

  function threeRows() {
    const a = makeRow({
      staff_name: '一番 花子',
      office_id: INAGE,
      office_name: '稲毛',
      visits: [makeVisit({ patient_name: '患者 A', course_office_id: INAGE })],
    });
    const b = makeRow({
      staff_name: '二番 太郎',
      office_id: INAGE,
      office_name: '稲毛',
      visits: [
        makeVisit({
          patient_name: '患者 B',
          course_office_id: TSUGA,
          phase: 'missing',
          alert_level: 'missing',
        }),
      ],
    });
    const c = makeRow({
      staff_name: '三番 次郎',
      office_id: TSUGA,
      office_name: '都賀',
      visits: [],
      day_override: { kind: 'off', start_time: null, end_time: null, reason: null },
    });
    return [a, b, c];
  }

  it('右の固定パネル (aside) は無く、行を押すとその行の下にだけ開く', () => {
    renderWithRows(threeRows(), offices);
    expect(document.querySelector('aside')).toBeNull();
    expect(screen.queryByTestId('monitor-row-panel')).toBeNull();

    fireEvent.click(screen.getByTestId('monitor-row-0'));
    const panel = screen.getByTestId('monitor-row-panel');
    expect(screen.getByTestId('monitor-row-0').nextElementSibling).toBe(panel);
    expect(within(panel).getByTestId('mock-map')).toBeInTheDocument();
    expect(within(panel).getByTestId('monitor-detail-route').textContent).toContain('患者 A');

    // 別の行を押すと、そちらに開き直す (開くのは 1 つだけ)。
    fireEvent.click(screen.getByTestId('monitor-row-1'));
    expect(screen.getAllByTestId('monitor-row-panel')).toHaveLength(1);
    expect(screen.getByTestId('monitor-row-1').nextElementSibling).toBe(
      screen.getByTestId('monitor-row-panel'),
    );
  });

  it('✕・Esc・同じ行をもう一度押すことで閉じる', () => {
    renderWithRows(threeRows(), offices);
    fireEvent.click(screen.getByTestId('monitor-row-0'));
    fireEvent.click(screen.getByTestId('monitor-row-panel-close'));
    expect(screen.queryByTestId('monitor-row-panel')).toBeNull();

    fireEvent.click(screen.getByTestId('monitor-row-0'));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByTestId('monitor-row-panel')).toBeNull();

    fireEvent.click(screen.getByTestId('monitor-row-0'));
    fireEvent.click(screen.getByTestId('monitor-row-0'));
    expect(screen.queryByTestId('monitor-row-panel')).toBeNull();
  });

  it('閉じるボタンは 44px 角', () => {
    renderWithRows(threeRows(), offices);
    fireEvent.click(screen.getByTestId('monitor-row-0'));
    const close = screen.getByTestId('monitor-row-panel-close');
    expect(close.className).toContain('h-11');
    expect(close.className).toContain('min-w-11');
  });

  it('訪問のカードを押すと、その行のパネルに訪問の詳細を並べる', () => {
    const rows = threeRows();
    renderWithRows(rows, offices);
    const v = rows[0]!.visits[0]!;
    fireEvent.click(screen.getByTestId(`monitor-bar-plan-${v.visit_id}`));
    const panel = screen.getByTestId('monitor-row-panel');
    expect(screen.getByTestId('monitor-row-0').nextElementSibling).toBe(panel);
    expect(within(panel).getByTestId('monitor-row-panel-detail')).toBeInTheDocument();
    expect(within(panel).getByTestId('monitor-detail-visit').textContent).toContain('患者 A');
  });

  it('要対応トレイから訪問を選ぶと、その訪問の行を開く', () => {
    const rows = threeRows();
    renderWithRows(rows, offices);
    const missing = rows[1]!.visits[0]!;
    fireEvent.click(screen.getByTestId(`monitor-alert-${missing.visit_id}`));
    const panel = screen.getByTestId('monitor-row-panel');
    expect(screen.getByTestId('monitor-row-1').nextElementSibling).toBe(panel);
    expect(within(panel).getByTestId('monitor-callbox')).toBeInTheDocument();
  });

  it('拠点チップ: その拠点の訪問を持つ人 (訪問の無い人は所属) を出し、集計はその拠点の訪問だけ', () => {
    renderWithRows(threeRows(), offices);
    // 全拠点: 3 行 (休みだけの人も出る)。
    expect(screen.getAllByTestId(/^monitor-row-\d+$/)).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: '都賀' }));
    // 都賀: 都賀の訪問を持つ稲毛所属の二番 + 都賀所属で休みの三番。
    const names = screen.getAllByTestId(/^monitor-row-\d+$/).map((el) => el.textContent ?? '');
    expect(names).toHaveLength(2);
    expect(names[0]).toContain('二番 太郎');
    expect(names[1]).toContain('三番 次郎');
    // 休みの帯が出る。
    expect(screen.getAllByTestId(/^monitor-offduty-/).length).toBeGreaterThan(0);
  });

  it('日付を変えると、開いていたパネルを閉じる', () => {
    renderWithRows(threeRows(), offices);
    fireEvent.click(screen.getByTestId('monitor-row-0'));
    expect(screen.getByTestId('monitor-row-panel')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '翌日' }));
    expect(screen.queryByTestId('monitor-row-panel')).toBeNull();
    fireEvent.click(screen.getByTestId('monitor-row-0'));
    fireEvent.click(screen.getByRole('button', { name: '前日' }));
    expect(screen.queryByTestId('monitor-row-panel')).toBeNull();
  });

  it('札の色はその日の拠点の並びではなく拠点マスタの順 (office_order) で決める', () => {
    // 都賀しか出ない日: offices は都賀だけ。マスタ順では都賀は 2 番目 = 普段の都賀の色。
    const row = makeRow({
      staff_name: '都賀 だけ',
      office_id: TSUGA,
      office_name: '都賀',
      course_tags: [{ label: '都A', course_id: 'c-t', office_id: TSUGA, office_name: '都賀' }],
      visits: [makeVisit({ course_office_id: TSUGA })],
    });
    renderWithRows([row], [{ id: TSUGA, name: '都賀' }], [INAGE, TSUGA]);
    const tag = within(screen.getByTestId(/^monitor-row-tags-/)).getByText('都A');
    expect(tag.getAttribute('style')).toContain('var(--warning-bg)');
  });

  it('スクロール領域はブラウザの scroll anchoring を切る (開け閉めの位置合わせは手書きの補正を正とする)', () => {
    renderWithRows(threeRows(), offices);
    expect(screen.getByTestId('monitor-timeline').parentElement?.className).toContain(
      '[overflow-anchor:none]',
    );
  });
});

describe('MonitorPage RBAC', () => {
  // RB (PO決定 2026-07-08): PC版は全ロール同一表示。staff もモニターを閲覧できる
  // (BE GET も staff 許可済み。確認済み等の書込みは admin/manager のまま)。
  it('staff ロールもモニター本体を閲覧できる (リダイレクトしない)', () => {
    useSession.mockReturnValue({ data: { user: { role: 'staff' } }, status: 'authenticated' });
    useMonitor.mockReturnValue({
      data: {
        date: '2026-06-30',
        now: '2026-06-30T04:30:00Z',
        thresholds: {
          match_m: 100,
          review_m: 300,
          accuracy_m: 50,
          no_show_grace_min: 20,
          late_min: 15,
          max_inprogress_min: 240,
        },
        offices: [],
        staff: [],
      },
      isLoading: false,
      isError: false,
    });
    render(<MonitorPage />);
    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByText('訪問モニター')).toBeInTheDocument();
  });

  it('manager ロールはモニター本体を描画する', () => {
    useSession.mockReturnValue({ data: { user: { role: 'manager' } }, status: 'authenticated' });
    useMonitor.mockReturnValue({
      data: {
        date: '2026-06-30',
        now: '2026-06-30T04:30:00Z',
        thresholds: {
          match_m: 100,
          review_m: 300,
          accuracy_m: 50,
          no_show_grace_min: 20,
          late_min: 15,
          max_inprogress_min: 240,
        },
        offices: [],
        staff: [],
      },
      isLoading: false,
      isError: false,
    });
    render(<MonitorPage />);
    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByText('訪問モニター')).toBeInTheDocument();
  });
});
