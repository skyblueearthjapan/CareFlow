/**
 * TimeSlotCell — unit tests (W7-FE3 Must-fix #8).
 *
 * vitest + @testing-library/react
 *
 * テストケース:
 *   1. 0 件 entry → 空セル描画
 *   2. 1 件 entry → PatientCard 1 つ描画
 *   3. 2 件 entry → PatientCard 2 つ並列
 *   4. 3 件 entry → PatientCard 1 つ + 「+2 件」バッジ
 *   5. rejectDuplicateDrop: 同一 patient → true (重複拒否)
 *   6. rejectDuplicateDrop: 別 patient → false (許可)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

// ─── モック ───────────────────────────────────────────────────────────────────

// dnd-kit: テスト環境では PointerEvent が使えないためスタブ
vi.mock('@dnd-kit/core', () => ({
  useDroppable: () => ({ isOver: false, setNodeRef: vi.fn() }),
  useDraggable: () => ({
    attributes: {},
    listeners: {},
    setNodeRef: vi.fn(),
    transform: null,
    isDragging: false,
  }),
  DndContext: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DragOverlay: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

// sonner toast
const mockToastWarning = vi.fn();
vi.mock('sonner', () => ({
  toast: {
    warning: (msg: string) => mockToastWarning(msg),
    success: vi.fn(),
    error: vi.fn(),
  },
}));

// @radix-ui/react-popover: ポータルをインラインに置換
vi.mock('@/components/ui/popover', () => ({
  Popover: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  PopoverTrigger: ({
    children,
    asChild: _asChild,
  }: {
    children: React.ReactNode;
    asChild?: boolean;
  }) => <div data-testid="popover-trigger">{children}</div>,
  PopoverContent: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="popover-content">{children}</div>
  ),
}));

// lucide-react icons
vi.mock('lucide-react', () => ({
  User: () => <span data-testid="icon-user" />,
  Users: () => <span data-testid="icon-users" />,
  X: () => <span data-testid="icon-x" />,
  Plus: () => <span data-testid="icon-plus" />,
}));

// @/lib/utils
vi.mock('@/lib/utils', () => ({
  cn: (...args: string[]) => args.filter(Boolean).join(' '),
}));

// ─── Import subject under test ────────────────────────────────────────────────

import { TimeSlotCell, rejectDuplicateDrop, type VisitEntry } from '../TimeSlotCell';

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeEntry(id: string, overrides: Partial<VisitEntry> = {}): VisitEntry {
  return {
    patientId: id,
    draggableId: `patient:${id}`,
    patient: { id, name: `患者 ${id}` },
    staffCount: 1,
    ...overrides,
  };
}

function renderCell(entries: VisitEntry[]) {
  return render(
    <TimeSlotCell
      droppableId="cell:0:08:00"
      weekday={0}
      time="08:00"
      entries={entries}
      isHourBoundary={false}
      disabled={false}
    />,
  );
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('TimeSlotCell', () => {
  beforeEach(() => {
    mockToastWarning.mockClear();
  });

  it('1. 0 件 entry → 空セル描画 (PatientCard なし)', () => {
    renderCell([]);
    // 患者名が無い = PatientCard なし
    expect(screen.queryByText(/患者/)).toBeNull();
  });

  it('2. 1 件 entry → PatientCard 1 つ描画', () => {
    renderCell([makeEntry('p1')]);
    expect(screen.getByText('患者 p1')).toBeInTheDocument();
    // バッジなし
    expect(screen.queryByText(/\+\d+件/)).toBeNull();
  });

  it('3. 2 件 entry → PatientCard 2 つ並列', () => {
    renderCell([makeEntry('p1'), makeEntry('p2')]);
    expect(screen.getByText('患者 p1')).toBeInTheDocument();
    expect(screen.getByText('患者 p2')).toBeInTheDocument();
    // バッジなし
    expect(screen.queryByText(/\+\d+件/)).toBeNull();
  });

  it('4. 3 件 entry → PatientCard 1 つ + "+2 件" バッジ', () => {
    renderCell([makeEntry('p1'), makeEntry('p2'), makeEntry('p3')]);
    // p1 は直接表示 + Popover 内にも表示されるため getAllByText で確認
    const p1Elements = screen.getAllByText('患者 p1');
    expect(p1Elements.length).toBeGreaterThanOrEqual(1);
    // "+2件" バッジが表示される
    expect(screen.getByText('+2件')).toBeInTheDocument();
    // Popover content に p2, p3 が含まれる (モックで常時表示)
    expect(screen.getByText('患者 p2')).toBeInTheDocument();
    expect(screen.getByText('患者 p3')).toBeInTheDocument();
  });
});

describe('rejectDuplicateDrop', () => {
  beforeEach(() => {
    mockToastWarning.mockClear();
  });

  it('5. 同一 patient が既存 entries にある → true + toast.warning', () => {
    const entries = [makeEntry('p1'), makeEntry('p2')];
    const result = rejectDuplicateDrop('p1', entries);
    expect(result).toBe(true);
    expect(mockToastWarning).toHaveBeenCalledOnce();
  });

  it('6. 別 patient → false (drop 許可, toast なし)', () => {
    const entries = [makeEntry('p1'), makeEntry('p2')];
    const result = rejectDuplicateDrop('p3', entries);
    expect(result).toBe(false);
    expect(mockToastWarning).not.toHaveBeenCalled();
  });
});
