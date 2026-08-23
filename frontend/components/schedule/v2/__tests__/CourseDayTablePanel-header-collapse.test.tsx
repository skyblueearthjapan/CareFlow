/**
 * CourseDayTablePanel — 上部折りたたみ (コンパクト表示) テスト。
 *
 * PO 要望 (2026-08-23): スケジュール画面の上部を折りたためるようにして
 * 盤面を広く見せたい。
 *
 * カバーするシナリオ:
 *  HC-1  既定は展開 — Row1 (主要操作) / Row2 (戻る進む + 青ピン一括) が見える
 *  HC-2  トグル押下 → Row1/Row2 が消え、曜日タブ / 戻る進む / 表示切替は残る
 *  HC-3  トグルの状態が localStorage ('carelink-ui') に永続する
 *  HC-4  「ツール」で Row1 を一時展開 → 畳み状態は維持されたまま
 *  HC-5  「ツール」再押下で閉じる
 *  HC-6  畳んだ状態でも週切替 (前週 / 今週 / 次週) が動く
 *  HC-7  畳んだ状態の拠点フィルタが onOfficeChange を呼ぶ
 *  HC-8  a11y — トグルは button で aria-label / aria-pressed を持つ
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// ─── hoisted 変数 ──────────────────────────────────────────────────────────

const { mockToast } = vi.hoisted(() => ({
  mockToast: {
    warning: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

// ─── モック ───────────────────────────────────────────────────────────────

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
  PointerSensor: class PointerSensor {},
  TouchSensor: class TouchSensor {},
  useSensor: (_Cls: unknown, opts?: unknown) => ({ sensor: _Cls, opts }),
  useSensors: (...args: unknown[]) => args,
}));

vi.mock('@dnd-kit/utilities', () => ({
  CSS: { Translate: { toString: () => '' } },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: null, status: 'unauthenticated' }),
}));

vi.mock('@/components/ui/card', () => ({
  Card: ({
    children,
    className,
    ...rest
  }: {
    children: React.ReactNode;
    className?: string;
    [k: string]: unknown;
  }) => (
    <div className={className} {...rest}>
      {children}
    </div>
  ),
}));

vi.mock('@/components/ui/skeleton', () => ({
  Skeleton: () => <div data-testid="skeleton" />,
}));

vi.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    onClick,
    disabled,
    'data-testid': testId,
    title,
    ...rest
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    'data-testid'?: string;
    title?: string;
    [k: string]: unknown;
  }) => (
    <button onClick={onClick} disabled={disabled} data-testid={testId} title={title} {...rest}>
      {children}
    </button>
  ),
}));

vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

// ─── Hooks ───

const mockOffices = vi.fn();
const mockPatients = vi.fn();
const mockStaffList = vi.fn();
const mockUseQueries = vi.fn();
const mockVisits = vi.fn();
const mockCourses = vi.fn();

vi.mock('@tanstack/react-query', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type TanstackQuery = typeof import('@tanstack/react-query');
  const actual = await importOriginal<TanstackQuery>();
  return {
    ...actual,
    useQueries: (...args: unknown[]) => mockUseQueries(...args),
  };
});

vi.mock('@/lib/api/fetcher', () => ({
  fetcher: vi.fn(),
}));

vi.mock('@/lib/queries/weekday_staff_capacity', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/weekday_staff_capacity');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    useWeekdayStaffCapacityLookup: () => ({
      staffCountFor: () => 5,
      managerCountFor: () => 0,
      courseCodesMax: 5,
      isLoading: false,
    }),
  };
});
vi.mock('@/lib/queries/pfv_course_presence', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/pfv_course_presence');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    usePfvCoursePresenceLookup: () => ({ pfvCountFor: () => 0, isLoading: false }),
  };
});
vi.mock('@/lib/queries/offices', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/offices');
  const actual = await importOriginal<M>();
  return { ...actual, useOffices: (...args: unknown[]) => mockOffices(...args) };
});
vi.mock('@/lib/queries/patients', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/patients');
  const actual = await importOriginal<M>();
  return { ...actual, usePatients: (...args: unknown[]) => mockPatients(...args) };
});
vi.mock('@/lib/queries/staff', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/staff');
  const actual = await importOriginal<M>();
  return { ...actual, useStaffList: (...args: unknown[]) => mockStaffList(...args) };
});
vi.mock('@/lib/queries/visits', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/visits');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    useVisits: (...args: unknown[]) => mockVisits(...args),
    useDeleteVisit: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});
vi.mock('@/lib/queries/courses', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/courses');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    useCourses: (...args: unknown[]) => mockCourses(...args),
    useUpdateCourse: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});
vi.mock('@/lib/queries/place_and_fix', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/place_and_fix');
  const actual = await importOriginal<M>();
  return { ...actual, usePlaceAndFix: () => ({ mutateAsync: vi.fn(), isPending: false }) };
});
vi.mock('@/lib/queries/generate_week', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/generate_week');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    useGenerateWeek: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useGenerateWeekOnly: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});
vi.mock('@/lib/queries/assign_staff_only', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/assign_staff_only');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    useAssignStaffOnly: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});
vi.mock('@/lib/queries/g21', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/g21');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    useTogglePfvPin: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
    useBulkPinPfvs: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  };
});
vi.mock('@/lib/queries/staff-events', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/staff-events');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    useWeekStaffEvents: () => ({ data: [], isLoading: false }),
    buildStaffEventsMap: () => new Map(),
    useUpdateEventForDrag: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});
vi.mock('@/lib/queries/opLog', () => ({
  useOpLogState: () => ({
    data: { can_undo: true, can_redo: true, undo_label: null, redo_label: null },
    isLoading: false,
  }),
  useUndoOpLog: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRedoOpLog: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useInvalidateOpLog: () => vi.fn(),
  OP_LOG_STATE_KEY: 'op-log-state',
}));

vi.mock('@/lib/queries/visitMoveWeekOnly', () => ({
  useVisitMoveWeekOnly: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), back: vi.fn() }),
  usePathname: () => '/schedule',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/queries/schedulingSettings', () => ({
  useSchedulingSettings: () => ({ data: undefined, isLoading: false }),
}));

// ─── Subject under test ────────────────────────────────────────────────────

import { CourseDayTablePanel } from '../CourseDayTablePanel';
import { useUIStore } from '@/lib/stores/ui';

// ─── helpers ──────────────────────────────────────────────────────────────

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

function monday(year: number, month: number, day: number): Date {
  const d = new Date(year, month - 1, day, 0, 0, 0, 0);
  const dow = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - dow);
  return d;
}

const baseTpl = {
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
};

function setupHooks() {
  mockOffices.mockReturnValue({
    allOffices: [
      { id: 'office-honten', name: '本店' },
      { id: 'office-tsuga', name: '都賀' },
    ],
    isLoading: false,
  });
  mockPatients.mockReturnValue({ data: { items: [] }, isLoading: false });
  mockStaffList.mockReturnValue({ data: [], isLoading: false });
  mockUseQueries.mockReturnValue([
    {
      data: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      isLoading: false,
    },
  ]);
  mockVisits.mockReturnValue({ data: { items: [], truncated: false }, isLoading: false });
  mockCourses.mockReturnValue({ data: [], isLoading: false });
}

const WEEK_START = monday(2026, 5, 4);

const onWeekChange = vi.fn();
const onOfficeChange = vi.fn();

function renderPanel() {
  return renderWithClient(
    <CourseDayTablePanel
      weekStart={WEEK_START}
      officeId={null}
      canEdit
      onWeekChange={onWeekChange}
      onOfficeChange={onOfficeChange}
    />,
  );
}

/** トグルを押して畳んだ状態にする。 */
function collapse() {
  fireEvent.click(screen.getByTestId('schedule-header-collapse-toggle'));
}

