/**
 * Wave 28 Phase B-1: CourseDayTable のセル内 event 表示テスト.
 *
 * カバーするシナリオ:
 *   1. eventTypeLabel — 日本語 type はそのまま返る
 *   2. eventTypeLabel — DB snake_case は日本語に変換される
 *   3. eventTypeLabel — 未知の type はそのまま返る
 *   4. EVENT_TYPE_LABEL — 全 DB キーが登録されている
 *   5. 空スロットに event があれば event-slot-row が描画される (CourseDayTable 統合)
 *   6. visit があるスロットに event があれば event-slot-row が描画される
 *   7. 担当スタッフが未設定の場合は event-slot-row が描画されない
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

// ─── dnd-kit mock ────────────────────────────────────────────────────────
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

import { eventTypeLabel, EVENT_TYPE_LABEL, CourseDayTable } from '../CourseDayTable';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { EventRead } from '@/lib/schemas/staff-events';

// ─────────────────────────────────────────────────────────────────────────
// Unit tests for eventTypeLabel helper
// ─────────────────────────────────────────────────────────────────────────

describe('eventTypeLabel (Wave 28 B-4)', () => {
  it('1. 日本語 type "研修" はそのまま返る', () => {
    expect(eventTypeLabel('研修')).toBe('研修');
  });

  it('2. DB snake_case "training" は "研修" に変換される', () => {
    expect(eventTypeLabel('training')).toBe('研修');
  });

  it('2b. "meeting" → "会議"', () => {
    expect(eventTypeLabel('meeting')).toBe('会議');
  });

  it('2c. "errand" → "役所同行"', () => {
    expect(eventTypeLabel('errand')).toBe('役所同行');
  });

  it('3. 未知の type はそのまま返る', () => {
    expect(eventTypeLabel('unknown_type')).toBe('unknown_type');
  });
});

describe('EVENT_TYPE_LABEL (Wave 28 B-4)', () => {
  it('4. DB の全 snake_case キーが登録されている', () => {
    const DB_KEYS = ['training', 'meeting', 'event', 'errand', 'interview', 'office', 'other'];
    for (const key of DB_KEYS) {
      expect(EVENT_TYPE_LABEL).toHaveProperty(key);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────
// CourseDayTable 統合テスト — event-slot-row 表示
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
  date: '2026-05-04', // 月曜
  title: '研修',
  start_time: '10:00',
  end_time: '12:00',
  type: '研修',
  ...overrides,
});

function renderTable(
  opts: {
    assignedStaffId?: string | null;
    staffEventsByStaff?: Map<string, EventRead[]>;
    visits?: Parameters<typeof CourseDayTable>[0]['visits'];
  } = {},
) {
  const {
    assignedStaffId = 'staff-1',
    staffEventsByStaff = new Map([['staff-1', [makeEvent()]]]),
    visits = [],
  } = opts;

  return render(
    <CourseDayTable
      weekday={0} // 月曜
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
      visits={visits}
      staffOptions={[]}
      staffEventsByStaff={staffEventsByStaff}
      canEdit={false}
      onChangeAssignedStaff={vi.fn()}
      isStaffMutating={false}
    />,
  );
}

describe('CourseDayTable セル内 event 表示 (Wave 28 B-1)', () => {
  it('5. 空スロット (visits なし) にスタッフの event があれば event-slot-row が描画される', () => {
    renderTable();
    // 10:00〜12:00 の event → 10:00, 10:15, 10:30, 10:45, 11:00, 11:15, 11:30, 11:45 の各スロットに表示
    const rows = screen.getAllByTestId('event-slot-row');
    expect(rows.length).toBeGreaterThan(0);
    expect(rows[0]).toHaveTextContent('研修');
    expect(rows[0]).toHaveTextContent('10:00-12:00');
  });

  it('6. visit があるスロットにも event-slot-row が描画される', () => {
    renderTable({
      visits: [
        {
          id: 'v-1',
          patient_id: 'p-1',
          patient_name: '田中',
          patient_address: '東京都',
          patient_requires_multiple_staff: false,
          patient_sex_restriction_label: null,
          required_staff_count: 1,
          start_slot: '10:00',
        },
      ],
    });
    expect(screen.getByTestId('course-occupant-name-v-1')).toBeInTheDocument();
    expect(screen.getAllByTestId('event-slot-row').length).toBeGreaterThan(0);
  });

  it('7. 担当スタッフ未設定の場合は event-slot-row が描画されない', () => {
    renderTable({ assignedStaffId: null });
    expect(screen.queryByTestId('event-slot-row')).not.toBeInTheDocument();
  });
});
