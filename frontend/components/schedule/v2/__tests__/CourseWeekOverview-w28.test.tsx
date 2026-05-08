/**
 * Wave 28 Phase B-2: CourseWeekOverview のセル内 event エントリ表示テスト.
 *
 * カバーするシナリオ:
 *   1. event がある曜日セルに event エントリが描画される (data-testid="course-week-overview-event-{id}")
 *   2. visit と event が時刻順にソートされて描画される
 *   3. staffEventsByStaff が未渡しのときは event エントリが描画されない
 *   4. assignedStaffByTemplateWeekday が未渡しのときは event エントリが描画されない
 *   5. event のラベルは "HH:MM-HH:MM 研修" の形式で表示される
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('@/components/ui/card', () => ({
  Card: ({
    children,
    className,
    ...rest
  }: {
    children: React.ReactNode;
    className?: string;
    [k: string]: unknown;
  }) => (
    <div className={className} {...rest}>
      {children}
    </div>
  ),
}));

vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

import { CourseWeekOverview, type WeekOverviewVisit } from '../CourseWeekOverview';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { EventRead } from '@/lib/schemas/staff-events';

const baseTpl = {
  capacity_mon: 4,
  capacity_tue: 4,
  capacity_wed: 4,
  capacity_thu: 4,
  capacity_fri: 4,
  capacity_sat: 4,
  capacity_sun: 0,
  notes: null,
  created_at: '',
  updated_at: '',
  deleted_at: null,
};

function makeTemplate(id: string, label: string, officeId: string): CourseTemplateRead {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return { id, label, office_id: officeId, ...baseTpl } as any;
}

const makeEvent = (overrides: Partial<EventRead> = {}): EventRead => ({
  id: 'event-1',
  date: '2026-05-04', // 月曜
  title: '研修',
  start_time: '10:00',
  end_time: '12:00',
  type: '研修',
  ...overrides,
});

describe('CourseWeekOverview event エントリ表示 (Wave 28 B-2)', () => {
  it('1. 担当スタッフの event がある曜日セルに event エントリが描画される', () => {
    const tpl = makeTemplate('tpl-A', 'A', 'o1');
    const staffEventsByStaff = new Map([['staff-1', [makeEvent()]]]);
    const assignedStaffByTemplateWeekday = new Map([['tpl-A:0', 'staff-1']]); // 月曜=0

    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={[]}
        onJumpToDay={vi.fn()}
        staffEventsByStaff={staffEventsByStaff}
        assignedStaffByTemplateWeekday={assignedStaffByTemplateWeekday}
      />,
    );

    expect(screen.getByTestId('course-week-overview-event-event-1')).toBeInTheDocument();
  });

  it('2. visit と event が時刻順にソートして描画される', () => {
    const tpl = makeTemplate('tpl-A', 'A', 'o1');
    // event: 10:00-12:00, visit: 09:30
    const event = makeEvent({ start_time: '10:00', end_time: '12:00' });
    const staffEventsByStaff = new Map([['staff-1', [event]]]);
    const assignedStaffByTemplateWeekday = new Map([['tpl-A:0', 'staff-1']]);
    const visits: WeekOverviewVisit[] = [
      {
        id: 'v-early',
        patient_id: 'p-1',
        patient_name: '田中',
        weekday: 0,
        course_template_id: 'tpl-A',
        start_time: '09:30',
      },
      {
        id: 'v-late',
        patient_id: 'p-2',
        patient_name: '佐藤',
        weekday: 0,
        course_template_id: 'tpl-A',
        start_time: '11:00',
      },
    ];

    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={visits}
        onJumpToDay={vi.fn()}
        staffEventsByStaff={staffEventsByStaff}
        assignedStaffByTemplateWeekday={assignedStaffByTemplateWeekday}
      />,
    );

    // 全エントリが描画される
    expect(screen.getByTestId('course-week-overview-name-v-early')).toBeInTheDocument();
    expect(screen.getByTestId('course-week-overview-event-event-1')).toBeInTheDocument();
    expect(screen.getByTestId('course-week-overview-name-v-late')).toBeInTheDocument();

    // 時刻順: v-early(09:30) → event(10:00) → v-late(11:00)
    const items = screen
      .getByTestId('course-week-overview-cell-tpl-A-0')
      .querySelectorAll('li[data-testid]');
    const ids = Array.from(items).map((el) => el.getAttribute('data-testid') ?? '');
    expect(ids.indexOf('course-week-overview-name-v-early')).toBeLessThan(
      ids.indexOf('course-week-overview-event-event-1'),
    );
    expect(ids.indexOf('course-week-overview-event-event-1')).toBeLessThan(
      ids.indexOf('course-week-overview-name-v-late'),
    );
  });

  it('3. staffEventsByStaff が未渡しのときは event エントリが描画されない', () => {
    const tpl = makeTemplate('tpl-A', 'A', 'o1');
    const assignedStaffByTemplateWeekday = new Map([['tpl-A:0', 'staff-1']]);

    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={[]}
        onJumpToDay={vi.fn()}
        assignedStaffByTemplateWeekday={assignedStaffByTemplateWeekday}
        // staffEventsByStaff は意図的に渡さない
      />,
    );

    expect(screen.queryByTestId('course-week-overview-event-event-1')).not.toBeInTheDocument();
  });

  it('4. assignedStaffByTemplateWeekday が未渡しのときは event エントリが描画されない', () => {
    const tpl = makeTemplate('tpl-A', 'A', 'o1');
    const staffEventsByStaff = new Map([['staff-1', [makeEvent()]]]);

    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={[]}
        onJumpToDay={vi.fn()}
        staffEventsByStaff={staffEventsByStaff}
        // assignedStaffByTemplateWeekday は意図的に渡さない
      />,
    );

    expect(screen.queryByTestId('course-week-overview-event-event-1')).not.toBeInTheDocument();
  });

  it('5. event のラベルは "HH:MM-HH:MM イベント種別" 形式で表示される', () => {
    const tpl = makeTemplate('tpl-A', 'A', 'o1');
    const event = makeEvent({ start_time: '14:00', end_time: '16:00', type: '研修' });
    const staffEventsByStaff = new Map([['staff-1', [event]]]);
    const assignedStaffByTemplateWeekday = new Map([['tpl-A:0', 'staff-1']]);

    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={[]}
        onJumpToDay={vi.fn()}
        staffEventsByStaff={staffEventsByStaff}
        assignedStaffByTemplateWeekday={assignedStaffByTemplateWeekday}
      />,
    );

    const el = screen.getByTestId('course-week-overview-event-event-1');
    expect(el).toHaveTextContent('14:00-16:00 研修');
  });
});
