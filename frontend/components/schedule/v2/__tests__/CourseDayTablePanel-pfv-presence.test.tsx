/**
 * CourseDayTablePanel — PO 2026-07-09「PFV に含まれるコースを正とする」列表示の回帰。
 *
 * カバー:
 *   ① staffCount=0 でも PFV presence がある template の列が出る
 *   ② 当該日に visit がある template の列が出る (staff=0, pfv=0 でも)
 *   ③ スタッフ不足バナーが出る / 足りていれば出ない
 *   ④ 担当不在 (assigned_staff_id が削除済み staff を指す) 列ヘッダ警告
 *
 * staffCountFor / pfvCountFor は hoisted な可変フックにして各テストで差し替える。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// ─── 可変ステート (staffCountFor / pfvCountFor をテストごとに差し替える) ───
const capState = vi.hoisted(() => ({
  staffCountFor: (_officeId: string, _weekday: number) => 5,
  pfvCountFor: (_templateId: string, _weekday: number) => 0,
}));

// ─── モック ──────────────────────────────────────────────────────────────────

const { mockToast } = vi.hoisted(() => ({
  mockToast: { warning: vi.fn(), success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

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
  useGenerateWeekOnly: () => ({ mutateAsync: vi.fn(), isPending: false }),
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

// ─── Subject under test ─────────────────────────────────────────────────────

import { CourseDayTablePanel } from '../CourseDayTablePanel';

// ─── helpers ────────────────────────────────────────────────────────────────

function monday(year: number, month: number, day: number): Date {
  const d = new Date(year, month - 1, day, 0, 0, 0, 0);
  const dow = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - dow);
  return d;
}

interface SetupOpts {
  staff?: Array<Record<string, unknown>>;
  visits?: Array<Record<string, unknown>>;
  courses?: Array<Record<string, unknown>>;
  templates?: Array<Record<string, unknown>>;
  patients?: Array<Record<string, unknown>>;
  offices?: Array<{ id: string; name: string }>;
}

function setupHooks(opts: SetupOpts = {}) {
  mockOffices.mockReturnValue({
    allOffices: opts.offices ?? [{ id: 'office-honten', name: '本店' }],
    isLoading: false,
  });
  mockPatients.mockReturnValue({ data: { items: opts.patients ?? [] }, isLoading: false });
  mockStaffList.mockReturnValue({ data: opts.staff ?? [], isLoading: false });
  mockUseQueries.mockReturnValue([{ data: opts.templates ?? [], isLoading: false }]);
  mockVisits.mockReturnValue({
    data: { items: opts.visits ?? [], truncated: false },
    isLoading: false,
  });
  mockCourses.mockReturnValue({ data: opts.courses ?? [], isLoading: false });
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

// weekStart monday(2026,5,4) は ISO 2026-W19 (既存テストと同一)。
function renderPanel(officeId: string | null = null) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CourseDayTablePanel weekStart={monday(2026, 5, 4)} officeId={officeId} canEdit={true} />
    </QueryClientProvider>,
  );
}

// ─── Tests ──────────────────────────────────────────────────────────────────

describe('CourseDayTablePanel — PFV presence 列表示 (PO 2026-07-09)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // 既定に戻す (十分なスタッフ・PFV 無し)。
    capState.staffCountFor = () => 5;
    capState.pfvCountFor = () => 0;
  });

  it('① staffCount=0 でも PFV presence がある template の列が出る', () => {
    // スタッフ 0 名。C は PFV があり、D は PFV 無し。
    capState.staffCountFor = () => 0;
    capState.pfvCountFor = (t: string) => (t === 'tpl-C' ? 2 : 0);
    setupHooks({
      templates: [
        { id: 'tpl-C', office_id: 'office-honten', label: 'C', ...baseTpl },
        { id: 'tpl-D', office_id: 'office-honten', label: 'D', ...baseTpl },
      ],
    });
    renderPanel();
    fireEvent.click(screen.getByTestId('course-day-tab-0'));
    // PFV のある C は列が出る。PFV も staff も無い D は出ない。
    expect(screen.getByTestId('tl-col-tpl-C:0')).toBeInTheDocument();
    expect(screen.queryByTestId('tl-col-tpl-D:0')).not.toBeInTheDocument();
  });

  it('② staff=0 / PFV=0 でも当該日に visit がある template の列が出る', () => {
    capState.staffCountFor = () => 0;
    capState.pfvCountFor = () => 0;
    setupHooks({
      templates: [{ id: 'tpl-C', office_id: 'office-honten', label: 'C', ...baseTpl }],
      courses: [
        {
          id: 'course-C',
          iso_year: 2026,
          iso_week: 19,
          weekday: 0,
          code: 'C',
          office_id: 'office-honten',
          assigned_staff_id: null,
          course_status: 'course_fixed',
          deleted_at: null,
        },
      ],
      visits: [
        {
          id: 'v1',
          course_id: 'course-C',
          patient_id: 'p1',
          patient_name: '患者1',
          start_time: '10:00',
          end_time: '10:30',
          visit_date: '2026-05-04',
          status: 'planned',
        },
      ],
    });
    renderPanel();
    fireEvent.click(screen.getByTestId('course-day-tab-0'));
    expect(screen.getByTestId('tl-col-tpl-C:0')).toBeInTheDocument();
  });

  it('③ スタッフ不足バナー: 表示 A-E 列数 > 稼働スタッフ数 のとき出る / 足りていれば出ない', () => {
    // 不足ケース: staff 0 名だが PFV で C/D の 2 列が出る → バナー。
    capState.staffCountFor = () => 0;
    capState.pfvCountFor = () => 2;
    setupHooks({
      templates: [
        { id: 'tpl-C', office_id: 'office-honten', label: 'C', ...baseTpl },
        { id: 'tpl-D', office_id: 'office-honten', label: 'D', ...baseTpl },
      ],
    });
    const { unmount } = renderPanel();
    fireEvent.click(screen.getByTestId('course-day-tab-0'));
    expect(screen.getByTestId('staff-shortage-banner')).toBeInTheDocument();
    expect(screen.getByText(/スタッフ不足/)).toBeInTheDocument();
    unmount();

    // 充足ケース: staff 5 名で A/B の 2 列 → 2 <= 5 でバナー無し。
    vi.clearAllMocks();
    capState.staffCountFor = () => 5;
    capState.pfvCountFor = () => 0;
    setupHooks({
      templates: [
        { id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl },
        { id: 'tpl-B', office_id: 'office-honten', label: 'B', ...baseTpl },
      ],
    });
    renderPanel();
    fireEvent.click(screen.getByTestId('course-day-tab-0'));
    expect(screen.queryByTestId('staff-shortage-banner')).not.toBeInTheDocument();
  });

  it('④ 担当不在: assigned_staff_id が削除済み staff を指す列ヘッダに（担当不在）を出す', () => {
    // staff 一覧に 'ghost' は存在しない (= 削除済み)。course は ghost を指す。
    capState.staffCountFor = () => 5;
    capState.pfvCountFor = () => 0;
    setupHooks({
      offices: [{ id: 'office-honten', name: '本店' }],
      staff: [
        {
          id: 'staff-live',
          name: '生存 太郎',
          kana: 'セイゾン',
          status: 'active',
          role: 'staff',
          primary_office_id: 'office-honten',
          is_trainee: false,
        },
      ],
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      courses: [
        {
          id: 'course-A',
          iso_year: 2026,
          iso_week: 19,
          weekday: 0,
          code: 'A',
          office_id: 'office-honten',
          assigned_staff_id: 'ghost',
          course_status: 'course_fixed',
          deleted_at: null,
        },
      ],
    });
    renderPanel('office-honten');
    fireEvent.click(screen.getByTestId('course-day-tab-0'));
    expect(screen.getByTestId('tl-col-tpl-A:0')).toBeInTheDocument();
    expect(screen.getByText('（担当不在）')).toBeInTheDocument();
  });
});
