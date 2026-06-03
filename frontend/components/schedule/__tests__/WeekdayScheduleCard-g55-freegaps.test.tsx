/**
 * Phase G-55 (リストモード 空き時間帯 時刻位置配置): WeekdayScheduleCard が
 * コースの訪問行に空き時間帯マーカーを時刻順 interleave で挿入するテスト.
 *
 * 意味論 (モバイル AgendaBoard / 日テーブル / 週ビューと統一):
 *   - CourseListItem.freeGaps (≥60分) + capacity を受け取る.
 *   - 頭数ゲート: capacity.remaining<=0 (満員) のときは gap を出さない.
 *   - gap は visit と時刻順に並ぶ (午前=上 / 午後=下).
 *
 * カバー:
 *   1. remaining>0 → 午前 gap が visit より前、午後 gap が visit より後 (時刻順 interleave).
 *   2. 満員 (remaining<=0) → gap を出さない (頭数ゲート).
 *   3. capacity 未指定 → gap を出さない (後方互換).
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

vi.mock('../v2/VisitArrow', () => ({
  VisitArrow: () => <span data-testid="visit-arrow" />,
}));

vi.mock('../v2/PinScopeMenu', () => ({
  PinScopeMenu: () => null,
}));

import { WeekdayScheduleCard } from '../WeekdayScheduleCard';
import type { CourseListItem } from '../WeekdayScheduleCard';
import type { FreeGap } from '@/lib/scheduling/freeGaps';

const GAPS: FreeGap[] = [
  { startMin: 570, endMin: 660, label: '09:30〜11:00' },
  { startMin: 780, endMin: 1080, label: '13:00〜18:00' },
];

function makeCourse(over: Partial<CourseListItem>): CourseListItem {
  return {
    key: 'c1',
    title: '本店 A コース',
    visits: [
      {
        key: 'v1',
        patient_id: 'p1',
        start_time: '11:30',
        patient_name: '山田 太郎',
      },
    ],
    ...over,
  };
}

const PREFIX = 'list-g55';

describe('Phase G-55: WeekdayScheduleCard リスト 空き時間帯 時刻順 interleave', () => {
  it('1. remaining>0 → 午前 gap は visit より前、午後 gap は visit より後', () => {
    render(
      <WeekdayScheduleCard
        title="月曜 コース一覧"
        courses={[makeCourse({ freeGaps: GAPS, capacity: { filled: 1, max: 6 } })]}
        testIdPrefix={PREFIX}
      />,
    );
    const am = screen.getByTestId(`${PREFIX}-free-gap-570`);
    const pm = screen.getByTestId(`${PREFIX}-free-gap-780`);
    const visit = screen.getByText('山田 太郎');
    expect(am).toHaveAttribute('data-free-gap-start', '570');
    expect(pm).toHaveAttribute('data-free-gap-start', '780');
    // 午前 gap (09:30) → visit (11:30) → 午後 gap (13:00) の時刻順。
    expect(am.compareDocumentPosition(visit) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(visit.compareDocumentPosition(pm) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('2. 満員 (remaining<=0) → gap を出さない (頭数ゲート)', () => {
    render(
      <WeekdayScheduleCard
        title="月曜 コース一覧"
        courses={[makeCourse({ freeGaps: GAPS, capacity: { filled: 6, max: 6 } })]}
        testIdPrefix={PREFIX}
      />,
    );
    expect(screen.queryByTestId(`${PREFIX}-free-gap-570`)).not.toBeInTheDocument();
    expect(screen.queryByTestId(`${PREFIX}-free-gap-780`)).not.toBeInTheDocument();
  });

  it('3. capacity 未指定 → gap を出さない (後方互換)', () => {
    render(
      <WeekdayScheduleCard
        title="月曜 コース一覧"
        courses={[makeCourse({ freeGaps: GAPS })]}
        testIdPrefix={PREFIX}
      />,
    );
    expect(screen.queryByTestId(`${PREFIX}-free-gap-570`)).not.toBeInTheDocument();
  });
});
