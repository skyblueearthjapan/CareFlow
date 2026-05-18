/**
 * CourseWeekOverview 全員表示 + pair cluster + 情報絞り込みテスト
 * (Phase E-1: コンパクトモード撤去 — 常に全員表示).
 *
 * Phase E-1: `displayMode` prop は廃止. 常に「全員表示」(= 全 visit を slice せず描画).
 *   - 8 件入力 → 全 8 件描画 (overflow ラベルなし)
 *
 * Task C: 週ビューは情報を最小限に絞る.
 *   - 患者名 (button) + 開始時刻 (HH:MM) を表示
 *   - 同住所ペアは 1 つの黄色囲み (data-pair-size=2) で wrap
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

const baseTpl = {
  capacity_mon: 10,
  capacity_tue: 10,
  capacity_wed: 10,
  capacity_thu: 10,
  capacity_fri: 10,
  capacity_sat: 10,
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

function makeVisits(n: number, templateId: string): WeekOverviewVisit[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `v-${i}`,
    patient_id: `p-${i}`,
    patient_name: `患者${i}`,
    weekday: 0,
    course_template_id: templateId,
    start_time: `09:${String(i * 5).padStart(2, '0')}`,
  }));
}

describe('CourseWeekOverview 常時全員表示 (Phase E-1)', () => {
  it('8 件入力 → 全件描画 + overflow ラベルなし', () => {
    const tpl = makeTemplate('tpl-DM-B', 'B', 'o1');
    const visits = makeVisits(8, 'tpl-DM-B');
    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={visits}
        onJumpToDay={vi.fn()}
      />,
    );
    // 全 8 件描画.
    for (let i = 0; i < 8; i += 1) {
      expect(screen.getByTestId(`course-week-overview-name-v-${i}`)).toBeInTheDocument();
    }
    // overflow ラベルなし.
    expect(screen.queryByText(/…他 \d+ 名/)).toBeNull();
  });
});

describe('CourseWeekOverview 週ビュー pair cluster + 情報絞り込み (Task C / E)', () => {
  it('同住所ペア (lat/lng key) は 1 つの黄色囲みで wrap される', () => {
    const tpl = makeTemplate('tpl-DM-D', 'D', 'o1');
    const visits: WeekOverviewVisit[] = [
      {
        id: 'v-pair-a',
        patient_id: 'p-pair-a',
        patient_name: '田中 太郎',
        weekday: 0,
        course_template_id: 'tpl-DM-D',
        start_time: '09:00',
      },
      {
        id: 'v-pair-b',
        patient_id: 'p-pair-b',
        patient_name: '田中 次郎',
        weekday: 0,
        course_template_id: 'tpl-DM-D',
        start_time: '09:15',
      },
    ];
    // 2 名とも同 lat/lng bucket
    const sameAddressKeyByPatientId = new Map<string, string | null>([
      ['p-pair-a', 'lat-lng-bucket-1'],
      ['p-pair-b', 'lat-lng-bucket-1'],
    ]);
    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={visits}
        onJumpToDay={vi.fn()}
        sameAddressKeyByPatientId={sameAddressKeyByPatientId}
      />,
    );
    // pair-cluster 要素が描画される (data-pair-size=2).
    const pairs = screen.getAllByTestId(/^course-week-overview-pair-/);
    expect(pairs).toHaveLength(1);
    expect(pairs[0]!.getAttribute('data-pair-size')).toBe('2');
    // ヘッダー文言.
    expect(screen.getByText(/📍 同住所 \(2 名\)/)).toBeInTheDocument();
    // 各患者は cluster 内に表示.
    expect(screen.getByTestId('course-week-overview-name-v-pair-a')).toBeInTheDocument();
    expect(screen.getByTestId('course-week-overview-name-v-pair-b')).toBeInTheDocument();
  });

  it('開始時刻が patient 行に表示される (Task C: 時刻は省略しない)', () => {
    const tpl = makeTemplate('tpl-DM-E', 'E', 'o1');
    const visits: WeekOverviewVisit[] = [
      {
        id: 'v-time-1',
        patient_id: 'p-time-1',
        patient_name: '山本 一郎',
        weekday: 0,
        course_template_id: 'tpl-DM-E',
        start_time: '10:30',
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
    const nameLi = screen.getByTestId('course-week-overview-name-v-time-1');
    expect(nameLi.textContent).toContain('10:30');
    expect(nameLi.textContent).toContain('山本 一郎');
  });
});
