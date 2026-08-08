/**
 * 週のピン (青ピン) の盤面挙動 — PO 決定 2026-08-08.
 * 仕様: docs/plans/pin-and-movability-spec.md
 *
 * 赤ピン (型のピン) は型と一致する訪問にしか刺せないため、型とズレた訪問を
 * 今の位置で守る手段が無かった。青ピンはその穴を埋める。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { CourseGridVisit } from '@/components/schedule/v2/courseGrid';
import {
  TimelineDayBoard,
  type TimelineCourseColumn,
} from '@/components/schedule/timeline/TimelineDayBoard';

function visit(over: Partial<CourseGridVisit> & { id: string }): CourseGridVisit {
  return {
    patient_id: `p-${over.id}`,
    patient_name: `患者${over.id}`,
    patient_address: null,
    patient_requires_multiple_staff: false,
    patient_sex_restriction_label: null,
    required_staff_count: 1,
    start_slot: '09:30',
    start_time: '10:25',
    end_time: '11:00',
    ...over,
  } as CourseGridVisit;
}

function column(visits: CourseGridVisit[]): TimelineCourseColumn {
  return {
    template: { id: 't1', office_id: 'o1', label: 'A' } as TimelineCourseColumn['template'],
    course: { id: 'c1', assigned_staff_id: 's1' } as TimelineCourseColumn['course'],
    officeName: '稲毛',
    visits,
    assignedStaff: {
      id: 's1',
      name: '田中 一郎',
      sex: 'male',
    } as TimelineCourseColumn['assignedStaff'],
    freeGaps: [],
    capacity: { filled: visits.length, max: 6 },
    staffEvents: [],
    staffOptions: [],
    key: 'col-1',
  } as TimelineCourseColumn;
}

function renderBoard(v: CourseGridVisit, onToggleWeekPin?: (id: string, next: boolean) => void) {
  return render(<TimelineDayBoard columns={[column([v])]} onToggleWeekPin={onToggleWeekPin} />);
}

describe('週のピン (青ピン)', () => {
  it('未固定の訪問はトグルが「未ピン」状態で出る', () => {
    renderBoard(visit({ id: 'v1', source: 'auto' }), vi.fn());
    const btn = screen.getByTestId('tl-week-pin-toggle-v1');
    expect(btn).toHaveAttribute('data-week-pinned', 'false');
    expect(btn).toHaveAttribute('aria-pressed', 'false');
  });

  it('型とズレた訪問 (source=auto) でも今週固定できる — 赤ピンとの決定的な違い', async () => {
    const onToggle = vi.fn();
    // 型は 13:00 / 実配置は 10:25 = 赤ピンは刺せない状態。
    renderBoard(visit({ id: 'v2', source: 'auto', master_start_time: '13:00' }), onToggle);

    await userEvent.click(screen.getByTestId('tl-week-pin-toggle-v2'));
    expect(onToggle).toHaveBeenCalledWith('v2', true);
  });

  it('今週固定済みの訪問は青い画鋲が立ち、トグルで解除できる', async () => {
    const onToggle = vi.fn();
    renderBoard(visit({ id: 'v3', source: 'manual_week' }), onToggle);

    const btn = screen.getByTestId('tl-week-pin-toggle-v3');
    expect(btn).toHaveAttribute('data-week-pinned', 'true');
    expect(document.querySelector('[data-icon="corner-week-push-pin"]')).toBeInTheDocument();

    await userEvent.click(btn);
    expect(onToggle).toHaveBeenCalledWith('v3', false);
  });

  it('赤ピン (型のピン) が刺さっている訪問には青ピンのトグルを出さない', () => {
    // 型のピンが既に不可侵を担保しているため、二重の固定は意味が無い。
    renderBoard(visit({ id: 'v4', source: 'auto', is_pinned: true }), vi.fn());
    expect(screen.queryByTestId('tl-week-pin-toggle-v4')).not.toBeInTheDocument();
    // 赤い画鋲のみが立つ。
    expect(document.querySelector('[data-icon="corner-push-pin"]')).toBeInTheDocument();
    expect(document.querySelector('[data-icon="corner-week-push-pin"]')).not.toBeInTheDocument();
  });

  it('ハンドラ未指定 (閲覧のみ) ではトグルを描画しない', () => {
    renderBoard(visit({ id: 'v5', source: 'manual_week' }));
    expect(screen.queryByTestId('tl-week-pin-toggle-v5')).not.toBeInTheDocument();
    // 表示 (青い画鋲) は残る — 状態は読めるべきなので。
    expect(document.querySelector('[data-icon="corner-week-push-pin"]')).toBeInTheDocument();
  });
});
