/**
 * ScheduleGridV2 — controlled モード テスト (W8-FE2 Must-fix #2).
 *
 * weekStart / onWeekChange props によって親から週 state を単一管理できることを検証する。
 *
 * テストケース:
 *   1. weekStart props を渡すと ISO 週ラベルがその週を表示する
 *   2. WeekSelector の「次週」ボタンを押すと onWeekChange callback が呼ばれる
 *   3. weekStart / onWeekChange を渡さない場合 (uncontrolled) でも従来通り描画される
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// ─── モック ──────────────────────────────────────────────────────────────────

// dnd-kit
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
  PointerSensor: class {},
  useSensor: () => ({}),
  useSensors: (...args: unknown[]) => args,
}));

// sonner
vi.mock('sonner', () => ({
  toast: { warning: vi.fn(), success: vi.fn(), error: vi.fn() },
}));

// next-auth/react — useSession は ScheduleGridV2 からは呼ばれないが念のため
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: null, status: 'unauthenticated' }),
}));

// @/lib/queries/patients
const mockUsePatients = vi.fn();
vi.mock('@/lib/queries/patients', () => ({
  usePatients: (...args: unknown[]) => mockUsePatients(...args),
}));

// @/lib/queries/schedule_v2 — FixButton 経由で useFixSchedule が呼ばれる
vi.mock('@/lib/queries/schedule_v2', () => ({
  useFixSchedule: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
}));

// lucide-react icons (最小スタブ)
vi.mock('lucide-react', () => ({
  ChevronLeft: () => <span data-testid="icon-chevron-left" />,
  ChevronRight: () => <span data-testid="icon-chevron-right" />,
  Inbox: () => <span data-testid="icon-inbox" />,
  CheckCircle: () => <span />,
  AlertCircle: () => <span />,
  Loader2: () => <span />,
  User: () => <span />,
  Users: () => <span />,
  X: () => <span />,
  Plus: () => <span />,
}));

// @/components/ui/date-picker — ポータル不要なスタブ
vi.mock('@/components/ui/date-picker', () => ({
  DatePicker: ({
    value,
    onChange,
  }: {
    value: Date;
    onChange?: (d: Date) => void;
    formatStr?: string;
  }) => (
    <input
      data-testid="date-picker"
      type="text"
      readOnly
      value={value.toISOString().slice(0, 10)}
      onClick={() => onChange?.(value)}
    />
  ),
}));

// @/components/ui/card
vi.mock('@/components/ui/card', () => ({
  Card: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <div data-testid="card" className={className}>
      {children}
    </div>
  ),
}));

// @/components/ui/skeleton
vi.mock('@/components/ui/skeleton', () => ({
  Skeleton: ({ className }: { className?: string }) => (
    <div data-testid="skeleton" className={className} />
  ),
}));

// @/components/ui/button
vi.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    onClick,
    'aria-label': ariaLabel,
    ...rest
  }: {
    children?: React.ReactNode;
    onClick?: () => void;
    'aria-label'?: string;
    [key: string]: unknown;
  }) => (
    <button onClick={onClick} aria-label={ariaLabel} {...rest}>
      {children}
    </button>
  ),
}));

// @/lib/utils
vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

// @/components/ui/popover
vi.mock('@/components/ui/popover', () => ({
  Popover: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  PopoverTrigger: ({
    children,
    asChild: _asChild,
  }: {
    children: React.ReactNode;
    asChild?: boolean;
  }) => <div>{children}</div>,
  PopoverContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// FixButton — 描画だけで十分
vi.mock('../FixButton', () => ({
  FixButton: () => <button data-testid="fix-button">固定</button>,
}));

// ─── Import subject under test ────────────────────────────────────────────────

import { ScheduleGridV2 } from '../ScheduleGridV2';

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** 月曜日を返す (toWeekStart と同じロジック — ローカル時刻で構築). */
function monday(year: number, month: number, day: number): Date {
  // Date コンストラクタに数値を渡すとローカル時刻として解釈される (UTC 問題を回避)
  const d = new Date(year, month - 1, day, 0, 0, 0, 0);
  const dow = (d.getDay() + 6) % 7; // Mon=0..Sun=6
  d.setDate(d.getDate() - dow);
  return d;
}

function setupDefaultPatientsQuery() {
  mockUsePatients.mockReturnValue({
    data: { items: [] },
    isLoading: false,
    dataUpdatedAt: Date.now(),
  });
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('ScheduleGridV2 — controlled モード (W8-FE2)', () => {
  beforeEach(() => {
    mockUsePatients.mockReset();
    setupDefaultPatientsQuery();
  });

  it('1. weekStart props を渡すと ISO 週ラベルがその週を反映する', () => {
    // 2025-W01: 2024-12-30 (月) が ISO 週 2025-W01
    const w = monday(2024, 12, 30);
    render(<ScheduleGridV2 canEdit={false} weekStart={w} onWeekChange={vi.fn()} />);

    // ISO ラベル "ISO 2025-W01" が画面に存在するか確認
    expect(screen.getByText(/ISO 2025-W01/)).toBeInTheDocument();
  });

  it('2. onWeekChange callback — 「次週」ボタンを押すと呼ばれる', () => {
    const w = monday(2025, 1, 6); // 2025-01-06 は月曜 (2025-W02)
    const onWeekChange = vi.fn();
    render(<ScheduleGridV2 canEdit={false} weekStart={w} onWeekChange={onWeekChange} />);

    // aria-label="次週" のボタンをクリック
    const nextBtn = screen.getByRole('button', { name: '次週' });
    fireEvent.click(nextBtn);

    expect(onWeekChange).toHaveBeenCalledOnce();
    // 翌週の月曜 (+7 日) が渡されるはず
    const called = onWeekChange.mock.calls[0][0] as Date;
    const expectedNext = new Date(w);
    expectedNext.setDate(expectedNext.getDate() + 7);
    expect(called.getFullYear()).toBe(expectedNext.getFullYear());
    expect(called.getMonth()).toBe(expectedNext.getMonth());
    expect(called.getDate()).toBe(expectedNext.getDate());
  });

  it('3. uncontrolled (props なし) でも従来通り描画できる', () => {
    // weekStart / onWeekChange を渡さない = uncontrolled
    render(<ScheduleGridV2 canEdit={false} />);

    // ISO ラベルが存在すれば正常に描画されている
    expect(screen.getByText(/ISO \d{4}-W\d{2}/)).toBeInTheDocument();
  });
});
