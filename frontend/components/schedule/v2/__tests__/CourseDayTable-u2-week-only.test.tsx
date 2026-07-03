/**
 * Wave U-2: CourseDayTable「今週のみ」チップ (source='manual_week') の表示と昇格導線テスト.
 *
 * カバーするシナリオ:
 *   1. source='manual_week' → 「今週のみ」チップが表示される
 *   2. source='manual' / 欠落 → チップは非表示 (寛容)
 *   3. canEdit=true でチップクリック + confirm OK → onPromoteWeekOnly が patientId/name で呼ばれる
 *   4. confirm キャンセル → onPromoteWeekOnly は呼ばれない
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

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

vi.mock('lucide-react', () => ({
  X: (props: React.SVGProps<SVGSVGElement>) => <svg data-testid="x-icon" {...props} />,
  Info: (props: React.SVGProps<SVGSVGElement>) => <svg data-testid="info-icon" {...props} />,
}));

import { CourseDayTable } from '../CourseDayTable';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { CourseGridVisit } from '../CourseDayTable';

const baseTpl: CourseTemplateRead = {
  id: 'tpl-u2',
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

function visit(over: Partial<CourseGridVisit> = {}): CourseGridVisit {
  return {
    id: 'visit-u2-1',
    patient_id: 'p-1',
    patient_name: '山田太郎',
    patient_address: '東京都渋谷区',
    patient_requires_multiple_staff: false,
    patient_sex_restriction_label: null,
    required_staff_count: 1,
    start_slot: '10:00',
    ...over,
  };
}

function renderTable(
  opts: {
    canEdit?: boolean;
    onPromoteWeekOnly?: ((patientId: string, patientName: string) => void) | undefined;
    visits?: CourseGridVisit[];
  } = {},
) {
  const { canEdit = true, onPromoteWeekOnly = vi.fn(), visits = [visit()] } = opts;
  return render(
    <CourseDayTable
      weekday={0}
      template={baseTpl}
      course={
        {
          id: 'c-u2',
          assigned_staff_id: 'staff-1',
          weekday: 0,
          iso_year: 2026,
          iso_week: 27,
        } as unknown as Parameters<typeof CourseDayTable>[0]['course']
      }
      officeName="本店"
      visits={visits}
      staffOptions={[]}
      canEdit={canEdit}
      onChangeAssignedStaff={vi.fn()}
      isStaffMutating={false}
      onPromoteWeekOnly={onPromoteWeekOnly}
    />,
  );
}

describe('Wave U-2: 「今週のみ」チップ', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('1. source=manual_week → チップが表示される', () => {
    renderTable({ visits: [visit({ source: 'manual_week' })] });
    expect(screen.getByTestId('visit-week-only-chip')).toBeInTheDocument();
  });

  it('2. source=manual / 欠落 → チップは非表示', () => {
    renderTable({ visits: [visit({ source: 'manual' })] });
    expect(screen.queryByTestId('visit-week-only-chip')).not.toBeInTheDocument();
    renderTable({ visits: [visit({ source: null })] });
    expect(screen.queryByTestId('visit-week-only-chip')).not.toBeInTheDocument();
  });

  it('3. クリック + confirm OK → onPromoteWeekOnly(patientId, name)', () => {
    const onPromoteWeekOnly = vi.fn();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderTable({ visits: [visit({ source: 'manual_week' })], onPromoteWeekOnly });
    fireEvent.click(screen.getByTestId('visit-week-only-chip'));
    expect(onPromoteWeekOnly).toHaveBeenCalledOnce();
    expect(onPromoteWeekOnly).toHaveBeenCalledWith('p-1', '山田太郎');
  });

  it('4. confirm キャンセル → onPromoteWeekOnly は呼ばれない', () => {
    const onPromoteWeekOnly = vi.fn();
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderTable({ visits: [visit({ source: 'manual_week' })], onPromoteWeekOnly });
    fireEvent.click(screen.getByTestId('visit-week-only-chip'));
    expect(onPromoteWeekOnly).not.toHaveBeenCalled();
  });
});
