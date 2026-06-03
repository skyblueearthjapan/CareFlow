/**
 * Phase G-55 (週ビュー 空き時間帯 時刻位置配置): CourseWeekOverview のセル内に
 * 空き時間帯マーカーを患者名と時刻順 interleave で挿入するテスト.
 *
 * 意味論 (モバイル AgendaBoard / 日テーブルと統一):
 *   - freeGapsByCell (key=`${tpl.id}:${wd}`) で各セルの gap (≥60分) を受け取る.
 *   - 頭数ゲート: cap - 配置済 <= 0 (満員) のセルでは gap を出さない.
 *   - gap は患者名と時刻順に並ぶ (午前=上 / 午後=下).
 *
 * カバー:
 *   1. remaining>0 のセルに gap マーカー (午前/午後) が時刻順に出る.
 *   2. 満員 (visit数>=cap) のセルでは gap マーカーを出さない (頭数ゲート).
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
import type { WeekOverviewVisit } from '../CourseWeekOverview';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { FreeGap } from '@/lib/scheduling/freeGaps';

const TPL_ID = 'tpl-g55-week';
const OFFICE_ID = 'o1';

const baseTpl = {
  capacity_mon: 6,
  capacity_tue: 6,
  capacity_wed: 6,
  capacity_thu: 6,
  capacity_fri: 6,
  capacity_sat: 6,
  capacity_sun: 0,
  notes: null,
  created_at: '',
  updated_at: '',
  deleted_at: null,
};

const tpl = {
  id: TPL_ID,
  label: 'A',
  office_id: OFFICE_ID,
  ...baseTpl,
} as unknown as CourseTemplateRead;

const officeNameById = new Map([[OFFICE_ID, '本店']]);

// staffCountFor: 月曜 (wd=0) に 1 名出勤 → effectiveCapacity が 6 を返す。
const staffCountFor = (_officeId: string, wd: number) => (wd === 0 ? 1 : 0);

const GAPS: FreeGap[] = [
  { startMin: 570, endMin: 660, label: '09:30〜11:00' },
  { startMin: 780, endMin: 1080, label: '13:00〜18:00' },
];

function makeVisit(over: Partial<WeekOverviewVisit>): WeekOverviewVisit {
  return {
    id: 'v1',
    patient_id: 'p1',
    patient_name: '山田 太郎',
    weekday: 0,
    course_template_id: TPL_ID,
    start_time: '11:00',
    ...over,
  };
}

function renderOverview(opts: {
  visits: WeekOverviewVisit[];
  freeGapsByCell?: Map<string, FreeGap[]>;
}) {
  return render(
    <CourseWeekOverview
      templates={[tpl]}
      officeNameById={officeNameById}
      visits={opts.visits}
      onJumpToDay={vi.fn()}
      staffCountFor={staffCountFor}
      freeGapsByCell={opts.freeGapsByCell}
    />,
  );
}

describe('Phase G-55: CourseWeekOverview 週ビュー 空き時間帯 時刻位置', () => {
  it('1. remaining>0 のセルに gap マーカー (午前/午後) が時刻順に出る', () => {
    const cellMap = new Map<string, FreeGap[]>([[`${TPL_ID}:0`, GAPS]]);
    renderOverview({ visits: [makeVisit({})], freeGapsByCell: cellMap });
    const am = screen.getByTestId(`course-week-overview-free-gap-${TPL_ID}-0-570`);
    const pm = screen.getByTestId(`course-week-overview-free-gap-${TPL_ID}-0-780`);
    expect(am).toHaveAttribute('data-free-gap-start', '570');
    expect(pm).toHaveAttribute('data-free-gap-start', '780');
    expect(am.textContent).toContain('09:30〜11:00');
    expect(pm.textContent).toContain('13:00〜18:00');
    // 午前 gap (570) が午後 gap (780) より DOM 上で前 (時刻順)。
    expect(am.compareDocumentPosition(pm) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('2. 満員 (visit数>=cap) のセルでは gap マーカーを出さない (頭数ゲート)', () => {
    // cap=6 のセルに 6 visit を配置 → remaining=0 → gap 非表示。
    const visits = Array.from({ length: 6 }, (_, i) =>
      makeVisit({ id: `v${i}`, patient_id: `p${i}`, start_time: '11:00' }),
    );
    const cellMap = new Map<string, FreeGap[]>([[`${TPL_ID}:0`, GAPS]]);
    renderOverview({ visits, freeGapsByCell: cellMap });
    expect(
      screen.queryByTestId(`course-week-overview-free-gap-${TPL_ID}-0-570`),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId(`course-week-overview-free-gap-${TPL_ID}-0-780`),
    ).not.toBeInTheDocument();
  });
});
