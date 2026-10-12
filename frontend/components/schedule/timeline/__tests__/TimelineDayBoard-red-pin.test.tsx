/**
 * 赤ピン (完全固定 = movability==='locked') の盤面表示。
 * 赤の付け外しは患者マスタだけ — 盤面には赤の画鋲 (表示) があるが、トグルは無い。
 * 仕様: docs/plans/red-pin-simplify-design.md (K-6 / Q-2)
 */
import { render, screen } from '@testing-library/react';
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

function renderBoard(v: CourseGridVisit) {
  return render(<TimelineDayBoard columns={[column([v])]} />);
}

describe('赤ピン (完全固定) の盤面表示', () => {
  it('movability=locked の訪問には赤い画鋲が出る', () => {
    renderBoard(visit({ id: 'r1', movability: 'locked' }));
    expect(document.querySelector('[data-icon="corner-push-pin"]')).toBeInTheDocument();
  });

  it('unknown / 未設定の訪問には赤い画鋲は出ない', () => {
    renderBoard(visit({ id: 'r2', movability: 'unknown' }));
    expect(document.querySelector('[data-icon="corner-push-pin"]')).not.toBeInTheDocument();
  });

  it('赤い画鋲の表示は movability だけを根拠にする (旧 is_pinned では出ない)', () => {
    renderBoard(visit({ id: 'r3', is_pinned: true } as Partial<CourseGridVisit> & { id: string }));
    expect(document.querySelector('[data-icon="corner-push-pin"]')).not.toBeInTheDocument();
  });

  it('赤い画鋲が出ていても、盤面に赤のトグルは無い', () => {
    renderBoard(visit({ id: 'r4', movability: 'locked', source: 'auto' }));
    expect(screen.queryByTestId('tl-pin-toggle-r4')).not.toBeInTheDocument();
    expect(screen.queryByTestId('tl-week-pin-toggle-r4')).not.toBeInTheDocument();
  });
});
