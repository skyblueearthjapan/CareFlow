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

  it('未確認は「確認済みにする」→ 理由入力 → 確定で onReview を呼ぶ', () => {
    const v = makeVisit({ phase: 'missing', alert_level: 'missing', arrival: null });
    const row = makeRow({ visits: [v] });
    const onReview = vi.fn();
    render(<MonitorDetailPanel visit={v} row={row} onSelectVisit={vi.fn()} onReview={onReview} />);
    fireEvent.click(screen.getByTestId('monitor-review-button'));
    fireEvent.change(screen.getByTestId('monitor-review-comment'), {
      target: { value: '電話で確認済み' },
    });
    fireEvent.click(screen.getByTestId('monitor-review-submit'));
    expect(onReview).toHaveBeenCalledWith(v.visit_id, '電話で確認済み');
  });

  it('確認済みは確認者/理由を表示し、取り消しで onUnreview を呼ぶ', () => {
    const v = makeVisit({
      phase: 'missing',
      alert_level: 'none',
      arrival: null,
      reviewed: true,
      reviewed_by_name: '管理 太郎',
      reviewed_at: '2026-06-30T01:00:00Z',
      review_comment: '在宅を電話確認',
    });
    const row = makeRow({ visits: [v] });
    const onUnreview = vi.fn();
    render(
      <MonitorDetailPanel
        visit={v}
        row={row}
        onSelectVisit={vi.fn()}
        onReview={vi.fn()}
        onUnreview={onUnreview}
      />,
    );
    expect(screen.getByTestId('monitor-review-done')).toBeInTheDocument();
    expect(screen.getByText('管理 太郎', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('在宅を電話確認')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('monitor-review-undo'));
    expect(onUnreview).toHaveBeenCalledWith(v.visit_id);
  });
});
