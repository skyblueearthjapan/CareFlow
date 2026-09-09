/**
 * 週タイムライン — 患者ステータス連動の取消 (source='status_cancel') は描かない。
 *
 * design 2026-09-09 §7-4。判定は source と status の **両方** を見るので、
 * 親 (CourseDayTablePanel.overviewVisits) が status を運んでいることが前提
 * (2026-09-09 に status のコピー漏れを修正した回帰でもある)。
 */
import { render, screen } from '@testing-library/react';
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
    end_time: '10:00:00',
    ...over,
  } as WeekOverviewVisit;
}

describe('WeekTimelineBoard — status_cancel は非表示', () => {
  it('status_cancel は描かず、manual_cancel と通常訪問は描く', () => {
    render(
      <WeekTimelineBoard
        options={OPTIONS}
        visits={[
          wv({ id: 'plain', weekday: 0, source: 'auto', status: 'planned' }),
          wv({ id: 'manual', weekday: 1, source: 'manual_cancel', status: 'cancelled' }),
          wv({ id: 'status', weekday: 2, source: 'status_cancel', status: 'cancelled' }),
        ]}
      />,
    );
    expect(screen.getByTestId('wtl-visit-plain')).toBeInTheDocument();
    expect(screen.getByTestId('wtl-visit-manual')).toBeInTheDocument();
    expect(screen.queryByTestId('wtl-visit-status')).not.toBeInTheDocument();
  });

  it('showInactive=true なら連動取消も打ち消し線つきで描く (残骸点検・§3-4)', () => {
    render(
      <WeekTimelineBoard
        showInactive
        options={OPTIONS}
        visits={[
          wv({
            id: 'status',
            weekday: 2,
            source: 'status_cancel',
            status: 'cancelled',
            patient_status: 'admitted',
          }),
        ]}
      />,
    );
    const card = screen.getByTestId('wtl-visit-status');
    expect(card.className).toContain('line-through');
    expect(screen.getByTestId('wtl-inactive-badge-status')).toHaveTextContent('取消（連動）');
  });

  it('非稼働患者の予定が残っていたらバッジ + 薄色で見せる (トグル OFF でも・§3-4)', () => {
    render(
      <WeekTimelineBoard
        options={OPTIONS}
        visits={[
          wv({
            id: 'residue',
            weekday: 0,
            source: 'auto',
            status: 'planned',
            patient_status: 'suspended',
          }),
        ]}
      />,
    );
    const card = screen.getByTestId('wtl-visit-residue');
    expect(card.className).toContain('opacity-60');
    expect(screen.getByTestId('wtl-inactive-badge-residue')).toHaveTextContent('一時休止');
  });

  it('status が欠落していれば描く (source だけでは取り消し扱いにしない)', () => {
    render(
      <WeekTimelineBoard
        options={OPTIONS}
        visits={[wv({ id: 'odd', weekday: 0, source: 'status_cancel' })]}
      />,
    );
    expect(screen.getByTestId('wtl-visit-odd')).toBeInTheDocument();
  });
});
