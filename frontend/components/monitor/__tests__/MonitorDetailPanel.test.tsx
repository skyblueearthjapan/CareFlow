/** 詳細パネル: 空 / コース一覧 / visit 詳細 の切替。 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { MonitorDetailPanel } from '../MonitorDetailPanel';
import { makeRow, makeVisit } from './fixtures';

describe('MonitorDetailPanel', () => {
  it('未選択は空状態を表示', () => {
    render(<MonitorDetailPanel visit={null} row={null} onSelectVisit={vi.fn()} />);
    expect(screen.getByTestId('monitor-detail-empty')).toBeInTheDocument();
  });

  it('コースのみ選択時は訪問一覧、stop クリックで onSelectVisit', () => {
    const v = makeVisit({ patient_name: '本田 武' });
    const row = makeRow({ visits: [v] });
    const onSelectVisit = vi.fn();
    render(<MonitorDetailPanel visit={null} row={row} onSelectVisit={onSelectVisit} />);
    expect(screen.getByTestId('monitor-detail-course')).toBeInTheDocument();
    fireEvent.click(screen.getByText(/本田 武/));
    expect(onSelectVisit).toHaveBeenCalledWith(v.visit_id);
  });

  it('visit 選択時は予定/到着/滞在を表示', () => {
    const v = makeVisit({
      patient_name: '山田 花子',
      phase: 'done',
      alert_level: 'none',
      stay_minutes: 50,
      arrival_delay_min: 5,
      arrival: {
        kind: 'arrival',
        scanned_at: '2026-06-30T00:05:00Z',
        match_status: 'match',
        distance_m: 12,
        accuracy_m: 8,
        is_override: false,
      },
      departure: {
        kind: 'departure',
        scanned_at: '2026-06-30T00:55:00Z',
        match_status: 'match',
        is_override: false,
      },
    });
    const row = makeRow({ visits: [v] });
    render(<MonitorDetailPanel visit={v} row={row} onSelectVisit={vi.fn()} />);
    expect(screen.getByTestId('monitor-detail-visit')).toBeInTheDocument();
    expect(screen.getByText('滞在時間')).toBeInTheDocument();
    expect(screen.getByText('50分')).toBeInTheDocument();
  });

  it('未訪問は即連絡ボックスを表示', () => {
    const v = makeVisit({ phase: 'missing', alert_level: 'missing', arrival: null });
    const row = makeRow({ visits: [v] });
    render(<MonitorDetailPanel visit={v} row={row} onSelectVisit={vi.fn()} />);
    expect(screen.getByTestId('monitor-callbox')).toBeInTheDocument();
  });
});
