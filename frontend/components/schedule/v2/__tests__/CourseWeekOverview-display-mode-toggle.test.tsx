/**
 * CourseWeekOverview displayMode 切替テスト
 * (2026-W20 後期 /schedule UX 改修 — Task B / Task C).
 *
 * Task B: compact (省略あり) ⇄ full (全員表示) を `displayMode` prop で切替可能.
 *   - compact: 7 件超で「…他 N 名」表示
 *   - full   : slice せず全件表示
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

describe('CourseWeekOverview displayMode toggle (Task B)', () => {
  it('compact: 8 件入力 → 7 件描画 + 「…他 1 名」表示', () => {
    const tpl = makeTemplate('tpl-DM-A', 'A', 'o1');
    const visits = makeVisits(8, 'tpl-DM-A');
    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={visits}
        onJumpToDay={vi.fn()}
        displayMode="compact"
      />,
    );
    // 月曜 cell に 7 件まで表示 (visit 0..6).
    expect(screen.getByTestId('course-week-overview-name-v-0')).toBeInTheDocument();
    expect(screen.getByTestId('course-week-overview-name-v-6')).toBeInTheDocument();
    // 8 件目 (v-7) は省略される.
    expect(screen.queryByTestId('course-week-overview-name-v-7')).toBeNull();
    // overflow ラベル.
    expect(screen.getByText(/…他 1 名/)).toBeInTheDocument();
  });

  it('full: 8 件入力 → 全件描画 + overflow ラベルなし', () => {
    const tpl = makeTemplate('tpl-DM-B', 'B', 'o1');
    const visits = makeVisits(8, 'tpl-DM-B');
    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={visits}
        onJumpToDay={vi.fn()}
        displayMode="full"
      />,
    );
    // 全 8 件描画.
    for (let i = 0; i < 8; i += 1) {
      expect(screen.getByTestId(`course-week-overview-name-v-${i}`)).toBeInTheDocument();
    }
    // overflow ラベルなし.
    expect(screen.queryByText(/…他 \d+ 名/)).toBeNull();
  });

  it('default (= compact 相当) — 8 件で省略表示', () => {
    const tpl = makeTemplate('tpl-DM-C', 'C', 'o1');
    const visits = makeVisits(8, 'tpl-DM-C');
    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={visits}
        onJumpToDay={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('course-week-overview-name-v-7')).toBeNull();
    expect(screen.getByText(/…他 1 名/)).toBeInTheDocument();
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
        displayMode="full"
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
        displayMode="full"
      />,
    );
    const nameLi = screen.getByTestId('course-week-overview-name-v-time-1');
    expect(nameLi.textContent).toContain('10:30');
    expect(nameLi.textContent).toContain('山本 一郎');
  });
});
