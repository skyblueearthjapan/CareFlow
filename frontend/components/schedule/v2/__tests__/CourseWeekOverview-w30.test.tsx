/**
 * Wave 30: CourseWeekOverview の event ラベルに title を含む表示テスト.
 *
 * カバーするシナリオ:
 *   1. title ありの event は "種別: タイトル HH:MM-HH:MM" 形式で表示される
 *   2. title なし (空文字) の event は "種別 HH:MM-HH:MM" 形式で表示される
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

import { CourseWeekOverview } from '../CourseWeekOverview';
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
  id: 'event-w30',
  date: '2026-05-04',
  title: '接遇マナー',
  start_time: '14:00',
  end_time: '16:00',
  type: '研修',
  ...overrides,
});

describe('CourseWeekOverview event ラベルに title 表示 (Wave 30)', () => {
  it('1. title ありの event は "種別: タイトル HH:MM-HH:MM" 形式で表示される', () => {
    const tpl = makeTemplate('tpl-A', 'A', 'o1');
    const event = makeEvent({ title: '接遇マナー' });
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

    const el = screen.getByTestId('course-week-overview-event-event-w30');
    expect(el).toHaveTextContent('研修: 接遇マナー 14:00-16:00');
  });

  it('2. title なし (空文字) の event は "種別 HH:MM-HH:MM" 形式で表示される', () => {
    const tpl = makeTemplate('tpl-A', 'A', 'o1');
    const event = makeEvent({ title: '' });
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

    const el = screen.getByTestId('course-week-overview-event-event-w30');
    expect(el).toHaveTextContent('研修 14:00-16:00');
    expect(el).not.toHaveTextContent('研修:');
  });
});
