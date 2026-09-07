/**
 * WeekTimelineBoard — 列の dnd-kit droppable 化 (Phase 2)
 * (`docs/plans/dnd-all-views-design-2026-09-08.md` §2-1 / §4)。
 *
 * 検証:
 *   1. `dndEnabled` のとき (コース × 曜日) の全列が `wtl-col:{templateId}:{weekday}`
 *      の droppable として登録される
 *   2. isOver の列だけが光る (重なっているのはどこかを見せる)
 *   3. `dndEnabled=false` (既定) では droppable を 1 つも作らない
 *      — 週タイムラインは他の呼び出し元では読み取り専用のまま
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const { dnd } = vi.hoisted(() => ({
  dnd: {
    /** useDroppable に渡された引数 (id / data) を全部記録する。 */
    calls: [] as { id: string; data?: Record<string, unknown> }[],
    /** isOver を立てる droppable id (ハイライト検証用)。 */
    overId: null as string | null,
  },
}));

vi.mock('@dnd-kit/core', () => ({
  useDroppable: ({ id, data }: { id: string; data?: Record<string, unknown> }) => {
    dnd.calls.push({ id, data });
    return { isOver: dnd.overId === id, setNodeRef: vi.fn(), over: null, node: { current: null } };
  },
}));

import {
  WeekTimelineBoard,
  type WeekTimelineOption,
} from '@/components/schedule/timeline/WeekTimelineBoard';
import { buildWeekTimelineColDroppableId } from '@/components/schedule/v2/courseDnd';

const TPL_A = '00000000-0000-4000-8000-0000000000aa';
const TPL_B = '00000000-0000-4000-8000-0000000000bb';

const OPTIONS: WeekTimelineOption[] = [
  { templateId: TPL_A, label: '稲毛A・田中 一郎' },
  { templateId: TPL_B, label: '稲毛B・佐藤 花子' },
];

beforeEach(() => {
  dnd.calls = [];
  dnd.overId = null;
});

describe('WeekTimelineBoard — wtl-col droppable', () => {
  it('dndEnabled のとき 全 (コース × 曜日) 列が wtl-col: の droppable になる', () => {
    render(<WeekTimelineBoard options={OPTIONS} visits={[]} dndEnabled />);
    const ids = dnd.calls.map((c) => c.id);
    for (const tpl of [TPL_A, TPL_B]) {
      for (let wd = 0; wd < 6; wd++) {
        expect(ids).toContain(buildWeekTimelineColDroppableId(tpl, wd));
      }
    }
    expect(ids).toHaveLength(12);
  });

  it('droppable の data に templateId / weekday を載せる', () => {
    render(<WeekTimelineBoard options={OPTIONS} visits={[]} dndEnabled />);
    const call = dnd.calls.find((c) => c.id === buildWeekTimelineColDroppableId(TPL_A, 3));
    expect(call?.data).toEqual({ templateId: TPL_A, weekday: 3, open: true });
  });

  it('isOver の列だけが強調される', () => {
    dnd.overId = buildWeekTimelineColDroppableId(TPL_A, 2);
    render(<WeekTimelineBoard options={OPTIONS} visits={[]} dndEnabled />);
    const over = screen.getByTestId(`wtl-col-drop-${TPL_A}-2`);
    const other = screen.getByTestId(`wtl-col-drop-${TPL_A}-3`);
    expect(over.getAttribute('data-dnd-over')).toBe('true');
    expect(over.className).toContain('ring-brand-primary/60');
    expect(other.getAttribute('data-dnd-over')).toBe('false');
    expect(other.className).not.toContain('ring-brand-primary/60');
  });

  // BE は定員 0 の曜日でも Course を作ってしまうので、休を弾く材料は FE が載せる。
  it('休 (未開講) の列は data.open=false を載せ、重なっても brand 色で光らせない', () => {
    dnd.overId = buildWeekTimelineColDroppableId(TPL_A, 1);
    render(
      <WeekTimelineBoard
        options={[OPTIONS[0]!]}
        visits={[]}
        // 火 (1) だけ休。
        courseOpenByWeekday={(_tid, wd) => wd !== 1}
        dndEnabled
      />,
    );
    const closed = dnd.calls.find((c) => c.id === buildWeekTimelineColDroppableId(TPL_A, 1));
    const open = dnd.calls.find((c) => c.id === buildWeekTimelineColDroppableId(TPL_A, 0));
    expect(closed?.data).toEqual({ templateId: TPL_A, weekday: 1, open: false });
    expect(open?.data).toEqual({ templateId: TPL_A, weekday: 0, open: true });
    const layer = screen.getByTestId(`wtl-col-drop-${TPL_A}-1`);
    expect(layer.getAttribute('data-drop-open')).toBe('false');
    // 重なっていても受け皿ではないので brand 色にしない (期待させない)。
    expect(layer.getAttribute('data-dnd-over')).toBe('true');
    expect(layer.className).not.toContain('ring-brand-primary/60');
  });

  // 既に訪問がある列を「休」と言って触らせないのは事実と食い違う (週リストの isRest と同じ和集合)。
  it('休でも訪問が既にある列は開講扱い', () => {
    render(
      <WeekTimelineBoard
        options={[OPTIONS[0]!]}
        visits={[
          {
            id: 'v1',
            patient_id: 'p1',
            patient_name: '朝倉 美夢',
            weekday: 1,
            course_template_id: TPL_A,
            start_time: '09:30:00',
            end_time: '10:00:00',
          } as never,
        ]}
        courseOpenByWeekday={() => false}
        dndEnabled
      />,
    );
    const withVisit = dnd.calls.find((c) => c.id === buildWeekTimelineColDroppableId(TPL_A, 1));
    const empty = dnd.calls.find((c) => c.id === buildWeekTimelineColDroppableId(TPL_A, 2));
    expect(withVisit?.data).toMatchObject({ open: true });
    expect(empty?.data).toMatchObject({ open: false });
  });

  it('courseOpenByWeekday 未指定なら全て開講扱い (表示専用の呼び出し元に影響しない)', () => {
    render(<WeekTimelineBoard options={[OPTIONS[0]!]} visits={[]} dndEnabled />);
    expect(dnd.calls.every((c) => c.data?.open === true)).toBe(true);
  });

  it('dndEnabled 未指定 (既定 false) では droppable を作らない = 読み取り専用のまま', () => {
    render(<WeekTimelineBoard options={OPTIONS} visits={[]} />);
    expect(dnd.calls).toHaveLength(0);
    expect(screen.queryByTestId(`wtl-col-drop-${TPL_A}-0`)).toBeNull();
    // 列そのもの (表示) は従来どおり出る。
    expect(screen.getByTestId(`wtl-col-${TPL_A}-0`)).toBeInTheDocument();
  });
});
