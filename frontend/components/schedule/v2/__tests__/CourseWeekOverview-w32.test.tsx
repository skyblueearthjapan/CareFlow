/**
 * Wave 32: CourseWeekOverview の担当スタッフ名 + capacity 明示テスト.
 *
 * カバーするシナリオ:
 *   1. staffMap + assignedStaffByTemplateWeekday が渡されたとき、
 *      セル冒頭に「担当: ○○」が表示される
 *   2. 担当未割当 (assignedStaffByTemplateWeekday にエントリなし) のとき
 *      「担当: 未割当」が表示される
 *   3. staffMap が渡されていても対応する staff が見つからないとき
 *      「担当: 未割当」が表示される
 *   4. capacity ラベルが「N 名 / 上限 M」形式で表示される
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
import type { StaffRead } from '@/lib/schemas/staff';

const baseTpl = {
  capacity_mon: 7,
  capacity_tue: 7,
  capacity_wed: 7,
  capacity_thu: 7,
  capacity_fri: 7,
  capacity_sat: 0,
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

function makeStaff(id: string, name: string): StaffRead {
  return {
    id,
    name,
    kana: null,
    code: null,
    sex: 'unknown',
    status: 'active',
    role: 'staff',
    primary_office_id: 'o1',
    is_trainee: false,
    note: null,
    created_at: '',
    updated_at: '',
    deleted_at: null,
  };
}

describe('CourseWeekOverview 担当スタッフ名表示 (Wave 32)', () => {
  it('1. staffMap + assignedStaffByTemplateWeekday が渡されたとき「担当: ○○」が表示される', () => {
    const tpl = makeTemplate('tpl-W32-A', 'A', 'o1');
    const staff = makeStaff('staff-w32-1', '山田 太郎');
    const staffMap = new Map([['staff-w32-1', staff]]);
    const assignedStaffByTemplateWeekday = new Map([['tpl-W32-A:0', 'staff-w32-1']]); // 月曜=0

    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={[]}
        onJumpToDay={vi.fn()}
        staffMap={staffMap}
        assignedStaffByTemplateWeekday={assignedStaffByTemplateWeekday}
      />,
    );

    const staffEl = screen.getByTestId('course-week-overview-staff-tpl-W32-A-0');
    expect(staffEl).toHaveTextContent('担当: 山田 太郎');
  });

  it('2. 担当未割当のとき「担当: 未割当」が表示される', () => {
    const tpl = makeTemplate('tpl-W32-B', 'B', 'o1');
    const staff = makeStaff('staff-w32-2', '鈴木 花子');
    const staffMap = new Map([['staff-w32-2', staff]]);
    // assignedStaffByTemplateWeekday に月曜のエントリなし (= 未割当)
    const assignedStaffByTemplateWeekday = new Map<string, string>();

    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={[]}
        onJumpToDay={vi.fn()}
        staffMap={staffMap}
        assignedStaffByTemplateWeekday={assignedStaffByTemplateWeekday}
      />,
    );

    const staffEl = screen.getByTestId('course-week-overview-staff-tpl-W32-B-0');
    expect(staffEl).toHaveTextContent('担当: 未割当');
  });

  it('3. staffMap に対応スタッフが見つからないとき「担当: 未割当」が表示される', () => {
    const tpl = makeTemplate('tpl-W32-C', 'C', 'o1');
    // staffMap には別スタッフしかいない
    const staffMap = new Map([['staff-other', makeStaff('staff-other', '別の人')]]);
    const assignedStaffByTemplateWeekday = new Map([['tpl-W32-C:0', 'staff-w32-unknown']]);

    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={[]}
        onJumpToDay={vi.fn()}
        staffMap={staffMap}
        assignedStaffByTemplateWeekday={assignedStaffByTemplateWeekday}
      />,
    );

    const staffEl = screen.getByTestId('course-week-overview-staff-tpl-W32-C-0');
    expect(staffEl).toHaveTextContent('担当: 未割当');
  });

  it('4. capacity ラベルが「N 名 / 上限 M」形式で表示される', () => {
    const tpl = makeTemplate('tpl-W32-D', 'D', 'o1');
    const visits: WeekOverviewVisit[] = [
      {
        id: 'v-cap-1',
        patient_id: 'p-1',
        patient_name: '佐藤',
        weekday: 0,
        course_template_id: 'tpl-W32-D',
        start_time: '09:30',
      },
      {
        id: 'v-cap-2',
        patient_id: 'p-2',
        patient_name: '田中',
        weekday: 0,
        course_template_id: 'tpl-W32-D',
        start_time: '10:00',
      },
      {
        id: 'v-cap-3',
        patient_id: 'p-3',
        patient_name: '木村',
        weekday: 0,
        course_template_id: 'tpl-W32-D',
        start_time: '11:00',
      },
    ];

    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={visits}
        onJumpToDay={vi.fn()}
      />,
    );

    const capEl = screen.getByTestId('course-week-overview-capacity-tpl-W32-D-0');
    // W32 fix (37eb867): 上限ラベルは UI 上 "上限 6" 固定で表示する仕様.
    //   DB の capacity 値は内部判定 (満杯 warning 色) には使うが、文言は固定.
    expect(capEl).toHaveTextContent('3 名 / 上限 6');
  });
});
