/**
 * 日タイムライン — 患者ステータス連動の取消 (source='status_cancel') は描かない。
 *
 * design 2026-09-09 §7-4 (PO 決定):
 *   - 非稼働 (入院中/一時休止/解約済み/開始前) になった患者の未来訪問は
 *     BE が status='cancelled' + source='status_cancel' で取り消す。
 *     盤面からは **消す** (打ち消し線で残さない)。
 *   - 「今週だけ取消」= source='manual_cancel' は従来どおり打ち消し線で残す。
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
    start_time: '09:30:00',
    end_time: '10:00:00',
    ...over,
  } as CourseGridVisit;
}

function column(visits: CourseGridVisit[]): TimelineCourseColumn {
  return {
    key: 'c1',
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
  };
}

describe('TimelineDayBoard — status_cancel は非表示', () => {
  it('status_cancel の訪問は描かず、manual_cancel と通常訪問は描く', () => {
    render(
      <TimelineDayBoard
        columns={[
          column([
            visit({ id: 'plain', source: 'auto', status: 'planned' }),
            visit({
              id: 'manual',
              source: 'manual_cancel',
              status: 'cancelled',
              start_time: '10:30:00',
              end_time: '11:00:00',
            }),
            visit({
              id: 'status',
              source: 'status_cancel',
              status: 'cancelled',
              start_time: '11:30:00',
              end_time: '12:00:00',
            }),
          ]),
        ]}
        weekdayLabel="月"
      />,
    );
    expect(screen.getByTestId('tl-visit-plain')).toBeInTheDocument();
    // 「今週だけ取消」は残す (打ち消し線つき)。
    expect(screen.getByTestId('tl-visit-manual')).toBeInTheDocument();
    // 患者ステータス連動の取消は消す。
    expect(screen.queryByTestId('tl-visit-status')).not.toBeInTheDocument();
    expect(screen.queryByText('患者status')).not.toBeInTheDocument();
  });

  it('source=status_cancel でも status が cancelled でなければ描く (寛容判定)', () => {
    render(
      <TimelineDayBoard
        columns={[column([visit({ id: 'odd', source: 'status_cancel', status: 'planned' })])]}
        weekdayLabel="月"
      />,
    );
    expect(screen.getByTestId('tl-visit-odd')).toBeInTheDocument();
  });
});