// ─── Tests ─────────────────────────────────────────────────────────────────

describe('CourseDayTablePanel — 上部折りたたみ (コンパクト表示)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    useUIStore.setState({ scheduleHeaderCollapsed: false });
    setupHooks();
  });

  it('HC-1. 既定は展開 — Row1 / Row2 が表示され、トグルは「コンパクト表示」', () => {
    renderPanel();

    expect(screen.getByTestId('schedule-main-action-toolbar')).toBeInTheDocument();
    expect(screen.getByTestId('course-day-bulk-pin-row')).toBeInTheDocument();
    expect(screen.queryByTestId('schedule-compact-tools-button')).not.toBeInTheDocument();
    expect(screen.queryByTestId('schedule-compact-week-nav')).not.toBeInTheDocument();

    const toggle = screen.getByTestId('schedule-header-collapse-toggle');
    expect(toggle).toHaveTextContent('コンパクト表示');
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
  });

  it('HC-2. トグル押下 → Row1 が消え、曜日タブ / 戻る進む / 表示切替は残る', () => {
    renderPanel();
    collapse();

    // Row 1 (主要操作) は消える.
    expect(screen.queryByTestId('schedule-main-action-toolbar')).not.toBeInTheDocument();
    expect(screen.queryByTestId('generate-week-button')).not.toBeInTheDocument();
    // Row 2 の青ピン一括行も消える.
    expect(screen.queryByTestId('course-day-bulk-pin-row')).not.toBeInTheDocument();

    // 曜日タブ行は残る.
    expect(screen.getByTestId('course-day-tab-row')).toBeInTheDocument();
    expect(screen.getByTestId('course-day-tabs')).toBeInTheDocument();
    expect(screen.getByTestId('course-day-tab-staff')).toBeInTheDocument();

    // 戻る / 進む はコンパクト行に 1 つずつだけ存在する (重複しない).
    expect(screen.getAllByTestId('schedule-undo-button')).toHaveLength(1);
    expect(screen.getAllByTestId('schedule-redo-button')).toHaveLength(1);

    // 表示切替 (既定タブ = 週) も残る.
    expect(screen.getByTestId('course-week-mode-overview')).toBeInTheDocument();

    // 週切替と拠点フィルタがコンパクト行に出る.
    expect(screen.getByTestId('schedule-compact-week-nav')).toBeInTheDocument();
    expect(screen.getByTestId('schedule-compact-office-select')).toBeInTheDocument();

    // トグルのラベルは「上部を表示」へ.
    const toggle = screen.getByTestId('schedule-header-collapse-toggle');
    expect(toggle).toHaveTextContent('上部を表示');
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
  });

  it('HC-3. 折りたたみ状態が localStorage (carelink-ui) に永続する', () => {
    renderPanel();
    collapse();

    expect(useUIStore.getState().scheduleHeaderCollapsed).toBe(true);

    const raw = window.localStorage.getItem('carelink-ui');
    expect(raw).not.toBeNull();
    expect(JSON.parse(raw as string).state.scheduleHeaderCollapsed).toBe(true);
  });

  it('HC-4. 「ツール」で Row1 を一時展開しても畳み状態は維持される', () => {
    renderPanel();
    collapse();

    expect(screen.queryByTestId('schedule-compact-tools-popover')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('schedule-compact-tools-button'));

    expect(screen.getByTestId('schedule-compact-tools-popover')).toBeInTheDocument();
    expect(screen.getByTestId('schedule-main-action-toolbar')).toBeInTheDocument();
    expect(screen.getByTestId('generate-week-button')).toBeInTheDocument();

    // 一時展開でも戻る/進むは重複しない (Row 2 側は描画しない).
    expect(screen.getAllByTestId('schedule-undo-button')).toHaveLength(1);

    // 畳み状態そのものは維持 (永続値は true のまま).
    expect(useUIStore.getState().scheduleHeaderCollapsed).toBe(true);
    expect(screen.getByTestId('schedule-header-collapse-toggle')).toHaveTextContent('上部を表示');
  });

  it('HC-5. 「ツール」再押下で一時展開が閉じる', () => {
    renderPanel();
    collapse();

    fireEvent.click(screen.getByTestId('schedule-compact-tools-button'));
    expect(screen.getByTestId('schedule-compact-tools-popover')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('schedule-compact-tools-button'));
    expect(screen.queryByTestId('schedule-compact-tools-popover')).not.toBeInTheDocument();
    expect(screen.queryByTestId('schedule-main-action-toolbar')).not.toBeInTheDocument();
  });

  it('HC-6. 畳んだ状態でも週切替 (前週 / 次週 / 今週) が動く', () => {
    renderPanel();
    collapse();

    fireEvent.click(screen.getByTestId('schedule-compact-week-prev'));
    expect(onWeekChange).toHaveBeenCalledTimes(1);
    const prev = onWeekChange.mock.calls[0][0] as Date;
    expect(prev.getTime()).toBe(WEEK_START.getTime() - 7 * 24 * 60 * 60 * 1000);

    fireEvent.click(screen.getByTestId('schedule-compact-week-next'));
    expect(onWeekChange).toHaveBeenCalledTimes(2);
    const next = onWeekChange.mock.calls[1][0] as Date;
    expect(next.getTime()).toBe(WEEK_START.getTime() + 7 * 24 * 60 * 60 * 1000);

    fireEvent.click(screen.getByTestId('schedule-compact-week-today'));
    expect(onWeekChange).toHaveBeenCalledTimes(3);
    const today = onWeekChange.mock.calls[2][0] as Date;
    // 月曜 0:00 に丸められている.
    expect(today.getDay()).toBe(1);
    expect(today.getHours()).toBe(0);

    // 週ラベルが「M/d(月) 〜 M/d(土)」形式で出る.
    expect(screen.getByTestId('schedule-compact-week-label')).toHaveTextContent('(月)');
    expect(screen.getByTestId('schedule-compact-week-label')).toHaveTextContent('(土)');
  });

  it('HC-7. 畳んだ状態の拠点フィルタが onOfficeChange を呼ぶ', () => {
    renderPanel();
    collapse();

    const select = screen.getByTestId('schedule-compact-office-select');
    fireEvent.change(select, { target: { value: 'office-tsuga' } });
    expect(onOfficeChange).toHaveBeenCalledWith('office-tsuga');

    fireEvent.change(select, { target: { value: '' } });
    expect(onOfficeChange).toHaveBeenLastCalledWith(null);
  });

  it('HC-8. a11y — トグルと「ツール」は button で aria 属性を持つ', () => {
    renderPanel();

    const toggle = screen.getByTestId('schedule-header-collapse-toggle');
    expect(toggle.tagName).toBe('BUTTON');
    expect(toggle).toHaveAttribute('aria-label', 'コンパクト表示にする');
    expect(toggle).toHaveAttribute('title');

    collapse();

    const collapsedToggle = screen.getByTestId('schedule-header-collapse-toggle');
    expect(collapsedToggle).toHaveAttribute('aria-label', '上部を表示');

    const tools = screen.getByTestId('schedule-compact-tools-button');
    expect(tools.tagName).toBe('BUTTON');
    expect(tools).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(tools);
    expect(screen.getByTestId('schedule-compact-tools-button')).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });
});
