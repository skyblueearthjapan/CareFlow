/**
 * 週タイムラインの重なりカード (lanes ≥ 2) — mac-ui-crossplatform-design.md §2-B1
 * (日ビュー TimelineDayBoard.narrow-lanes.test と同じ規則の週版)。
 */
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { WeekOverviewVisit } from '@/components/schedule/v2/CourseWeekOverview';
import {
  WeekTimelineBoard,
  type WeekTimelineOption,
} from '@/components/schedule/timeline/WeekTimelineBoard';

const OPTIONS: WeekTimelineOption[] = [{ templateId: 't1', label: '稲毛A・田中 一郎' }];

function wv(over: Partial<WeekOverviewVisit> & { id: string; weekday: number }): WeekOverviewVisit {
  return {
    patient_id: `p-${over.id}`,
    patient_name: `患者${over.id}`,
    course_template_id: 't1',
    start_time: '09:30:00',
    ...over,
  } as WeekOverviewVisit;
}

function nameEl(id: string) {
  return within(screen.getByTestId(`wtl-visit-${id}`)).getByText(`患者${id}`);
}

describe('WeekTimelineBoard — 重なりカードの氏名 (§2-B1)', () => {
  it('単独カードは 12px・truncate', () => {
    render(
      <WeekTimelineBoard
        options={OPTIONS}
        visits={[wv({ id: 'x', weekday: 0, start_time: '09:30:00', end_time: '10:30:00' })]}
      />,
    );
    const el = nameEl('x');
    expect(el.className).toContain('text-[12px]');
    expect(el.className).toContain('truncate');
  });

  it('2 lanes の 60 分カードは 11px・2 行折り返し・時刻行あり', () => {
    render(
      <WeekTimelineBoard
        options={OPTIONS}
        visits={[
          wv({ id: 'x', weekday: 0, start_time: '09:30:00', end_time: '10:30:00' }),
          wv({ id: 'y', weekday: 0, start_time: '10:00:00', end_time: '11:00:00' }),
        ]}
      />,
    );
    const el = nameEl('x');
    expect(el.className).toContain('text-[11px]');
    expect(el.className).toContain('line-clamp-2');
    expect(el.className).not.toContain('truncate');
    const card = screen.getByTestId('wtl-visit-x');
    expect(within(card).getByText(/09:30・60分/)).toBeInTheDocument();
    expect(card.className).toContain('px-1');
  });

  it('2 lanes の 30 分カードは氏名 2 行のみ (時刻行なし)', () => {
    render(
      <WeekTimelineBoard
        options={OPTIONS}
        visits={[
          wv({ id: 'x', weekday: 0, start_time: '09:30:00', end_time: '10:00:00' }),
          wv({ id: 'y', weekday: 0, start_time: '09:45:00', end_time: '10:15:00' }),
        ]}
      />,
    );
    expect(nameEl('x').className).toContain('line-clamp-2');
    expect(within(screen.getByTestId('wtl-visit-x')).queryByText(/09:30・30分/)).toBeNull();
  });
});
