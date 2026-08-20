/**
 * WeekCoursePalette — 「コースの表」(週空間 A1・weekly-space-design.md §4-1)。
 *
 * ① 曜日グループにカードが出る。未割当は強調 + 「未割当」、割当済は担当名表示
 * ② カードの dragstart で payload (courseId, weekday) が積まれ onDragChange が飛ぶ
 * ③ パレットへのドロップで onUnassignDrop(courseId) が飛ぶ (= 担当解除)
 * ④ canEdit=false ではドラッグ不可
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import {
  COURSE_DND_MIME,
  WeekCoursePalette,
  type PaletteCourse,
} from '../WeekCoursePalette';

const C_MON_A = '00000000-0000-4000-8000-00000000c0a0';
const C_MON_B = '00000000-0000-4000-8000-00000000c0b0';
const STAFF_1 = '00000000-0000-4000-8000-000000000001';

const courses: PaletteCourse[] = [
  {
    id: C_MON_A,
    weekday: 0,
    label: '稲毛A',
    assignedStaffId: null,
    assignedStaffName: null,
    visitCount: 5,
    totalMinutes: 175,
    timeRange: '09:00〜15:30',
  },
  {
    id: C_MON_B,
    weekday: 0,
    label: '稲毛B',
    assignedStaffId: STAFF_1,
    assignedStaffName: '熊澤　花子',
    visitCount: 4,
    totalMinutes: 140,
    timeRange: '09:30〜14:00',
  },
];

const makeDataTransfer = (payload: object | null) => ({
  types: payload ? [COURSE_DND_MIME] : [],
  getData: (t: string) => (payload && t === COURSE_DND_MIME ? JSON.stringify(payload) : ''),
  setData: vi.fn(),
  dropEffect: '',
  effectAllowed: '',
});

describe('WeekCoursePalette', () => {
  it('① 曜日グループにカード表示: 未割当は「未割当」・割当済は担当名', () => {
    render(<WeekCoursePalette courses={courses} canEdit />);
    const cardA = screen.getByTestId(`palette-course-${C_MON_A}`);
    expect(within(cardA).getByText(/稲毛A/)).toBeInTheDocument();
    expect(within(cardA).getByText('未割当')).toBeInTheDocument();
    expect(within(cardA).getByText(/5件・175分・09:00〜15:30/)).toBeInTheDocument();
    const cardB = screen.getByTestId(`palette-course-${C_MON_B}`);
    expect(within(cardB).getByText(/熊澤/)).toBeInTheDocument();
    expect(cardB).toHaveAttribute('data-assigned', 'true');
    // ヘッダの未割当件数
    expect(screen.getByText(/未割当 1 件/)).toBeInTheDocument();
  });

  it('② dragstart で payload が積まれ onDragChange が飛ぶ', () => {
    const onDragChange = vi.fn();
    render(<WeekCoursePalette courses={courses} canEdit onDragChange={onDragChange} />);
    const card = screen.getByTestId(`palette-course-${C_MON_A}`);
    expect(card).toHaveAttribute('draggable', 'true');
    const dt = makeDataTransfer(null);
    fireEvent.dragStart(card, { dataTransfer: dt });
    expect(dt.setData).toHaveBeenCalledWith(
      COURSE_DND_MIME,
      JSON.stringify({ courseId: C_MON_A, weekday: 0 }),
    );
    expect(onDragChange).toHaveBeenCalledWith({ courseId: C_MON_A, weekday: 0 });
    fireEvent.dragEnd(card, { dataTransfer: dt });
    expect(onDragChange).toHaveBeenLastCalledWith(null);
  });

  it('③ パレットへのドロップで onUnassignDrop が飛ぶ', () => {
    const onUnassignDrop = vi.fn();
    render(<WeekCoursePalette courses={courses} canEdit onUnassignDrop={onUnassignDrop} />);
    fireEvent.drop(screen.getByTestId('week-course-palette'), {
      dataTransfer: makeDataTransfer({ courseId: C_MON_B, weekday: 0 }),
    });
    expect(onUnassignDrop).toHaveBeenCalledWith(C_MON_B);
  });

  it('⑤ activeDrag: 掴んでいるカードが半透明+破線になり、戻し先案内が強調される', () => {
    render(
      <WeekCoursePalette
        courses={courses}
        canEdit
        onUnassignDrop={vi.fn()}
        activeDrag={{ courseId: C_MON_A, weekday: 0 }}
      />,
    );
    const card = screen.getByTestId(`palette-course-${C_MON_A}`);
    expect(card.className).toContain('opacity-40');
    expect(screen.getByText('⤵ ここへ戻すと担当解除（今週のみ）')).toBeInTheDocument();
  });

  it('④ canEdit=false: カードはドラッグ不可・ドロップも無視', () => {
    const onUnassignDrop = vi.fn();
    render(<WeekCoursePalette courses={courses} canEdit={false} onUnassignDrop={onUnassignDrop} />);
    expect(screen.getByTestId(`palette-course-${C_MON_A}`)).toHaveAttribute('draggable', 'false');
    fireEvent.drop(screen.getByTestId('week-course-palette'), {
      dataTransfer: makeDataTransfer({ courseId: C_MON_B, weekday: 0 }),
    });
    expect(onUnassignDrop).not.toHaveBeenCalled();
  });
});
