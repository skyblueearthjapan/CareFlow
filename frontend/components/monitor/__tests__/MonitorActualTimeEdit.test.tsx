/**
 * 訪問モニターから実績の時刻を合わせる（設計 `pc-actual-time-edit-design-2026-10-06.md`
 * D1 / D2 / Q4）。
 *
 * * 詳細パネルの「実績の時刻を合わせる」枠は管理者だけ（`canAdjust` かつ BE の
 *   `adjust_allowed`）。打刻なしの訪問は「到着・退出を手で入れる」（`manual_arrival_allowed`）。
 * * 前日以前で退出が無い訪問は、滞在を数えず「退出未記録」。要対応トレイに
 *   「退出未記録 N件」を出し、押すと一覧、1 件押すとその訪問を選ぶ。
 * * 手で入れた到着はバーに「手入力」の印。
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

vi.mock('@/lib/queries/visit-recordings', () => ({
  useVisitRecordings: () => ({ data: { items: [], total: 0 }, isLoading: false }),
}));
vi.mock('@/lib/queries/visit-history', () => ({
  useAdjustVisitActualTime: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useResetVisitActualTime: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import type { MonitorCheckin } from '@/lib/schemas/monitor';

import { MonitorAlertTray } from '../MonitorAlertTray';
import { MonitorDetailPanel } from '../MonitorDetailPanel';
import { MonitorTimeline } from '../MonitorTimeline';
import { makeRow, makeVisit } from './fixtures';

const arrivalCheckin = (iso: string): MonitorCheckin => ({
  kind: 'arrival',
  scanned_at: iso,
  match_status: 'match',
  is_override: false,
});

/** 前日以前で到着 14:05・退出なし（BE が departure_missing を立てた訪問）。 */
function pastNoDeparture() {
  return makeVisit({
    patient_name: '中村 光子',
    start_time: '14:00',
    end_time: '14:30',
    phase: 'inprogress',
    alert_level: 'review',
    arrival: arrivalCheckin('2026-10-03T05:05:00Z'),
    arrival_at: '2026-10-03T05:05:00Z',
    arrival_read_at: '2026-10-03T05:05:00Z',
    departure_missing: true,
    stay_minutes: null,
    adjust_allowed: true,
    manual_arrival_allowed: false,
  });
}

describe('詳細パネル — 実績の時刻を合わせる枠', () => {
  it('管理者 (canAdjust) で adjust_allowed の訪問にだけ枠を出す', () => {
    const v = pastNoDeparture();
    const { rerender } = render(
      <MonitorDetailPanel
        visit={v}
        row={makeRow({ visits: [v] })}
        onSelectVisit={vi.fn()}
        canAdjust
      />,
    );
    expect(screen.getByTestId('monitor-adjust-box')).toBeInTheDocument();
    // 退出の読み取りが無い = 「退出を HH:MM で記録する」・候補は 2 つ。
    expect(screen.getByTestId('monitor-adjust-save-departure')).toHaveTextContent(
      '退出を 14:35 で記録する',
    );
    expect(screen.getAllByTestId(/^monitor-adjust-cand-/)).toHaveLength(2);

    // 管理者以外 (canAdjust なし) には出さない。
    rerender(
      <MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />,
    );
    expect(screen.queryByTestId('monitor-adjust-box')).not.toBeInTheDocument();

    // BE が合わせられないとした訪問にも出さない。
    rerender(
      <MonitorDetailPanel
        visit={{ ...v, adjust_allowed: false }}
        row={makeRow({ visits: [v] })}
        onSelectVisit={vi.fn()}
        canAdjust
      />,
    );
    expect(screen.queryByTestId('monitor-adjust-box')).not.toBeInTheDocument();
  });

  it('前日以前で退出が無い訪問は「退出未記録」と出し、滞在を数えない', () => {
    const v = pastNoDeparture();
    render(
      <MonitorDetailPanel
        visit={v}
        row={makeRow({ visits: [v] })}
        onSelectVisit={vi.fn()}
        canAdjust
      />,
    );
    expect(screen.getByTestId('monitor-departure-missing')).toHaveTextContent('退出未記録');
    const detail = screen.getByTestId('monitor-detail-visit');
    expect(detail).toHaveTextContent('滞在時間退出未記録');
    expect(detail).not.toHaveTextContent('（滞在中）');
  });

  it('当日の訪問中は従来どおり「（滞在中）」と滞在分', () => {
    const v = makeVisit({
      phase: 'inprogress',
      arrival: arrivalCheckin('2026-10-06T04:13:00Z'),
      arrival_at: '2026-10-06T04:13:00Z',
      stay_minutes: 117,
      departure_missing: false,
    });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    expect(screen.queryByTestId('monitor-departure-missing')).not.toBeInTheDocument();
    expect(screen.getByTestId('monitor-detail-visit')).toHaveTextContent('（滞在中）');
    expect(screen.getByTestId('monitor-detail-visit')).toHaveTextContent('117分');
  });

  it('打刻なしの訪問は、管理者だけ「到着・退出を手で入れる」', () => {
    const v = makeVisit({
      phase: 'missing',
      alert_level: 'missing',
      start_time: '10:00',
      end_time: '10:45',
      adjust_allowed: false,
      manual_arrival_allowed: true,
    });
    const { rerender } = render(
      <MonitorDetailPanel
        visit={v}
        row={makeRow({ visits: [v] })}
        onSelectVisit={vi.fn()}
        canAdjust
      />,
    );
    const box = screen.getByTestId('monitor-adjust-manual-box');
    // 入力は空から・予定は横に出す (PO 決定 2026-10-07)。
    expect(within(box).getByLabelText('到着の時刻')).toHaveValue('');
    expect(box).toHaveTextContent('予定 10:00');

    rerender(
      <MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />,
    );
    expect(screen.queryByTestId('monitor-adjust-manual-box')).not.toBeInTheDocument();
  });
});

