/**
 * CourseDayTablePanel — 「週を生成」再実行の確認ダイアログ (PO 2026-07-10)。
 *
 * 2026-10-01: ツールバーは「週を作る」になり、ダイアログで「固定訪問から生成」を選ぶと
 * 従来の「週を生成」と同じ流れ (この確認ダイアログを含む) になる
 * (copy-week-design-2026-09-30.md §4-1)。以下の「押す」は「週を作る → 固定訪問から生成」。
 *
 * カバー:
 *   ① 当週に訪問がある状態で「週を生成」を押す → 確認ダイアログが出て mutation は未実行
 *      → 「再実行する」で mutation が実行される
 *   ② 訪問 0 件 (未生成) の週で押す → ダイアログなしで即実行 (挙動不変)
 *
 * mock 流儀は CourseDayTablePanel-pfv-presence.test.tsx を踏襲。ただし
 * useGenerateWeekOnly の mutateAsync を hoisted spy にして呼び出しを検証する。
 * Dialog (@/components/ui/dialog) は実物を使う (確認ダイアログの描画を検証するため)。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// ─── 可変ステート ───
const capState = vi.hoisted(() => ({
  staffCountFor: (_officeId: string, _weekday: number) => 5,
  pfvCountFor: (_templateId: string, _weekday: number) => 0,
}));

const { mockToast } = vi.hoisted(() => ({
  mockToast: { warning: vi.fn(), success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

// 週生成 mutation の hoisted spy (呼び出し有無を検証する)。
const genState = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
}));

// 「コピー前に戻す」: その週の保存一覧と復元 (連携画面の「取り込み前に戻す」と同じ API)。
const snapState = vi.hoisted(() => ({
  snapshots: [] as Array<Record<string, unknown>>,
  hasCheckins: false,
  restore: vi.fn(),
}));
vi.mock('@/lib/queries/integrations', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  const actual = await importOriginal<typeof import('@/lib/queries/integrations')>();
  return {
    ...actual,
    useInboundSnapshots: () => ({
      data: { snapshots: snapState.snapshots, hasCheckins: snapState.hasCheckins },
    }),
    useRestoreInboundSnapshot: () => ({ mutateAsync: snapState.restore, isPending: false }),
  };
});

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

vi.mock('@dnd-kit/utilities', () => ({ CSS: { Translate: { toString: () => '' } } }));
vi.mock('sonner', () => ({ toast: mockToast }));
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: null, status: 'unauthenticated' }),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), back: vi.fn() }),
  usePathname: () => '/schedule',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('lucide-react', () => {
  const named: Record<string, () => React.ReactElement> = {};
  return new Proxy(named, {
    get: (target, prop) => {
      if (prop === '__esModule') return true;
      if (typeof prop !== 'string' || !/^[A-Z]/.test(prop)) return undefined;
      if (!(prop in target)) target[prop] = () => <span />;
      return target[prop];
    },
  });
});

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
vi.mock('@/components/ui/skeleton', () => ({ Skeleton: () => <div data-testid="skeleton" /> }));
vi.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    onClick,
    disabled,
    ...rest
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    [k: string]: unknown;
  }) => (
    <button onClick={onClick} disabled={disabled} {...rest}>
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
  return { ...actual, useQueries: (...args: unknown[]) => mockUseQueries(...args) };
});

vi.mock('@/lib/api/fetcher', () => ({ fetcher: vi.fn() }));

vi.mock('@/lib/queries/weekday_staff_capacity', () => ({
  useWeekdayStaffCapacityLookup: () => ({
    staffCountFor: (o: string, w: number) => capState.staffCountFor(o, w),
    managerCountFor: () => 0,
    courseCodesMax: 5,
    isLoading: false,
  }),
}));
vi.mock('@/lib/queries/pfv_course_presence', () => ({
  usePfvCoursePresenceLookup: () => ({
    pfvCountFor: (t: string, w: number) => capState.pfvCountFor(t, w),
    isLoading: false,
  }),
}));
vi.mock('@/lib/queries/offices', () => ({
  useOffices: (...args: unknown[]) => mockOffices(...args),
}));
vi.mock('@/lib/queries/patients', () => ({
  usePatients: (...args: unknown[]) => mockPatients(...args),
  useCreatePatient: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/staff', () => ({
  useStaffList: (...args: unknown[]) => mockStaffList(...args),
}));
vi.mock('@/lib/queries/visits', () => ({
  useVisits: (...args: unknown[]) => mockVisits(...args),
  useDeleteVisit: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateVisit: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/courses', () => ({
  useCourses: (...args: unknown[]) => mockCourses(...args),
  useUpdateCourse: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/place_and_fix', () => ({
  usePlaceAndFix: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/generate_week', () => ({
  useGenerateWeek: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useGenerateWeekOnly: () => ({ mutateAsync: genState.mutateAsync, isPending: false }),
}));
vi.mock('@/lib/queries/assign_staff_only', () => ({
  useAssignStaffOnly: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useApplyStaffReview: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/g21', () => ({
  useTogglePfvPin: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  useBulkPinPfvs: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/autoScheduleV2', () => ({
  useDiffAddProposalsMutation: () => ({
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    data: undefined,
    error: null,
    isSuccess: false,
  }),
  useFullOptimizeMutation: () => ({
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    data: undefined,
    error: null,
    isSuccess: false,
  }),
  useApplyIndividualMutation: () => ({
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    error: null,
    isSuccess: false,
  }),
  useResetToFixedMutation: () => ({
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    error: null,
    isSuccess: false,
  }),
  useApplyWeekOnlyMutation: () => ({
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    error: null,
    isSuccess: false,
  }),
  useUnassignAllStaffMutation: () => ({
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    error: null,
    isSuccess: false,
  }),
}));
vi.mock('@/lib/queries/staff-events', () => ({
  useWeekStaffEvents: () => ({ data: [], isLoading: false }),
  buildStaffEventsMap: () => new Map(),
  useUpdateEventForDrag: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/opLog', () => ({
  useOpLogState: () => ({ data: undefined, isLoading: false }),
  useUndoOpLog: () => ({ mutateAsync: vi.fn().mockResolvedValue(undefined), isPending: false }),
  useRedoOpLog: () => ({ mutateAsync: vi.fn().mockResolvedValue(undefined), isPending: false }),
  useInvalidateOpLog: () => vi.fn(),
  OP_LOG_STATE_KEY: 'op-log-state',
}));
vi.mock('@/lib/queries/visitMoveWeekOnly', () => ({
  useVisitMoveWeekOnly: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/schedulingSettings', () => ({
  useSchedulingSettings: () => ({ data: undefined, isLoading: false }),
  useUpdateSchedulingSettings: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/api/patientSync', () => ({
  useBulkSyncWeekToFixedMutation: () => ({
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    error: null,
    isSuccess: false,
  }),
  useBulkApplyWeekOnlyVisitChangesMutation: () => ({
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    error: null,
    isSuccess: false,
  }),
  useSyncWeekVisitsToFixedMutation: () => ({
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    error: null,
    isSuccess: false,
  }),
}));

// ─── Subject under test ───
import { CourseDayTablePanel } from '../CourseDayTablePanel';

function monday(year: number, month: number, day: number): Date {
  const d = new Date(year, month - 1, day, 0, 0, 0, 0);
  const dow = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - dow);
  return d;
}

interface SetupOpts {
  visits?: Array<Record<string, unknown>>;
}

function setupHooks(opts: SetupOpts = {}) {
  mockOffices.mockReturnValue({
    allOffices: [{ id: 'office-honten', name: '本店' }],
    isLoading: false,
  });
  mockPatients.mockReturnValue({ data: { items: [] }, isLoading: false });
  mockStaffList.mockReturnValue({ data: [], isLoading: false });
  mockUseQueries.mockReturnValue([{ data: [], isLoading: false }]);
  mockVisits.mockReturnValue({
    data: { items: opts.visits ?? [], truncated: false },
    isLoading: false,
  });
  mockCourses.mockReturnValue({ data: [], isLoading: false });
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CourseDayTablePanel weekStart={monday(2026, 5, 4)} officeId={null} canEdit={true} />
    </QueryClientProvider>,
  );
}

const CONFIRM_TITLE = 'この週は既に生成されています。再実行しますか？';

/** 「週を作る」→「固定訪問から生成」→「固定訪問から生成する」(= 従来の「週を生成」)。 */
function pressGenerateFromFixed() {
  fireEvent.click(screen.getByTestId('generate-week-button'));
  expect(screen.getByTestId('generate-week-button')).toHaveTextContent('週を作る');
  fireEvent.click(screen.getByTestId('make-week-how-fixed'));
  fireEvent.click(screen.getByText('固定訪問から生成する'));
}

