/**
 * StaffWeekBoard — セルの dnd-kit droppable 化
 * (`docs/plans/dnd-all-views-design-2026-09-08.md` §2-1 / §4)。
 *
 * 検証:
 *   1. 全セルが `sw-cell:{rowKey}:{weekday}` の droppable として登録される
 *      (「（担当なし）」行も含む = ⭐/プールカードの受け皿になる)
 *   2. isOver でセルが光る (重なっているのはどこかを見せる)
 *   3. **回帰**: ブラウザ標準 DnD (コース帯 / 訪問帯の付け替え) は従来どおり動く
 *      — dnd-kit の droppable を足しても標準 drop を食わない
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

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

import { StaffWeekBoard } from '../StaffWeekBoard';
import { COURSE_DND_MIME, VISIT_DND_MIME, buildStaffWeekCellDroppableId } from '../courseDnd';
import type { WeekOverviewVisit } from '../CourseWeekOverview';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { StaffRead } from '@/lib/schemas/staff';

const WEEK_START = new Date(2026, 6, 20); // 2026-07-20 (月)
const OFFICE_ID = '00000000-0000-4000-8000-00000000000f';
const TPL_A = '00000000-0000-4000-8000-0000000000aa';
const STAFF_1 = '00000000-0000-4000-8000-000000000001';
const COURSE_ID = '00000000-0000-4000-8000-00000000c001';

const templates = [
  { id: TPL_A, office_id: OFFICE_ID, label: 'A' },
] as unknown as CourseTemplateRead[];

const staffMap = new Map<string, StaffRead>([
  [
    STAFF_1,
    {
      id: STAFF_1,
      name: '宇田川　優莉',
      primary_office_id: OFFICE_ID,
      is_trainee: false,
      status: 'active',
    } as unknown as StaffRead,
  ],
]);

const visits = [
  {
    id: 'v1',
    patient_id: 'p1',
    patient_name: '朝倉　美夢',
    weekday: 0,
    course_template_id: TPL_A,
    start_time: '09:00',
    end_time: '09:35',
  },
] as unknown as WeekOverviewVisit[];

const assigned = new Map<string, string>([[`${TPL_A}:0`, STAFF_1]]);

const makeDataTransfer = (payload: object, mime: string = COURSE_DND_MIME) => ({
  types: [mime],
  getData: (t: string) => (t === mime ? JSON.stringify(payload) : ''),
  setData: vi.fn(),
  dropEffect: '',
  effectAllowed: '',
});

function renderBoard(props: Partial<React.ComponentProps<typeof StaffWeekBoard>> = {}) {
  return render(
    <StaffWeekBoard
      templates={templates}
      officeNameById={new Map([[OFFICE_ID, '稲毛']])}
      visits={visits}
      assignedStaffByTemplateWeekday={assigned}
      staffMap={staffMap}
      staffEventsByStaff={new Map()}
      weekStart={WEEK_START}
      showAllStaff
      alwaysShowUnassignedRow
      {...props}
    />,
  );
}

beforeEach(() => {
  dnd.calls = [];
  dnd.overId = null;
});

describe('StaffWeekBoard — sw-cell droppable', () => {
  it('全セルが sw-cell:{rowKey}:{weekday} の droppable になる', () => {
    renderBoard();
    const ids = dnd.calls.map((c) => c.id);
    for (let wd = 0; wd < 6; wd++) {
      expect(ids).toContain(buildStaffWeekCellDroppableId(STAFF_1, wd));
      // 「（担当なし）」行も受け皿 (プール/⭐ を担当なし枠へ入れられる)。
      expect(ids).toContain(buildStaffWeekCellDroppableId('__unassigned__', wd));
    }
  });

  it('droppable の data に rowKey / weekday を載せる', () => {
    renderBoard();
    const call = dnd.calls.find((c) => c.id === buildStaffWeekCellDroppableId(STAFF_1, 3));
    expect(call?.data).toEqual({ rowKey: STAFF_1, weekday: 3 });
  });

  it('isOver のセルだけが強調される', () => {
    dnd.overId = buildStaffWeekCellDroppableId(STAFF_1, 2);
    renderBoard();
    const over = screen.getByTestId(`staff-week-cell-${STAFF_1}-2`);
    const other = screen.getByTestId(`staff-week-cell-${STAFF_1}-3`);
    expect(over.getAttribute('data-dnd-over')).toBe('true');
    expect(over.className).toContain('outline-brand-primary');
    expect(other.getAttribute('data-dnd-over')).toBe('false');
    expect(other.className).not.toContain('outline-brand-primary');
  });

  // ─── 回帰: ブラウザ標準 DnD (コース帯 / 訪問帯) ─────────────────────────

  it('コース帯の標準ドロップは従来どおり onCourseDrop へ飛ぶ', () => {
    const onCourseDrop = vi.fn();
    renderBoard({ onCourseDrop });
    fireEvent.drop(screen.getByTestId(`staff-week-cell-${STAFF_1}-1`), {
      dataTransfer: makeDataTransfer({ courseId: COURSE_ID, weekday: 0 }),
    });
    expect(onCourseDrop).toHaveBeenCalledWith(COURSE_ID, STAFF_1, 1);
  });

  it('訪問帯の標準ドロップは従来どおり onVisitDrop へ飛ぶ', () => {
    const onVisitDrop = vi.fn();
    renderBoard({ onVisitDrop });
    fireEvent.drop(screen.getByTestId(`staff-week-cell-${STAFF_1}-4`), {
      dataTransfer: makeDataTransfer({ visitId: 'v1', weekday: 0 }, VISIT_DND_MIME),
    });
    expect(onVisitDrop).toHaveBeenCalledWith('v1', STAFF_1, 4);
  });
});
