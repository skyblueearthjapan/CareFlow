/**
 * CourseDayTable 🔒 完全固定 toggle UI テスト (Phase G-21 T4).
 *
 * カバーするシナリオ:
 *   1. canEdit=true + onTogglePin + fixed_visit_id 有り → 🔒 button が DOM に存在
 *   2. canEdit=false → 🔒 button が DOM に存在しない
 *   3. onTogglePin なし → 🔒 button が DOM に存在しない
 *   4. fixed_visit_id なし → button は disabled / tooltip = '先に固定枠登録が必要'
 *   5. is_pinned=true → セルに data-has-pinned-visit / data-pinned="true"
 *   6. 🔒 click → onTogglePin(pfvId, !isPinned) が呼ばれる
 *   7. is_pinned=false visit を click → onTogglePin が true で呼ばれる
 *   8. 🔒 button の aria-pressed が isPinned と同期する
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

// lucide-react X / Info / Lock / Unlock アイコンを軽量スタブ化
vi.mock('lucide-react', () => ({
  X: (props: React.SVGProps<SVGSVGElement>) => <svg data-testid="x-icon" {...props} />,
  Info: (props: React.SVGProps<SVGSVGElement>) => <svg data-testid="info-icon" {...props} />,
  Lock: (props: React.SVGProps<SVGSVGElement>) => <svg data-testid="lock-icon" {...props} />,
  Unlock: (props: React.SVGProps<SVGSVGElement>) => <svg data-testid="unlock-icon" {...props} />,
}));

// Phase G-47: PinScopeMenu を軽量モック化.
//   - menu UI は別ファイル (PinScopeMenu.test.tsx) で検証する.
//   - ここでは trigger button の click が即時 'day' スコープで onSelect を呼ぶ
//     簡易動作にして、 CourseDayTable 側の wiring (= 既存テスト互換) を検証する.
vi.mock('../PinScopeMenu', () => ({
  PinScopeMenu: ({
    pfvId,
    patientId,
    isPinned,
    onSelect,
    disabled,
    children,
  }: {
    pfvId: string;
    patientId: string;
    isPinned: boolean;
    onSelect: (
      pfvId: string,
      nextPinned: boolean,
      scope: 'day' | 'all-days',
      patientId: string,
    ) => void;
    disabled?: boolean;
    children: (args: { isOpen: boolean }) => React.ReactElement;
  }) => {
    const node = children({ isOpen: false });
    // trigger button に onClick を注入して 'day' スコープで onSelect を発火する.
    // disabled のときは何もしない (= 既存挙動と同じ).
    const handleClick = () => {
      if (disabled) return;
      onSelect(pfvId, !isPinned, 'day', patientId);
    };
    return React.cloneElement(node, {
      onClick: handleClick,
    });
  },
}));

import { CourseDayTable } from '../CourseDayTable';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { CourseGridVisit } from '../CourseDayTable';

// ─────────────────────────────────────────────────────────────────────────
// Fixtures
// ─────────────────────────────────────────────────────────────────────────

const baseTpl: CourseTemplateRead = {
  id: 'tpl-g21',
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

function makeVisit(overrides: Partial<CourseGridVisit> = {}): CourseGridVisit {
  return {
    id: 'visit-g21-1',
    patient_id: 'p-1',
    patient_name: '山田太郎',
    patient_address: '東京都渋谷区',
    patient_requires_multiple_staff: false,
    patient_sex_restriction_label: null,
    required_staff_count: 1,
    start_slot: '10:00',
    fixed_visit_id: 'pfv-1',
    is_pinned: false,
    ...overrides,
  };
}

interface RenderOpts {
  canEdit?: boolean;
  // Phase G-47: onTogglePin に scope + patientId 引数が追加された.
  onTogglePin?: (
    pfvId: string,
    nextPinned: boolean,
    scope: 'day' | 'all-days',
    patientId: string,
  ) => void;
  visits?: CourseGridVisit[];
  includeOnTogglePin?: boolean;
}

function renderTable(opts: RenderOpts = {}) {
  const {
    canEdit = true,
    onTogglePin = vi.fn(),
    visits = [makeVisit()],
    includeOnTogglePin = true,
  } = opts;
  return render(
    <CourseDayTable
      weekday={0}
      template={baseTpl}
      course={
        {
          id: 'c-g21',
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
      onTogglePin={includeOnTogglePin ? onTogglePin : undefined}
    />,
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────

describe('CourseDayTable 🔒 pin toggle (Phase G-21)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('1. canEdit=true + onTogglePin + fixed_visit_id 有り → 🔒 button が DOM に存在する', () => {
    renderTable({ canEdit: true });
    expect(screen.getByTestId('pin-visit-btn-visit-g21-1')).toBeInTheDocument();
  });

  it('2. canEdit=false → 🔒 button が DOM に存在しない (staff ロール)', () => {
    renderTable({ canEdit: false });
    expect(screen.queryByTestId('pin-visit-btn-visit-g21-1')).not.toBeInTheDocument();
  });

  it('3. onTogglePin なし → 🔒 button が DOM に存在しない', () => {
    renderTable({ canEdit: true, includeOnTogglePin: false });
    expect(screen.queryByTestId('pin-visit-btn-visit-g21-1')).not.toBeInTheDocument();
  });

  it('4. fixed_visit_id なし → button は disabled / tooltip は「先に固定枠登録が必要」', () => {
    renderTable({
      canEdit: true,
      visits: [makeVisit({ fixed_visit_id: null, is_pinned: false })],
    });
    const btn = screen.getByTestId('pin-visit-btn-visit-g21-1');
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('title', '先に固定枠登録が必要');
    expect(btn).toHaveAttribute('data-pfv-id', '');
  });

  it('5. is_pinned=true → セルに data-has-pinned-visit + button data-pinned="true"', () => {
    renderTable({
      canEdit: true,
      visits: [makeVisit({ is_pinned: true })],
    });
    const btn = screen.getByTestId('pin-visit-btn-visit-g21-1');
    expect(btn).toHaveAttribute('data-pinned', 'true');
    expect(btn).toHaveAttribute('aria-pressed', 'true');
    // 行 (droppable cell) に data-has-pinned-visit が立つ
    const cells = document.querySelectorAll('[data-has-pinned-visit="true"]');
    expect(cells.length).toBeGreaterThan(0);
  });

  it('6. 🔒 click (is_pinned=true) → onTogglePin(pfvId, false, "day", patientId) が呼ばれる', () => {
    const onTogglePin = vi.fn();
    renderTable({
      canEdit: true,
      onTogglePin,
      visits: [makeVisit({ fixed_visit_id: 'pfv-xyz', is_pinned: true, patient_id: 'p-1' })],
    });
    fireEvent.click(screen.getByTestId('pin-visit-btn-visit-g21-1'));
    expect(onTogglePin).toHaveBeenCalledOnce();
    // Phase G-47: 4 引数 (scope='day' / patientId) を伴って呼ばれる.
    expect(onTogglePin).toHaveBeenCalledWith('pfv-xyz', false, 'day', 'p-1');
  });

  it('7. 🔓 click (is_pinned=false) → onTogglePin(pfvId, true, "day", patientId) が呼ばれる', () => {
    const onTogglePin = vi.fn();
    renderTable({
      canEdit: true,
      onTogglePin,
      visits: [makeVisit({ fixed_visit_id: 'pfv-zzz', is_pinned: false, patient_id: 'p-2' })],
    });
    fireEvent.click(screen.getByTestId('pin-visit-btn-visit-g21-1'));
    expect(onTogglePin).toHaveBeenCalledWith('pfv-zzz', true, 'day', 'p-2');
  });

  it('8. fixed_visit_id なし click → onTogglePin が呼ばれない (button disabled で stopPropagation)', () => {
    const onTogglePin = vi.fn();
    renderTable({
      canEdit: true,
      onTogglePin,
      visits: [makeVisit({ fixed_visit_id: null, is_pinned: false })],
    });
    const btn = screen.getByTestId('pin-visit-btn-visit-g21-1');
    // ボタンは disabled でも fireEvent.click は実行可. 内部 guard で no-op になる.
    fireEvent.click(btn);
    expect(onTogglePin).not.toHaveBeenCalled();
  });

  // ─────────────────────────────────────────────────────────────────────
  // Phase G-21 final C1 (F1): pinned visit は D&D 経路で動かせない.
  // useDraggable 自体は mock 済 (= disabled prop は実際には dnd-kit に渡されない)
  // が、 行コンテナの `data-row-draggable-disabled` 属性と `cursor-not-allowed`
  // class で「FE 側が pinned visit を drag 不可と認識している」ことを検証する.
  // ─────────────────────────────────────────────────────────────────────

  it('9. is_pinned=true → 行 div に data-row-draggable-disabled / cursor-not-allowed が付く (F1)', () => {
    renderTable({
      canEdit: true,
      visits: [makeVisit({ is_pinned: true })],
    });
    const rowEl = document.querySelector(
      '[data-row-draggable-visit-id="visit-g21-1"]',
    ) as HTMLElement | null;
    expect(rowEl).not.toBeNull();
    expect(rowEl?.getAttribute('data-row-draggable-disabled')).toBe('true');
    expect(rowEl?.className).toContain('cursor-not-allowed');
    expect(rowEl?.getAttribute('title')).toMatch(/完全固定中/);
  });

  it('10. is_pinned=false → 行 div は draggable (data-row-draggable-disabled 無し)', () => {
    renderTable({
      canEdit: true,
      visits: [makeVisit({ is_pinned: false })],
    });
    const rowEl = document.querySelector(
      '[data-row-draggable-visit-id="visit-g21-1"]',
    ) as HTMLElement | null;
    expect(rowEl).not.toBeNull();
    expect(rowEl?.hasAttribute('data-row-draggable-disabled')).toBe(false);
    expect(rowEl?.className).toContain('cursor-grab');
  });
});