const VISIT = {
  id: 'v1',
  course_id: 'course-C',
  patient_id: 'p1',
  patient_name: '患者1',
  start_time: '10:00',
  end_time: '10:30',
  visit_date: '2026-05-04',
  status: 'planned',
};

describe('CourseDayTablePanel — 「週を生成」再実行の確認ダイアログ (PO 2026-07-10)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    capState.staffCountFor = () => 5;
    capState.pfvCountFor = () => 0;
    genState.mutateAsync = vi.fn().mockResolvedValue({ visits_created: 0 });
    snapState.snapshots = [];
    snapState.hasCheckins = false;
    snapState.restore = vi.fn().mockResolvedValue({ restored: 3, wiped: 5 });
  });

  it('① 訪問ありで押す → ダイアログが出て mutation 未実行 → 「再実行する」で実行', async () => {
    setupHooks({ visits: [VISIT] });
    renderPanel();

    // まだダイアログは出ていない。
    expect(screen.queryByText(CONFIRM_TITLE)).not.toBeInTheDocument();

    pressGenerateFromFixed();

    // ダイアログが出る & mutation は未実行。
    expect(await screen.findByText(CONFIRM_TITLE)).toBeInTheDocument();
    expect(genState.mutateAsync).not.toHaveBeenCalled();

    // 「再実行する」で実行される。
    fireEvent.click(screen.getByTestId('generate-week-confirm-ok'));
    await waitFor(() => expect(genState.mutateAsync).toHaveBeenCalledTimes(1));
  });

  it('② 訪問 0 件で押す → ダイアログなしで即実行 (挙動不変)', async () => {
    setupHooks({ visits: [] });
    renderPanel();

    pressGenerateFromFixed();

    // ダイアログは出ず、即 mutation が走る。
    expect(screen.queryByText(CONFIRM_TITLE)).not.toBeInTheDocument();
    await waitFor(() => expect(genState.mutateAsync).toHaveBeenCalledTimes(1));
  });
});

