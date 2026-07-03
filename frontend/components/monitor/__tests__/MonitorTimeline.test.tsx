/** タイムライン描画テスト (患者名 / 実績バーの状態色 / 行クリック)。 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { MonitorTimeline } from '../MonitorTimeline';
import { makeRow, makeVisit } from './fixtures';

describe('MonitorTimeline', () => {
  it('予定の患者名を描画する', () => {
    const v = makeVisit({ patient_name: '佐藤 一郎' });
    const row = makeRow({ visits: [v] });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={13 * 60 + 30}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    expect(screen.getAllByText('佐藤 一郎').length).toBeGreaterThan(0);
  });

  it('到着済 mismatch の実績バーは data-status=mismatch', () => {
    const v = makeVisit({
      phase: 'done',
      alert_level: 'mismatch',
      arrival: {
        kind: 'arrival',
        scanned_at: '2026-06-30T00:05:00Z',
        match_status: 'mismatch',
        distance_m: 360,
        is_override: false,
      },
      departure: {
        kind: 'departure',
        scanned_at: '2026-06-30T00:55:00Z',
        match_status: 'mismatch',
        is_override: false,
      },
    });
    const row = makeRow({ visits: [v] });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={13 * 60 + 30}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    const bar = screen.getByTestId(`monitor-bar-actual-${v.visit_id}`);
    expect(bar.getAttribute('data-status')).toBe('mismatch');
  });

  it('未訪問は実績バーに「未訪問」を表示する', () => {
    const v = makeVisit({ phase: 'missing', alert_level: 'missing', arrival: null });
    const row = makeRow({ visits: [v] });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={13 * 60 + 30}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    const bar = screen.getByTestId(`monitor-bar-actual-${v.visit_id}`);
    expect(bar.getAttribute('data-status')).toBe('missing');
    expect(bar.textContent).toContain('未訪問');
  });

  it('行クリックで onSelectRow、バークリックで onSelectVisit', () => {
    const v = makeVisit();
    const row = makeRow({ staff_id: 'staff-x', visits: [v] });
    const onSelectRow = vi.fn();
    const onSelectVisit = vi.fn();
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={onSelectRow}
        onSelectVisit={onSelectVisit}
      />,
    );
    fireEvent.click(screen.getByTestId('monitor-row-0'));
    expect(onSelectRow).toHaveBeenCalledWith('staff-x');
    fireEvent.click(screen.getByTestId(`monitor-bar-plan-${v.visit_id}`));
    expect(onSelectVisit).toHaveBeenCalledWith(v.visit_id);
  });

  it('同時刻 2 件で両方の患者名が可視・data-lane が 0/1 に振り分けられる', () => {
    const v1 = makeVisit({ patient_name: '田中 一郎', start_time: '09:00', end_time: '10:00' });
    const v2 = makeVisit({ patient_name: '鈴木 花子', start_time: '09:00', end_time: '10:00' });
    const row = makeRow({ visits: [v1, v2] });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={13 * 60 + 30}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    // 両方の患者名が描画されていること。
    expect(screen.getAllByText('田中 一郎').length).toBeGreaterThan(0);
    expect(screen.getAllByText('鈴木 花子').length).toBeGreaterThan(0);
    // 予定バーの data-lane が 0/1 に分かれていること。
    const bar1 = screen.getByTestId(`monitor-bar-plan-${v1.visit_id}`);
    const bar2 = screen.getByTestId(`monitor-bar-plan-${v2.visit_id}`);
    const lanes = new Set([bar1.getAttribute('data-lane'), bar2.getAttribute('data-lane')]);
    expect(lanes).toEqual(new Set(['0', '1']));
  });

  it('重なりなし (連続) は 1 レーン → data-lane=0', () => {
    const v1 = makeVisit({ patient_name: '田中 一郎', start_time: '09:00', end_time: '10:00' });
    const v2 = makeVisit({ patient_name: '鈴木 花子', start_time: '10:00', end_time: '11:00' });
    const row = makeRow({ visits: [v1, v2] });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    const bar1 = screen.getByTestId(`monitor-bar-plan-${v1.visit_id}`);
    const bar2 = screen.getByTestId(`monitor-bar-plan-${v2.visit_id}`);
    expect(bar1.getAttribute('data-lane')).toBe('0');
    expect(bar2.getAttribute('data-lane')).toBe('0');
  });

  it('pair_waiting の visit は「ペア待ち」バッジを表示し、未訪問バーは出さない', () => {
    const v = makeVisit({
      phase: 'awaiting',
      alert_level: 'none',
      pair_waiting: true,
      arrival: null,
    });
    const row = makeRow({ visits: [v] });
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={13 * 60 + 30}
        onSelectRow={vi.fn()}
        onSelectVisit={vi.fn()}
      />,
    );
    const badge = screen.getByTestId(`monitor-pair-waiting-${v.visit_id}`);
    expect(badge.textContent).toContain('ペア待ち');
    // 未訪問バーは出ない (誤警告にしない)。
    expect(screen.queryByTestId(`monitor-bar-actual-${v.visit_id}`)).toBeNull();
  });

  it('pair_waiting バッジのクリックで onSelectVisit', () => {
    const v = makeVisit({ phase: 'awaiting', alert_level: 'none', pair_waiting: true });
    const row = makeRow({ visits: [v] });
    const onSelectVisit = vi.fn();
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={13 * 60 + 30}
        onSelectRow={vi.fn()}
        onSelectVisit={onSelectVisit}
      />,
    );
    fireEvent.click(screen.getByTestId(`monitor-pair-waiting-${v.visit_id}`));
    expect(onSelectVisit).toHaveBeenCalledWith(v.visit_id);
  });

  it('担当未設定の行もクリックで選択できる (rowKey=unassigned-コース)', () => {
    const v = makeVisit();
    const row = makeRow({ staff_id: null, staff_name: null, course_label: 'Aコース', visits: [v] });
    const onSelectRow = vi.fn();
    render(
      <MonitorTimeline
        rows={[row]}
        selectedRowKey={null}
        selectedVisitId={null}
        nowMinutes={-1}
        onSelectRow={onSelectRow}
        onSelectVisit={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('monitor-row-0'));
    expect(onSelectRow).toHaveBeenCalledWith('unassigned-Aコース');
  });
});
