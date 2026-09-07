/**
 * CourseWeekOverview — セルの dnd-kit droppable 化 (Phase 2)
 * (`docs/plans/dnd-all-views-design-2026-09-08.md` §2-1 / §4)。
 *
 * 検証:
 *   1. `dndEnabled` のとき 全 (template × weekday) セルが
 *      `cwo-cell:{templateId}:{weekday}` の droppable として登録される
 *   2. isOver のセルだけが光る
 *   3. `dndEnabled=false` (既定) では droppable を 1 つも作らない
 *      — 週リストは他の呼び出し元では読み取り専用のまま
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const { dnd } = vi.hoisted(() => ({
  dnd: {
    calls: [] as { id: string; data?: Record<string, unknown> }[],
    overId: null as string | null,
  },
}));

vi.mock('@dnd-kit/core', () => ({
  useDroppable: ({ id, data }: { id: string; data?: Record<string, unknown> }) => {
    dnd.calls.push({ id, data });
    return { isOver: dnd.overId === id, setNodeRef: vi.fn(), over: null, node: { current: null } };
  },
}));

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
import { buildWeekOverviewCellDroppableId } from '../courseDnd';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';

const TPL_A = '00000000-0000-4000-8000-0000000000aa';
const TPL_B = '00000000-0000-4000-8000-0000000000bb';

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

const templates = [
  { id: TPL_A, label: 'A', office_id: 'o1', ...baseTpl },
  { id: TPL_B, label: 'B', office_id: 'o1', ...baseTpl },
] as unknown as CourseTemplateRead[];

/** A-E を全開講させる十分なスタッフ数 (=5)。 */
const fullStaff = () => 5;

function renderOverview(props: Partial<React.ComponentProps<typeof CourseWeekOverview>> = {}) {
  return render(
    <CourseWeekOverview
      templates={templates}
      officeNameById={new Map([['o1', '本店']])}
      visits={[]}
      onJumpToDay={vi.fn()}
      staffCountFor={fullStaff}
      {...props}
    />,
  );
}

beforeEach(() => {
  dnd.calls = [];
  dnd.overId = null;
});

describe('CourseWeekOverview — cwo-cell droppable', () => {
  it('dndEnabled のとき 全 (template × weekday) セルが cwo-cell: の droppable になる', () => {
    renderOverview({ dndEnabled: true });
    const ids = dnd.calls.map((c) => c.id);
    for (const tpl of [TPL_A, TPL_B]) {
      for (let wd = 0; wd < 6; wd++) {
        expect(ids).toContain(buildWeekOverviewCellDroppableId(tpl, wd));
      }
    }
    expect(ids).toHaveLength(12);
  });

  it('droppable の data に templateId / weekday を載せる', () => {
    renderOverview({ dndEnabled: true });
    const call = dnd.calls.find((c) => c.id === buildWeekOverviewCellDroppableId(TPL_B, 5));
    expect(call?.data).toEqual({ templateId: TPL_B, weekday: 5, open: true });
  });

  it('isOver のセルだけが強調される', () => {
    dnd.overId = buildWeekOverviewCellDroppableId(TPL_A, 1);
    renderOverview({ dndEnabled: true });
    const over = screen.getByTestId(`cwo-cell-drop-${TPL_A}-1`);
    const other = screen.getByTestId(`cwo-cell-drop-${TPL_A}-2`);
    expect(over.getAttribute('data-dnd-over')).toBe('true');
    expect(over.className).toContain('outline-brand-primary');
    expect(other.getAttribute('data-dnd-over')).toBe('false');
    expect(other.className).not.toContain('outline-brand-primary');
  });

  // BE は定員 0 の曜日でも Course を作ってしまうので、休を弾く材料は FE が載せる。
  it('休 (isRest) のセルは data.open=false を載せ、重なっても brand 色で光らせない', () => {
    // staffCountFor=0 → A/B とも実効定員 0・PFV も visit も無い = 全セル「休」。
    dnd.overId = buildWeekOverviewCellDroppableId(TPL_A, 0);
    renderOverview({ dndEnabled: true, staffCountFor: () => 0 });
    expect(screen.getByTestId(`course-week-overview-cell-${TPL_A}-0`)).toHaveTextContent('休');
    const call = dnd.calls.find((c) => c.id === buildWeekOverviewCellDroppableId(TPL_A, 0));
    expect(call?.data).toEqual({ templateId: TPL_A, weekday: 0, open: false });
    const layer = screen.getByTestId(`cwo-cell-drop-${TPL_A}-0`);
    expect(layer.getAttribute('data-drop-open')).toBe('false');
    expect(layer.getAttribute('data-dnd-over')).toBe('true');
    expect(layer.className).not.toContain('outline-brand-primary');
  });

  it('開講しているセルは data.open=true', () => {
    renderOverview({ dndEnabled: true });
    const call = dnd.calls.find((c) => c.id === buildWeekOverviewCellDroppableId(TPL_A, 0));
    expect(call?.data).toEqual({ templateId: TPL_A, weekday: 0, open: true });
  });

  it('dndEnabled 未指定 (既定 false) では droppable を作らない = 読み取り専用のまま', () => {
    renderOverview();
    expect(dnd.calls).toHaveLength(0);
    expect(screen.queryByTestId(`cwo-cell-drop-${TPL_A}-0`)).toBeNull();
    // セルそのもの (表示) は従来どおり出る。
    expect(screen.getByTestId(`course-week-overview-cell-${TPL_A}-0`)).toBeInTheDocument();
  });
});
