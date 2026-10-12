/**
 * 青ピン廃止 (BP-1 / PO 2026-10-12): 盤面に青のトグル・青の画鋲は出ない。
 * 「今週のみ」チップは残る。赤 (完全固定) の表示は TimelineDayBoard-red-pin.test.tsx。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

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

function renderBoard(v: CourseGridVisit) {
  return render(<TimelineDayBoard columns={[column([v])]} />);
}

describe('青ピン廃止', () => {
  it('青のトグルも青い画鋲も出ない (week_pinned が来ても無視する)', () => {
    renderBoard(visit({ id: 'v1', source: 'auto', week_pinned: true } as never));
    expect(screen.queryByTestId('tl-week-pin-toggle-v1')).not.toBeInTheDocument();
    expect(document.querySelector('[data-icon="corner-week-push-pin"]')).not.toBeInTheDocument();
  });

  it('今週のみ (manual_week) は青い画鋲ではなく「今週のみ」チップで示す', () => {
    renderBoard(visit({ id: 'v2', source: 'manual_week', end_time: '12:30' }));
    expect(document.querySelector('[data-icon="corner-week-push-pin"]')).not.toBeInTheDocument();
    expect(screen.getByTestId('tl-week-only-chip-v2')).toBeInTheDocument();
  });

  it('赤い画鋲は角 (既定の位置) に 1 本だけ刺さる', () => {
    renderBoard(visit({ id: 'v3', source: 'manual_week', movability: 'locked' }));
    expect(document.querySelectorAll('[data-icon="corner-push-pin"]')).toHaveLength(1);
  });
});