describe('CourseDayTablePanel — 「コピー前に戻す」(copy-week-design-2026-09-30.md §4-6)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    snapState.snapshots = [];
    snapState.hasCheckins = false;
    snapState.restore = vi.fn().mockResolvedValue({ restored: 3, wiped: 5 });
  });

  it('一番新しい保存がコピー直前のときだけ出し、確認のうえ復元する', async () => {
    setupHooks({ visits: [VISIT] });
    snapState.snapshots = [
      {
        id: 'snap-copy',
        weekStart: '2026-05-04',
        kind: 'copy_week',
        visitsCount: 5,
        createdAt: '2026-05-01T01:00:00Z',
      },
    ];
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderPanel();
    // いつのコピーかを見せる (古い保存への押し間違いを減らす)
    const created = new Date('2026-05-01T01:00:00Z');
    expect(screen.getByTestId('undo-copy-week-button')).toHaveTextContent(
      `${created.getMonth() + 1}/${created.getDate()} ${created.getHours()}:00 のコピー前に戻す`,
    );
    fireEvent.click(screen.getByTestId('undo-copy-week-button'));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(String(confirmSpy.mock.calls[0]?.[0])).toContain('5/4(月)〜5/10(日) の週');
    await waitFor(() =>
      expect(snapState.restore).toHaveBeenCalledWith({ snapshotId: 'snap-copy' }),
    );
    confirmSpy.mockRestore();
  });

  it('一番新しい保存が取り込み直前なら出さない', () => {
    setupHooks({ visits: [VISIT] });
    snapState.snapshots = [
      {
        id: 'snap-smart',
        weekStart: '2026-05-04',
        kind: 'smart',
        visitsCount: 5,
        createdAt: '2026-05-02T01:00:00Z',
      },
      {
        id: 'snap-copy',
        weekStart: '2026-05-04',
        kind: 'copy_week',
        visitsCount: 5,
        createdAt: '2026-05-01T01:00:00Z',
      },
    ];
    renderPanel();
    expect(screen.queryByTestId('undo-copy-week-button')).not.toBeInTheDocument();
  });

  it.each([
    ['戻した後', { restoredAt: '2026-05-01T02:00:00Z' }, false],
    ['足すだけのコピー', { copyMode: 'add_only' }, false],
    ['今の週に打刻がある', {}, true],
  ])('%s は出さない', (_label, extra, hasCheckins) => {
    setupHooks({ visits: [VISIT] });
    snapState.snapshots = [
      {
        id: 'snap-copy',
        weekStart: '2026-05-04',
        kind: 'copy_week',
        visitsCount: 5,
        createdAt: '2026-05-01T01:00:00Z',
        copyMode: 'replace',
        ...extra,
      },
    ];
    snapState.hasCheckins = hasCheckins as boolean;
    renderPanel();
    expect(screen.queryByTestId('undo-copy-week-button')).not.toBeInTheDocument();
  });
});
