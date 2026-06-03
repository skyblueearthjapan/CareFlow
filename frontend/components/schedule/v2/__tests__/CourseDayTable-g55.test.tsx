/**
 * Phase G-55: CourseDayTable 親機 空き表示 UI テスト.
 *
 * 親機 (デスクトップ) の日テーブルに「空きN枠」(頭数) と「空き時間帯」(≥60分) を
 * 親機意匠の Badge / 帯で出す。モバイル現場ボードと同じ意味論:
 *   - remaining = capacity.max - capacity.filled.
 *   - remaining<=0 (満員) のときは時間 gap があっても空き時間帯を一切出さない (頭数ゲート).
 *   - remaining>0 のときのみ「空きN枠」Badge + 空き時間帯帯を表示する.
 *
 * Phase G-55 (時刻位置配置改修): 空き時間帯は見出し下の「サマリ帯」をやめ、
 * 時間グリッド内の該当時刻位置 (午前=上 / 午後=下) にマーカーを配置する。
 * 各マーカーは testid `course-day-free-gap-{weekday}-{templateId}-{startMin}` を持つ。
 *
 * カバー:
 *   1. capacity 指定 + remaining>0 → 「空きN枠」Badge + 各 gap マーカーを時刻位置に表示.
 *   2. 満員 (filled>=max) → 「満員」Badge + gap マーカーは非表示 (頭数ゲート).
 *   3. capacity 未指定 (旧呼出) → Badge も gap マーカーも非表示 (後方互換).
 *   4. ヘッダーのコース名定員が capacity.max を反映する.
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('@dnd-kit/core', () => ({
  useDraggable: () => ({ attributes: {}, listeners: {}, setNodeRef: () => {}, isDragging: false }),
  useDroppable: () => ({ isOver: false, setNodeRef: () => {} }),
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
  Lock: (props: React.SVGProps<SVGSVGElement>) => <svg data-testid="lock-icon" {...props} />,
  Unlock: (props: React.SVGProps<SVGSVGElement>) => <svg data-testid="unlock-icon" {...props} />,
}));

import { CourseDayTable } from '../CourseDayTable';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { CourseGridVisit } from '../CourseDayTable';
import type { FreeGap } from '@/lib/scheduling/freeGaps';

const TPL_ID = 'tpl-g55';

const baseTpl: CourseTemplateRead = {
  id: TPL_ID,
  label: 'A',
  office_id: 'o1',
  capacity_mon: 6,
  capacity_tue: 6,
  capacity_wed: 6,
  capacity_thu: 6,
  capacity_fri: 6,
  capacity_sat: 6,
  capacity_sun: 0,
  notes: null,
  created_at: '',
  updated_at: '',
  deleted_at: null,
} as unknown as CourseTemplateRead;

const visit1: CourseGridVisit = {
  id: 'v-g55-1',
  patient_id: 'p-1',
  patient_name: '山田太郎',
  patient_address: '東京都渋谷区',
  patient_requires_multiple_staff: false,
  patient_sex_restriction_label: null,
  required_staff_count: 1,
  start_slot: '11:00',
};

const GAPS: FreeGap[] = [
  { startMin: 570, endMin: 660, label: '09:30〜11:00' },
  { startMin: 780, endMin: 1080, label: '13:00〜18:00' },
];

function renderTable(
  opts: {
    capacity?: { filled: number; max: number };
    freeGaps?: FreeGap[];
    visits?: CourseGridVisit[];
  } = {},
) {
  const { capacity, freeGaps, visits = [visit1] } = opts;
  return render(
    <CourseDayTable
      weekday={0}
      template={baseTpl}
      course={
        { id: 'c-g55', assigned_staff_id: null } as Parameters<typeof CourseDayTable>[0]['course']
      }
      officeName="本店"
      visits={visits}
      staffOptions={[]}
      canEdit={false}
      onChangeAssignedStaff={vi.fn()}
      isStaffMutating={false}
      capacity={capacity}
      freeGaps={freeGaps}
    />,
  );
}

describe('Phase G-55: CourseDayTable 親機 空き表示', () => {
  it('1. capacity remaining>0 → 「空きN枠」Badge + 各 gap マーカーを時刻位置に表示', () => {
    renderTable({ capacity: { filled: 1, max: 6 }, freeGaps: GAPS });
    // 頭数の空き Badge (remaining = 6 - 1 = 5).
    const cap = screen.getByTestId(`course-day-capacity-0-${TPL_ID}`);
    expect(cap).toHaveAttribute('data-remaining', '5');
    expect(cap.textContent).toContain('空き5枠');
    // 各 gap マーカーが時刻位置 (startMin) に配置される (午前=09:30 / 午後=13:00)。
    const am = screen.getByTestId(`course-day-free-gap-0-${TPL_ID}-570`);
    const pm = screen.getByTestId(`course-day-free-gap-0-${TPL_ID}-780`);
    expect(am).toHaveAttribute('data-free-gap-start', '570');
    expect(pm).toHaveAttribute('data-free-gap-start', '780');
    expect(am.textContent).toContain('09:30〜11:00');
    expect(pm.textContent).toContain('13:00〜18:00');
    // gridRow が時刻位置順 (午前が午後より前の行) になっている。
    const amRow = Number((am.style.gridRow.match(/^(\d+)/) ?? [])[1]);
    const pmRow = Number((pm.style.gridRow.match(/^(\d+)/) ?? [])[1]);
    expect(amRow).toBeLessThan(pmRow);
  });

  it('2. 満員 (filled>=max) → 「満員」Badge + gap マーカーは非表示 (頭数ゲート)', () => {
    // remaining = 0 だが freeGaps は時間的には存在する状態 → ゲートでマーカーを出さない。
    renderTable({ capacity: { filled: 6, max: 6 }, freeGaps: GAPS });
    const cap = screen.getByTestId(`course-day-capacity-0-${TPL_ID}`);
    expect(cap).toHaveAttribute('data-remaining', '0');
    expect(cap.textContent).toContain('満員');
    expect(screen.queryByTestId(`course-day-free-gap-0-${TPL_ID}-570`)).not.toBeInTheDocument();
    expect(screen.queryByTestId(`course-day-free-gap-0-${TPL_ID}-780`)).not.toBeInTheDocument();
  });

  it('3. capacity 未指定 (旧呼出) → Badge も gap マーカーも出さない (後方互換)', () => {
    renderTable({ freeGaps: GAPS });
    expect(screen.queryByTestId(`course-day-capacity-0-${TPL_ID}`)).not.toBeInTheDocument();
    expect(screen.queryByTestId(`course-day-free-gap-0-${TPL_ID}-570`)).not.toBeInTheDocument();
  });

  it('4. ヘッダーのコース名定員が capacity.max を反映する', () => {
    renderTable({ capacity: { filled: 2, max: 6 }, freeGaps: [] });
    expect(screen.getByText('本店-A コース (6)')).toBeInTheDocument();
    // freeGaps 空配列 → マーカーは出ないが Badge は出る。
    expect(screen.queryByTestId(`course-day-free-gap-0-${TPL_ID}-570`)).not.toBeInTheDocument();
    expect(screen.getByTestId(`course-day-capacity-0-${TPL_ID}`).textContent).toContain('空き4枠');
  });
});
