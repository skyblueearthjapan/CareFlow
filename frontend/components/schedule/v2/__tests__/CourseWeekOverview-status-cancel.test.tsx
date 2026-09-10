/**
 * 週リスト (CourseWeekOverview) — 患者ステータス連動の取消は出さない。
 *
 * design 2026-09-09 §7-4 (PO 決定): source='status_cancel' + status='cancelled'
 * の訪問は盤面から消す。「今週だけ取消」= manual_cancel は従来どおり残す。
 * 件数バッジ (n 名 / 上限 N) からも外れることを併せて担保する。
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

function makeVisit(over: Partial<WeekOverviewVisit> & { id: string }): WeekOverviewVisit {
  return {
    patient_id: `p-${over.id}`,
    patient_name: `患者${over.id}`,
    weekday: 0,
    course_template_id: 'tpl-A',
    start_time: '09:30',
    ...over,
  };
}

/** A-E を全開講させる十分なスタッフ数 (=5) を返すスタブ. */
const fullStaff = () => 5;

function renderWith(visits: WeekOverviewVisit[], showInactive = false) {
  return render(
    <CourseWeekOverview
      templates={[makeTemplate('tpl-A', 'A', 'o1')]}
      officeNameById={new Map([['o1', '本店']])}
      visits={visits}
      onJumpToDay={vi.fn()}
      staffCountFor={fullStaff}
      showInactive={showInactive}
    />,
  );
}

describe('CourseWeekOverview — status_cancel は非表示', () => {
  it('status_cancel の訪問は出さず、manual_cancel と通常訪問は出す', () => {
    renderWith([
      makeVisit({ id: 'plain', source: 'auto', status: 'planned' }),
      makeVisit({ id: 'manual', source: 'manual_cancel', status: 'cancelled' }),
      makeVisit({ id: 'status', source: 'status_cancel', status: 'cancelled' }),
    ]);
    expect(screen.getByTestId('course-week-overview-name-plain')).toBeInTheDocument();
    expect(screen.getByTestId('course-week-overview-name-manual')).toBeInTheDocument();
    expect(screen.queryByTestId('course-week-overview-name-status')).not.toBeInTheDocument();
  });

  it('showInactive=true なら連動取消も打ち消し線つきで出す (残骸点検・§3-4)', () => {
    renderWith(
      [
        makeVisit({
          id: 'status',
          source: 'status_cancel',
          status: 'cancelled',
          patient_status: 'admitted',
        }),
      ],
      true,
    );
    const row = screen.getByTestId('course-week-overview-name-status');
    expect(row.className).toContain('line-through');
    expect(screen.getByTestId('course-week-overview-inactive-status')).toHaveTextContent(
      '取消（連動）',
    );
  });

  it('非稼働患者の予定が残っていたらバッジ + 薄色で見せる (トグル OFF でも・§3-4)', () => {
    renderWith([
      makeVisit({ id: 'residue', source: 'auto', status: 'planned', patient_status: 'admitted' }),
    ]);
    const row = screen.getByTestId('course-week-overview-name-residue');
    expect(row.className).toContain('opacity-60');
    expect(screen.getByTestId('course-week-overview-inactive-residue')).toHaveTextContent('入院中');
  });

  it('ステータス変更日より前の予定にはバッジを出さない (PO 2026-09-10)', () => {
    renderWith([
      makeVisit({
        id: 'past',
        source: 'auto',
        status: 'planned',
        patient_status: 'admitted',
        // 入院中にしたのは 9/8。9/7 は実際に訪問した日なので従来表示のまま。
        patient_status_since: '2026-09-08',
        visit_date: '2026-09-07',
      }),
    ]);
    const row = screen.getByTestId('course-week-overview-name-past');
    expect(row.className).not.toContain('opacity-60');
    expect(screen.queryByTestId('course-week-overview-inactive-past')).not.toBeInTheDocument();
  });

  it('ステータス変更日以降の予定にはバッジを出す (PO 2026-09-10)', () => {
    renderWith([
      makeVisit({
        id: 'after',
        source: 'auto',
        status: 'planned',
        patient_status: 'admitted',
        patient_status_since: '2026-09-08',
        visit_date: '2026-09-08',
      }),
    ]);
    expect(screen.getByTestId('course-week-overview-inactive-after')).toHaveTextContent('入院中');
  });

  it('トグル ON でも件数バッジは変わらない (枠の埋まり具合は取消を数えない)', () => {
    const visits = [
      makeVisit({ id: 'plain', source: 'auto', status: 'planned' }),
      makeVisit({
        id: 'status',
        source: 'status_cancel',
        status: 'cancelled',
        patient_status: 'admitted',
      }),
    ];
    renderWith(visits, true);
    // 取消も打ち消し線つきで見えているが…
    expect(screen.getByTestId('course-week-overview-name-status')).toBeInTheDocument();
    // …定員バッジは 1 名のまま (残骸点検のトグルで枠が埋まって見えない)。
    expect(screen.getByTestId('course-week-overview-capacity-tpl-A-0')).toHaveTextContent(
      '1 名 / 上限 6',
    );
  });

  it('件数バッジからも status_cancel が外れる', () => {
    renderWith([
      makeVisit({ id: 'plain', source: 'auto', status: 'planned' }),
      makeVisit({ id: 'status', source: 'status_cancel', status: 'cancelled' }),
    ]);
    expect(screen.getByTestId('course-week-overview-capacity-tpl-A-0')).toHaveTextContent(
      '1 名 / 上限 6',
    );
  });
});