describe('要対応トレイ — 退出未記録', () => {
  it('「退出未記録 N件」を出し、押すと一覧、1 件押すとその訪問を選ぶ', () => {
    const a = pastNoDeparture();
    const b = { ...pastNoDeparture(), visit_id: 'v-b', patient_name: '森 義雄' };
    const late = makeVisit({
      alert_level: 'review',
      patient_name: '遅延 太郎',
      arrival_delay_min: 20,
    });
    const onSelect = vi.fn();
    render(
      <MonitorAlertTray
        rows={[makeRow({ visits: [a, b, late] })]}
        selectedVisitId={null}
        onSelectVisit={onSelect}
      />,
    );
    const btn = screen.getByTestId('monitor-alert-departure-missing');
    expect(btn).toHaveTextContent('退出未記録 2件');
    // 札は「退出未記録」。
    expect(screen.getByTestId(`monitor-alert-${a.visit_id}`)).toHaveTextContent('退出未記録');

    fireEvent.click(btn);
    const pop = screen.getByTestId('monitor-alert-popover');
    expect(pop).toHaveTextContent('退出未記録 2件');
    expect(pop).toHaveTextContent('中村 光子');
    expect(pop).not.toHaveTextContent('遅延 太郎');
    fireEvent.click(within(pop).getByText('森 義雄'));
    expect(onSelect).toHaveBeenCalledWith('v-b');
  });
});

describe('タイムライン — 退出未記録と手入力の印', () => {
  it('過去の日の退出未記録は今まで伸ばさず「退出未記録」、手で入れた到着は「手入力」', () => {
    const missing = pastNoDeparture();
    const manual = makeVisit({
      patient_name: '伊東 誠',
      start_time: '10:00',
      end_time: '10:45',
      phase: 'done',
      arrival_at: '2026-10-05T01:02:00Z',
      departure_at: '2026-10-05T01:47:00Z',
      arrival_adjusted: true,
      arrival_manual: true,
      departure_adjusted: true,
      departure_manual: true,
    });
    render(
      <MonitorTimeline
        rows={[makeRow({ visits: [missing, manual] })]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    const bar = screen.getByTestId(`monitor-bar-actual-${missing.visit_id}`);
    expect(bar).toHaveTextContent('退出未記録');
    expect(bar).toHaveAttribute('data-departure-missing', 'true');
    expect(screen.getByTestId(`monitor-bar-adjusted-${manual.visit_id}`)).toHaveTextContent(
      '手入力',
    );
  });
});

describe('未訪問の記録の後に到着を手で入れた訪問', () => {
  const v = () =>
    makeVisit({
      patient_name: '伊東 誠',
      start_time: '10:00',
      end_time: '10:45',
      phase: 'done',
      no_show: {
        kind: 'no_show',
        scanned_at: '2026-10-05T01:20:00Z',
        match_status: 'match',
        reason: '不在でした',
        is_override: false,
      },
      reason: null,
      arrival_at: '2026-10-05T01:30:00Z',
      departure_at: '2026-10-05T02:10:00Z',
      arrival_adjusted: true,
      arrival_manual: true,
      departure_adjusted: true,
      departure_manual: true,
      adjust_allowed: true,
    });

  it('詳細は未訪問の記録を履歴として出し、枠にも注意書き', () => {
    const visit = v();
    render(
      <MonitorDetailPanel
        visit={visit}
        row={makeRow({ visits: [visit] })}
        onSelectVisit={vi.fn()}
        canAdjust
      />,
    );
    expect(screen.getByTestId('monitor-detail-visit')).toHaveTextContent(
      '未訪問の記録（履歴）あり（理由: 不在でした）',
    );
    expect(screen.queryByTestId('monitor-callbox')).not.toBeInTheDocument();
    expect(screen.getByTestId('monitor-adjust-no-show')).toHaveTextContent('訪問した扱いです');
  });

  it('バーは実績の時刻を出す (未訪問として隠さない)', () => {
    const visit = v();
    render(
      <MonitorTimeline
        rows={[makeRow({ visits: [visit] })]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    expect(screen.getByTestId(`monitor-bar-actual-time-${visit.visit_id}`)).toHaveTextContent(
      '10:30',
    );
  });
});
