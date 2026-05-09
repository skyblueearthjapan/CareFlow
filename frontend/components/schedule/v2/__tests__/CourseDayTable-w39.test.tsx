/**
 * Wave 39: CourseDayTable のスタッフイベント「rowSpan 1 ブロック表示」+
 * 「draggable 化」のテスト.
 *
 * カバーするシナリオ:
 *   W39-1. 60min event は rowSpan=4 で 1 ブロック描画される (15min = 4 行)
 *   W39-2. 30min event は rowSpan=2
 *   W39-3. 45min event は rowSpan=3
 *   W39-4. 15min event は rowSpan=1
 *   W39-5. eventDraggableId / parseEventDraggableId のラウンドトリップ
 *   W39-6. parseEventDraggableId は visit:* / pool-patient:* を弾く
 *   W39-7. 担当未割当のセルでは event ブロックが描画されない
 *   W39-8. 同 staff の複数 event が並走しても各々 1 ブロックずつ描画される
 *   W39-9. event の grid-row 開始位置 (rowIndex+2) が 09:30 起点で計算される
 *   W39-10. 担当者の event は data-event-start-time / data-event-row-span 属性を持つ
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('@dnd-kit/core', () => ({
  useDraggable: () => ({
    attributes: {},
    listeners: {},
    setNodeRef: () => {},
    isDragging: false,
  }),
  useDroppable: () => ({
    isOver: false,
    setNodeRef: () => {},
  }),
}));

vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

import { CourseDayTable, eventDraggableId, parseEventDraggableId } from '../CourseDayTable';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { EventRead } from '@/lib/schemas/staff-events';

const baseTpl: CourseTemplateRead = {
  id: 'tpl-1',
  label: 'A',
  office_id: 'o1',
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
} as unknown as CourseTemplateRead;

const makeEvent = (overrides: Partial<EventRead> = {}): EventRead => ({
  id: 'event-1',
  date: '2026-05-04', // 月曜
  title: '研修',
  start_time: '10:00',
  end_time: '11:00',
  type: '研修',
  ...overrides,
});

function renderTable(
  opts: {
    assignedStaffId?: string | null;
    events?: EventRead[];
  } = {},
) {
  const { assignedStaffId = 'staff-1', events = [makeEvent()] } = opts;
  return render(
    <CourseDayTable
      weekday={0}
      template={baseTpl}
      course={
        {
          id: 'c-1',
          assigned_staff_id: assignedStaffId,
          weekday: 0,
          iso_year: 2026,
          iso_week: 19,
        } as unknown as Parameters<typeof CourseDayTable>[0]['course']
      }
      officeName="本店"
      visits={[]}
      staffOptions={[]}
      staffEventsByStaff={new Map([['staff-1', events]])}
      canEdit={true}
      onChangeAssignedStaff={vi.fn()}
      isStaffMutating={false}
    />,
  );
}

describe('CourseDayTable W39: event rowSpan 1 ブロック表示', () => {
  it('W39-1. 60min event (10:00-11:00) は rowSpan=4 で 1 ブロック描画される', () => {
    renderTable({ events: [makeEvent({ start_time: '10:00', end_time: '11:00' })] });
    const block = screen.getByTestId('event-block-event-1');
    expect(block.getAttribute('data-event-row-span')).toBe('4');
    expect(block.style.gridRow).toContain('span 4');
  });

  it('W39-2. 30min event (10:00-10:30) は rowSpan=2', () => {
    renderTable({
      events: [makeEvent({ id: 'ev-30', start_time: '10:00', end_time: '10:30' })],
    });
    const block = screen.getByTestId('event-block-ev-30');
    expect(block.getAttribute('data-event-row-span')).toBe('2');
  });

  it('W39-3. 45min event (10:00-10:45) は rowSpan=3', () => {
    renderTable({
      events: [makeEvent({ id: 'ev-45', start_time: '10:00', end_time: '10:45' })],
    });
    const block = screen.getByTestId('event-block-ev-45');
    expect(block.getAttribute('data-event-row-span')).toBe('3');
  });

  it('W39-4. 15min event (10:00-10:15) は rowSpan=1', () => {
    renderTable({
      events: [makeEvent({ id: 'ev-15', start_time: '10:00', end_time: '10:15' })],
    });
    const block = screen.getByTestId('event-block-ev-15');
    expect(block.getAttribute('data-event-row-span')).toBe('1');
  });

  it('W39-7. 担当未割当のセルでは event ブロックが描画されない', () => {
    renderTable({ assignedStaffId: null });
    expect(screen.queryByTestId('event-block-event-1')).not.toBeInTheDocument();
  });

  it('W39-8. 同 staff の複数 event はそれぞれ 1 ブロックずつ描画される', () => {
    renderTable({
      events: [
        makeEvent({ id: 'ev-A', start_time: '10:00', end_time: '11:00' }),
        makeEvent({ id: 'ev-B', start_time: '14:00', end_time: '14:30' }),
      ],
    });
    const a = screen.getByTestId('event-block-ev-A');
    const b = screen.getByTestId('event-block-ev-B');
    expect(a.getAttribute('data-event-row-span')).toBe('4');
    expect(b.getAttribute('data-event-row-span')).toBe('2');
  });

  it('W39-9. event の grid-row は 09:30 起点で +2 オフセット (column header 行 + 1-based)', () => {
    // 10:00 は TIME_SLOTS index 2 (09:30=0, 09:45=1, 10:00=2) → grid-row = 4 / span N
    renderTable({ events: [makeEvent({ start_time: '10:00', end_time: '11:00' })] });
    const block = screen.getByTestId('event-block-event-1');
    expect(block.style.gridRow).toContain('4 /');
  });

  it('W39-10. event-block は data-event-* 属性を持つ', () => {
    renderTable({
      events: [makeEvent({ id: 'ev-meta', start_time: '13:30', end_time: '14:30' })],
    });
    const block = screen.getByTestId('event-block-ev-meta');
    expect(block.getAttribute('data-event-id')).toBe('ev-meta');
    expect(block.getAttribute('data-event-start-time')).toBe('13:30');
    expect(block.getAttribute('data-event-row-span')).toBe('4');
  });
});

describe('CourseDayTable W39: eventDraggableId helpers', () => {
  it('W39-5. eventDraggableId / parseEventDraggableId のラウンドトリップ', () => {
    const raw = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
    const id = eventDraggableId(raw);
    expect(id).toBe(`event:${raw}`);
    expect(parseEventDraggableId(id)).toBe(raw);
  });

  it('W39-6. parseEventDraggableId は visit:* / pool-patient:* / その他を null で弾く', () => {
    expect(parseEventDraggableId('visit:abc')).toBeNull();
    expect(parseEventDraggableId('pool-patient:abc')).toBeNull();
    expect(parseEventDraggableId('course-day-cell:0:tpl:10:00')).toBeNull();
    expect(parseEventDraggableId('random-string')).toBeNull();
  });
});
