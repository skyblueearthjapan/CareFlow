/**
 * CourseWeekOverview — Wave 18 Phase B-6 テスト.
 *
 * - 全 (template × weekday) のセルが描画される
 * - 容量バッジ "x/N" が表示され、満杯では warning スタイル
 * - capacity=0 の曜日は「休」表示
 * - 曜日ヘッダクリックで onJumpToDay(wd) が呼ばれる
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

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

describe('CourseWeekOverview (B-6)', () => {
  it('templates が空のとき空表示', () => {
    render(
      <CourseWeekOverview
        templates={[]}
        officeNameById={new Map()}
        visits={[]}
        onJumpToDay={vi.fn()}
      />,
    );
    expect(screen.getByTestId('course-week-overview-empty')).toBeInTheDocument();
  });

  it('全 (template × weekday) セルが描画される', () => {
    const templates = [makeTemplate('tpl-A', 'A', 'o1'), makeTemplate('tpl-B', 'B', 'o1')];
    render(
      <CourseWeekOverview
        templates={templates}
        officeNameById={new Map([['o1', '本店']])}
        visits={[]}
        onJumpToDay={vi.fn()}
      />,
    );
    // 2 templates × 6 weekdays = 12 セル
    for (const tplId of ['tpl-A', 'tpl-B']) {
      for (const wd of [0, 1, 2, 3, 4, 5]) {
        expect(screen.getByTestId(`course-week-overview-cell-${tplId}-${wd}`)).toBeInTheDocument();
      }
    }
    // ヘッダー (officeName-label)
    expect(screen.getByTestId('course-week-overview-row-header-tpl-A')).toHaveTextContent('本店-A');
  });

  it('容量バッジが "x/N" で表示される', () => {
    const tpl = makeTemplate('tpl-A', 'A', 'o1');
    const visits: WeekOverviewVisit[] = [
      {
        id: 'v-1',
        patient_id: 'p-1',
        patient_name: '田中',
        weekday: 0,
        course_template_id: 'tpl-A',
        start_time: null,
      },
      {
        id: 'v-2',
        patient_id: 'p-2',
        patient_name: '佐藤',
        weekday: 0,
        course_template_id: 'tpl-A',
        start_time: null,
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
    const badge = screen.getByTestId('course-week-overview-capacity-tpl-A-0');
    expect(badge).toHaveTextContent('2 名 / 上限 4');
    // 患者氏名が描画される
    expect(screen.getByTestId('course-week-overview-name-v-1')).toHaveTextContent('田中');
    expect(screen.getByTestId('course-week-overview-name-v-2')).toHaveTextContent('佐藤');
  });

  it('capacity=0 の曜日は「休」表示で、容量バッジは出ない', () => {
    // 日曜は表示対象外なので別の曜日を 0 にする
    const tpl: CourseTemplateRead = makeTemplate('tpl-A', 'A', 'o1');
    const tpl2 = { ...tpl, capacity_sat: 0 };
    render(
      <CourseWeekOverview
        templates={[tpl2]}
        officeNameById={new Map([['o1', '本店']])}
        visits={[]}
        onJumpToDay={vi.fn()}
      />,
    );
    const cell = screen.getByTestId('course-week-overview-cell-tpl-A-5');
    expect(cell).toHaveTextContent('休');
    expect(screen.queryByTestId('course-week-overview-capacity-tpl-A-5')).not.toBeInTheDocument();
  });

  it('曜日ヘッダクリックで onJumpToDay(wd) が呼ばれる', () => {
    const onJumpToDay = vi.fn();
    render(
      <CourseWeekOverview
        templates={[makeTemplate('tpl-A', 'A', 'o1')]}
        officeNameById={new Map([['o1', '本店']])}
        visits={[]}
        onJumpToDay={onJumpToDay}
      />,
    );
    fireEvent.click(screen.getByTestId('course-week-overview-header-3')); // 木
    expect(onJumpToDay).toHaveBeenCalledWith(3);
  });

  it('start_time 付き visit が "HH:MM 氏名" 形式で表示される', () => {
    const tpl = makeTemplate('tpl-A', 'A', 'o1');
    const visits: WeekOverviewVisit[] = [
      {
        id: 'v-t1',
        patient_id: 'p-t1',
        patient_name: '山田',
        weekday: 1,
        course_template_id: 'tpl-A',
        start_time: '09:30:00',
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
    expect(screen.getByTestId('course-week-overview-name-v-t1')).toHaveTextContent('09:30 山田');
  });

  it('start_time が null の visit は氏名のみ表示', () => {
    const tpl = makeTemplate('tpl-A', 'A', 'o1');
    const visits: WeekOverviewVisit[] = [
      {
        id: 'v-t2',
        patient_id: 'p-t2',
        patient_name: '鈴木',
        weekday: 2,
        course_template_id: 'tpl-A',
        start_time: null,
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
    const el = screen.getByTestId('course-week-overview-name-v-t2');
    expect(el).toHaveTextContent('鈴木');
    expect(el).not.toHaveTextContent(':');
  });
});
