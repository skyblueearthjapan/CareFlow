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

  // --- 代行 / 予定外 (qr-open-checkin-design.md §6) ---

  it('代行 visit は「予定の担当 / 実際の訪問」を並記する (実際=代行者)', () => {
    // 代行 B のあとに担当 A が打ち直しても、「実際の訪問」は代行者 B を出す。
    const v = makeVisit({
      staff_name: '担当 A',
      actual_staff_name: '担当 A',
      substitute_staff_name: '代行 B',
      is_substitute: true,
      alert_level: 'review',
    });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    const box = screen.getByTestId('monitor-detail-substitute');
    expect(box.textContent).toContain('予定の担当: 担当 A');
    expect(box.textContent).toContain('実際の訪問: 代行 B');
    expect(screen.queryByTestId('monitor-detail-unplanned')).toBeNull();
  });

  it('代行者名が無い応答では名前を併記しない', () => {
    const v = makeVisit({
      staff_name: '担当 A',
      actual_staff_name: '担当 A',
      substitute_staff_name: null,
      is_substitute: true,
      alert_level: 'review',
    });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    const box = screen.getByTestId('monitor-detail-substitute');
    expect(box.textContent).toContain('予定の担当: 担当 A');
    expect(box.textContent).not.toContain('実際の訪問:');
  });

  it('予定外かつ代行なら両方のブロックを出す', () => {
    const v = makeVisit({
      staff_name: '担当 A',
      actual_staff_name: '代行 B',
      substitute_staff_name: '代行 B',
      is_substitute: true,
      is_unplanned: true,
      alert_level: 'review',
    });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    expect(screen.getByTestId('monitor-detail-unplanned')).toBeInTheDocument();
    expect(screen.getByTestId('monitor-detail-substitute').textContent).toContain(
      '実際の訪問: 代行 B',
    );
  });

  it('予定外 visit は「予定外訪問」を明示する', () => {
    const v = makeVisit({
      patient_name: '飛込 花子',
      actual_staff_name: '実績 次郎',
      is_unplanned: true,
      alert_level: 'review',
    });
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    const box = screen.getByTestId('monitor-detail-unplanned');
    expect(box.textContent).toContain('予定外訪問');
    expect(box.textContent).toContain('実際の訪問: 実績 次郎');
  });

  it('通常 visit は代行/予定外の枠を出さない', () => {
    const v = makeVisit();
    render(<MonitorDetailPanel visit={v} row={makeRow({ visits: [v] })} onSelectVisit={vi.fn()} />);
    expect(screen.queryByTestId('monitor-detail-substitute')).toBeNull();
    expect(screen.queryByTestId('monitor-detail-unplanned')).toBeNull();
  });
});
