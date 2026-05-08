/**
 * Wave 30: CourseDayTable の formatEventLabel ヘルパーテスト.
 *
 * カバーするシナリオ:
 *   1. title ありの event は "種別: タイトル HH:MM-HH:MM" 形式になる
 *   2. title が空文字の event は "種別 HH:MM-HH:MM" 形式 (title falsy 扱い)
 *   3. event-slot-row に title が含まれる (CourseDayTable 統合)
 *   4. event-slot-row に title なしの場合は "種別 HH:MM-HH:MM" 形式になる
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

import { formatEventLabel, CourseDayTable } from '../CourseDayTable';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { EventRead } from '@/lib/schemas/staff-events';

// ─────────────────────────────────────────────────────────────────────────
// Unit tests for formatEventLabel helper
// ─────────────────────────────────────────────────────────────────────────

describe('formatEventLabel (Wave 30)', () => {
  it('1. title ありの event は "種別: タイトル HH:MM-HH:MM" 形式になる', () => {
    const result = formatEventLabel({
      type: '研修',
      title: '接遇マナー',
      start_time: '14:00',
      end_time: '16:00',
    });
    expect(result).toBe('研修: 接遇マナー 14:00-16:00');
  });

  it('2. title が空文字の event は "種別 HH:MM-HH:MM" 形式になる', () => {
    const result = formatEventLabel({
      type: '研修',
      title: '',
      start_time: '14:00',
      end_time: '16:00',
    });
    expect(result).toBe('研修 14:00-16:00');
  });

  it('2b. title が null の event は "種別 HH:MM-HH:MM" 形式になる', () => {
    const result = formatEventLabel({
      type: 'イベント',
      title: null,
      start_time: '09:00',
      end_time: '10:00',
    });
    expect(result).toBe('イベント 09:00-10:00');
  });

  it('2c. title が undefined の event は "種別 HH:MM-HH:MM" 形式になる', () => {
    const result = formatEventLabel({
      type: '会議',
      start_time: '13:00',
      end_time: '14:00',
    });
    expect(result).toBe('会議 13:00-14:00');
  });
});

// ─────────────────────────────────────────────────────────────────────────
// CourseDayTable 統合テスト — title 付き event 表示
// ─────────────────────────────────────────────────────────────────────────

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
  date: '2026-05-04',
  title: '接遇マナー',
  start_time: '10:00',
  end_time: '12:00',
  type: '研修',
  ...overrides,
});

function renderTable(
  opts: {
    eventOverrides?: Partial<EventRead>;
  } = {},
) {
  const event = makeEvent(opts.eventOverrides ?? {});
  return render(
    <CourseDayTable
      weekday={0}
      template={baseTpl}
      course={
        {
          id: 'c-1',
          assigned_staff_id: 'staff-1',
          weekday: 0,
          iso_year: 2026,
          iso_week: 19,
        } as unknown as Parameters<typeof CourseDayTable>[0]['course']
      }
      officeName="本店"
      visits={[]}
      staffOptions={[]}
      staffEventsByStaff={new Map([['staff-1', [event]]])}
      canEdit={false}
      onChangeAssignedStaff={vi.fn()}
      isStaffMutating={false}
    />,
  );
}

describe('CourseDayTable event-slot-row に title 表示 (Wave 30)', () => {
  it('3. event-slot-row に title が含まれる形式で表示される', () => {
    renderTable({
      eventOverrides: { title: '接遇マナー', type: '研修', start_time: '10:00', end_time: '12:00' },
    });
    const rows = screen.getAllByTestId('event-slot-row');
    expect(rows.length).toBeGreaterThan(0);
    expect(rows[0]).toHaveTextContent('研修: 接遇マナー 10:00-12:00');
  });

  it('4. title なし (空文字) の event は "種別 HH:MM-HH:MM" 形式で表示される', () => {
    renderTable({
      eventOverrides: { title: '', type: '研修', start_time: '10:00', end_time: '12:00' },
    });
    const rows = screen.getAllByTestId('event-slot-row');
    expect(rows.length).toBeGreaterThan(0);
    expect(rows[0]).toHaveTextContent('研修 10:00-12:00');
    expect(rows[0]).not.toHaveTextContent('研修:');
  });
});
