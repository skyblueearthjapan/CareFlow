/**
 * Wave 38: CourseDayTable visit セルに「相方の現在地」表示テスト.
 *
 * カバーするシナリオ:
 *   1. partner_location=null (通常患者) → 注記なし + 左ボーダーなし (regression)
 *   2. partner_location={kind:'cell',...} → "相方: 本店-A 10:00" 表示 +
 *      patient_requires_multiple_staff=true なら左ボーダー強調
 *   3. partner_location={kind:'pool'} → "相方: プール" 表示 + 警告色 (text-warning)
 *   4. partner_location 指定でも patient_requires_multiple_staff=false なら
 *      左ボーダーは付かない (= multi flag 単独で border 制御)
 *   5. tooltip に相方位置が含まれる
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

vi.mock('lucide-react', () => ({
  X: (props: React.SVGProps<SVGSVGElement>) => <svg data-testid="x-icon" {...props} />,
  Info: (props: React.SVGProps<SVGSVGElement>) => <svg data-testid="info-icon" {...props} />,
}));

import { CourseDayTable } from '../CourseDayTable';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { CourseGridVisit } from '../CourseDayTable';

const baseTpl: CourseTemplateRead = {
  id: 'tpl-w38',
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

function renderTable(visits: CourseGridVisit[]) {
  return render(
    <CourseDayTable
      weekday={0}
      template={baseTpl}
      course={
        {
          id: 'c-w38',
          assigned_staff_id: null,
          weekday: 0,
          iso_year: 2026,
          iso_week: 19,
        } as unknown as Parameters<typeof CourseDayTable>[0]['course']
      }
      officeName="本店"
      visits={visits}
      staffOptions={[]}
      canEdit={true}
      onChangeAssignedStaff={vi.fn()}
      isStaffMutating={false}
      onDeleteVisit={vi.fn()}
    />,
  );
}

const baseVisit = (overrides: Partial<CourseGridVisit> = {}): CourseGridVisit => ({
  id: 'v-1',
  patient_id: 'p-1',
  patient_name: '田中 太郎',
  patient_address: null,
  patient_requires_multiple_staff: false,
  patient_sex_restriction_label: null,
  required_staff_count: 1,
  start_slot: '10:00',
  ...overrides,
});

describe('Wave 38: CourseDayTable 相方現在地表示', () => {
  it('1. 通常患者 (multi=false / partner_location=null) → 注記なし + 左ボーダーなし (regression)', () => {
    renderTable([baseVisit({ id: 'v-norm', patient_requires_multiple_staff: false })]);
    expect(screen.queryByTestId('course-occupant-partner-location-v-norm')).not.toBeInTheDocument();
    // 名前 cell の親 div に multi-staff 属性が付かない
    const nameEl = screen.getByTestId('course-occupant-name-v-norm');
    // 親 (.group) は multi-staff 属性を持たない
    const groupParent = nameEl.closest('[data-multi-staff]');
    expect(groupParent).toBeNull();
  });

  it('2. 相方が別セルに配置済み → "相方: 本店-A 10:00" 表示 + 左ボーダー (multi=true)', () => {
    renderTable([
      baseVisit({
        id: 'v-multi-cell',
        patient_requires_multiple_staff: true,
        partner_location: { kind: 'cell', cellLabel: '本店-A', time: '10:00' },
      }),
    ]);
    const partnerEl = screen.getByTestId('course-occupant-partner-location-v-multi-cell');
    expect(partnerEl).toBeInTheDocument();
    expect(partnerEl.textContent).toBe('相方: 本店-A 10:00');
    // 警告色は付かない (cell 配置済みのため text-text-muted)
    expect(partnerEl.className).toContain('text-text-muted');
    expect(partnerEl.className).not.toContain('text-warning');
    expect(partnerEl.getAttribute('data-partner-location-kind')).toBe('cell');
    // 左ボーダー強調が付く (multi=true)
    const nameEl = screen.getByTestId('course-occupant-name-v-multi-cell');
    const groupParent = nameEl.closest('[data-multi-staff="true"]') as HTMLElement | null;
    expect(groupParent).not.toBeNull();
    expect(groupParent!.className).toContain('border-l-4');
    expect(groupParent!.className).toContain('border-brand-primary/60');
  });

  it('3. 相方が pool に残存 → "相方: プール" + 警告色 (text-warning)', () => {
    renderTable([
      baseVisit({
        id: 'v-multi-pool',
        patient_requires_multiple_staff: true,
        partner_location: { kind: 'pool' },
      }),
    ]);
    const partnerEl = screen.getByTestId('course-occupant-partner-location-v-multi-pool');
    expect(partnerEl).toBeInTheDocument();
    expect(partnerEl.textContent).toBe('相方: プール');
    expect(partnerEl.className).toContain('text-warning');
    expect(partnerEl.getAttribute('data-partner-location-kind')).toBe('pool');
  });

  it('4. partner_location 指定でも multi=false なら左ボーダーは付かない', () => {
    renderTable([
      baseVisit({
        id: 'v-edge',
        patient_requires_multiple_staff: false,
        partner_location: { kind: 'cell', cellLabel: '本店-B', time: '11:00' },
      }),
    ]);
    const nameEl = screen.getByTestId('course-occupant-name-v-edge');
    const groupParent = nameEl.closest('[data-multi-staff="true"]') as HTMLElement | null;
    expect(groupParent).toBeNull();
  });

  it('5. tooltip (title) に相方の現在地が含まれる (cell)', () => {
    renderTable([
      baseVisit({
        id: 'v-tip',
        patient_requires_multiple_staff: true,
        partner_label: '佐藤 花子 ② (本店-B コース)',
        partner_location: { kind: 'cell', cellLabel: '本店-B', time: '15:00' },
      }),
    ]);
    const nameEl = screen.getByTestId('course-occupant-name-v-tip');
    const tooltip = nameEl.getAttribute('title') ?? '';
    expect(tooltip).toContain('ペア:');
    expect(tooltip).toContain('相方:');
    expect(tooltip).toContain('本店-B 15:00');
  });

  it('6. partner_location 未指定 (undefined) → 注記なし', () => {
    renderTable([
      baseVisit({
        id: 'v-undef',
        patient_requires_multiple_staff: true,
        // partner_location は省略
      }),
    ]);
    expect(
      screen.queryByTestId('course-occupant-partner-location-v-undef'),
    ).not.toBeInTheDocument();
  });
});
