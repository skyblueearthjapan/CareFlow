/**
 * Wave 36: CourseDayTable 内 visit × ボタン削除 UI テスト.
 *
 * カバーするシナリオ:
 *   1. canEdit=true かつ onDeleteVisit 有り → × ボタンが DOM に存在する
 *   2. canEdit=false → × ボタンが DOM に存在しない (staff ロール)
 *   3. onDeleteVisit なし → × ボタンが DOM に存在しない
 *   4. × ボタンクリックで onDeleteVisit が visitId と patientName で呼ばれる
 *   5. × ボタンの aria-label が正しく設定されている
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

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

// lucide-react X / Info アイコンを軽量スタブ化
vi.mock('lucide-react', () => ({
  X: (props: React.SVGProps<SVGSVGElement>) => <svg data-testid="x-icon" {...props} />,
  Info: (props: React.SVGProps<SVGSVGElement>) => <svg data-testid="info-icon" {...props} />,
}));

import { CourseDayTable } from '../CourseDayTable';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { CourseGridVisit } from '../CourseDayTable';

// ─────────────────────────────────────────────────────────────────────────
// Fixtures
// ─────────────────────────────────────────────────────────────────────────

const baseTpl: CourseTemplateRead = {
  id: 'tpl-w36',
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

const visit1: CourseGridVisit = {
  id: 'visit-w36-1',
  patient_id: 'p-1',
  patient_name: '山田太郎',
  patient_address: '東京都渋谷区',
  patient_requires_multiple_staff: false,
  patient_sex_restriction_label: null,
  required_staff_count: 1,
  start_slot: '10:00',
};

function renderTable(
  opts: {
    canEdit?: boolean;
    onDeleteVisit?: ((visitId: string, patientName: string) => void) | undefined;
    visits?: CourseGridVisit[];
    includeOnDelete?: boolean;
  } = {},
) {
  const {
    canEdit = true,
    onDeleteVisit = vi.fn(),
    visits = [visit1],
    includeOnDelete = true,
  } = opts;
  return render(
    <CourseDayTable
      weekday={0}
      template={baseTpl}
      course={
        {
          id: 'c-w36',
          assigned_staff_id: 'staff-1',
          weekday: 0,
          iso_year: 2026,
          iso_week: 19,
        } as unknown as Parameters<typeof CourseDayTable>[0]['course']
      }
      officeName="本店"
      visits={visits}
      staffOptions={[]}
      canEdit={canEdit}
      onChangeAssignedStaff={vi.fn()}
      isStaffMutating={false}
      onDeleteVisit={includeOnDelete ? onDeleteVisit : undefined}
    />,
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────

describe('Wave 36: visit × ボタン削除 UI', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('1. canEdit=true かつ onDeleteVisit 有り → × ボタンが DOM に存在する', () => {
    renderTable({ canEdit: true });
    const btn = screen.getByTestId('delete-visit-btn-visit-w36-1');
    expect(btn).toBeInTheDocument();
  });

  it('2. canEdit=false → × ボタンが DOM に存在しない (staff ロール)', () => {
    renderTable({ canEdit: false });
    expect(screen.queryByTestId('delete-visit-btn-visit-w36-1')).not.toBeInTheDocument();
  });

  it('3. onDeleteVisit なし → × ボタンが DOM に存在しない', () => {
    renderTable({ canEdit: true, includeOnDelete: false });
    expect(screen.queryByTestId('delete-visit-btn-visit-w36-1')).not.toBeInTheDocument();
  });

  it('4. × ボタンクリックで onDeleteVisit が visitId と patientName で呼ばれる', () => {
    const onDeleteVisit = vi.fn();
    renderTable({ canEdit: true, onDeleteVisit });
    fireEvent.click(screen.getByTestId('delete-visit-btn-visit-w36-1'));
    expect(onDeleteVisit).toHaveBeenCalledOnce();
    expect(onDeleteVisit).toHaveBeenCalledWith('visit-w36-1', '山田太郎');
  });

  it('5. × ボタンの aria-label が正しく設定されている', () => {
    renderTable({ canEdit: true });
    const btn = screen.getByTestId('delete-visit-btn-visit-w36-1');
    expect(btn).toHaveAttribute('aria-label', '山田太郎 の訪問を削除');
    expect(btn).toHaveAttribute('title', 'この訪問を削除');
  });
});
