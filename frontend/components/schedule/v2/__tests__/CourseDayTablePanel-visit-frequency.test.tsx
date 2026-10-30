/**
 * CourseDayTablePanel — 訪問頻度 (隔週・月の第何週) と保留プール.
 *
 * biweekly-monthly-design-2026-10-30.md §3-3 / §4 (段 2 のレビュー):
 *   - 「効果を表示」を押す前から、お休みの週の方を「足りない」に数えない
 *     (GET visit-frequency-week の off_week)。
 *   - 訪問する週の希望回数は、その週に当たる枠の曜日数で数える
 *     (隔週・火=A・金=B・週 2 回 → どの週も希望 1)。
 *   - 毎週の方は今までどおり frequency_per_week で数える。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const { dndState, mockToast } = vi.hoisted(() => ({
  dndState: {
    capturedHandlers: { onDragEnd: undefined as undefined | ((e: unknown) => Promise<void>) },
  },
  mockToast: {
    warning: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

// ●未送信の帯 (SyncBar) はコースの表示でも出る (schedule-unit-switch 設計 D4)。
// この試験は帯の中身を見ないので空にする (帯の試験は CourseDayTablePanel-cockpit が持つ)。
vi.mock('@/components/schedule/v2/cockpit/SyncBar', () => ({ SyncBar: () => null }));
vi.mock('@dnd-kit/core', () => ({
  useDroppable: () => ({ isOver: false, setNodeRef: vi.fn() }),
  useDraggable: () => ({
    attributes: {},
    listeners: {},
    setNodeRef: vi.fn(),
    transform: null,
    isDragging: false,
  }),
  DndContext: ({
    children,
    onDragEnd,
  }: {
    children: React.ReactNode;
    onDragEnd?: (e: unknown) => Promise<void>;
  }) => {
    dndState.capturedHandlers.onDragEnd = onDragEnd;
    return <>{children}</>;
  },
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

// CourseDayTablePanel と間接 import の icon を網羅 (Proxy はワーカークラッシュするため明示列挙).
vi.mock('lucide-react', () => {
  const named: Record<string, () => React.ReactElement> = {
    Loader2: () => <span data-testid="loader" />,
    RefreshCw: () => <span data-testid="refresh-icon" />,
    UserCheck: () => <span data-testid="user-check-icon" />,
    Pin: () => <span data-testid="pin-icon" />,
    Undo2: () => <span data-testid="undo-icon" />,
    Redo2: () => <span data-testid="redo-icon" />,
  };
  return new Proxy(named, {
    get: (target, prop) => {
      if (prop === '__esModule') return true;
      // 'then' 等に関数を返すとモジュールが thenable 扱いされ await import が
      // 永久に解決しない。アイコン名 (PascalCase) だけ自動生成する。
      if (typeof prop !== 'string' || !/^[A-Z]/.test(prop)) return undefined;
      // 毎回新しい関数を返すと React がコンポーネント型の変更とみなして
      // 再マウントを繰り返すため、初回生成をキャッシュして識別性を安定させる。
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

vi.mock('@/components/ui/skeleton', () => ({
  Skeleton: () => <div data-testid="skeleton" />,
}));

vi.mock('@/components/ui/badge', () => ({
  Badge: ({
    children,
    className,
    ...rest
  }: {
    children: React.ReactNode;
    className?: string;
    [k: string]: unknown;
  }) => (
    <span className={className} {...rest}>
      {children}
    </span>
  ),
}));

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

vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({
    children,
    open,
    onOpenChange,
  }: {
    children: React.ReactNode;
    open?: boolean;
    onOpenChange?: (o: boolean) => void;
  }) =>
    open ? (
      <div data-testid="dialog-root" data-open={open}>
        {children}
        <button
          type="button"
          data-testid="dialog-close-shim"
          onClick={() => onOpenChange?.(false)}
        />
      </div>
    ) : null,
  DialogContent: ({ children, ...rest }: { children: React.ReactNode; [k: string]: unknown }) => (
    <div {...rest}>{children}</div>
  ),
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  DialogDescription: ({ children, id }: { children: React.ReactNode; id?: string }) => (
    <p id={id}>{children}</p>
  ),
}));

vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

const mockOffices = vi.fn();
const mockPatients = vi.fn();
const mockStaffList = vi.fn();
const mockUseQueries = vi.fn();
const mockVisits = vi.fn();
const mockCourses = vi.fn();
const mockPlaceAndFix = vi.fn();
const mockGenerateWeek = vi.fn();
const mockAssignStaffOnly = vi.fn();
const mockUpdateCourse = vi.fn();
const mockDeleteVisit = vi.fn();

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

vi.mock('@/lib/queries/weekday_staff_capacity', () => ({
  useWeekdayStaffCapacityLookup: () => ({
    staffCountFor: () => 5,
    managerCountFor: () => 0,
    courseCodesMax: 5,
    isLoading: false,
  }),
}));
vi.mock('@/lib/queries/pfv_course_presence', () => ({
  usePfvCoursePresenceLookup: () => ({
    pfvCountFor: () => 0,
    isLoading: false,
  }),
}));
vi.mock('@/lib/queries/offices', () => ({
  useOffices: (...args: unknown[]) => mockOffices(...args),
}));
vi.mock('@/lib/queries/patients', () => ({
  usePatients: (...args: unknown[]) => mockPatients(...args),
  // CreatePatientDialog (RegisterPatientButton 経由) が使用. noop で十分.
  useCreatePatient: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/staff', () => ({
  useStaffList: (...args: unknown[]) => mockStaffList(...args),
}));
vi.mock('@/lib/queries/visits', () => ({
  useVisits: (...args: unknown[]) => mockVisits(...args),
  useDeleteVisit: () => ({ mutateAsync: mockDeleteVisit, isPending: false }),
  useCreateVisit: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/courses', () => ({
  useCourses: (...args: unknown[]) => mockCourses(...args),
  useUpdateCourse: () => ({ mutateAsync: mockUpdateCourse, isPending: false }),
}));
vi.mock('@/lib/queries/place_and_fix', () => ({
  usePlaceAndFix: () => ({ mutateAsync: mockPlaceAndFix, isPending: false }),
}));
vi.mock('@/lib/queries/generate_week', () => ({
  useGenerateWeek: () => ({ mutateAsync: mockGenerateWeek, isPending: false }),
  useGenerateWeekOnly: () => ({ mutateAsync: mockGenerateWeek, isPending: false }),
}));
vi.mock('@/lib/queries/assign_staff_only', () => ({
  useAssignStaffOnly: () => ({ mutateAsync: mockAssignStaffOnly, isPending: false }),
  // Phase G-91: panel が useApplyStaffReview を直接呼ぶため noop mock が必要.
  useApplyStaffReview: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/g21', () => ({
  useSameAddressCandidates: () => ({ data: [], isLoading: false }),
  useSameAddressLinks: () => ({ data: [], isLoading: false }),
  useSetSameAddressLink: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteSameAddressLink: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useOfficeFeatureFlags: () => ({ data: [], isLoading: false }),
  useSetOfficeFeatureFlag: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteOfficeFeatureFlag: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
// BulkFixToPatternButton 内部で useApplyFromWeekBulk を呼ぶため必須 (QueryClient 不要化).
vi.mock('@/lib/queries/patient_fixed_visits', () => ({
  useFixedVisits: () => ({ data: [], isLoading: false }),
  useUpdateFixedVisits: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteFixedVisits: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useApplyFromWeek: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useApplyFromWeekBulk: () => ({ mutateAsync: vi.fn(), isPending: false }),
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
  useApplyWeekOnlyMutation: () => ({
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    error: null,
    isSuccess: false,
  }),
  // UnassignAllStaffButton (toolbar) が使用. noop で十分.
  useUnassignAllStaffMutation: () => ({
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    error: null,
    isSuccess: false,
  }),
}));
vi.mock('@/lib/queries/opLog', () => ({
  useOpLogState: () => ({ data: undefined, isLoading: false }),
  useUndoOpLog: () => ({
    mutateAsync: vi.fn().mockResolvedValue(undefined),
    isPending: false,
  }),
  useRedoOpLog: () => ({
    mutateAsync: vi.fn().mockResolvedValue(undefined),
    isPending: false,
  }),
  useInvalidateOpLog: () => vi.fn(),
  OP_LOG_STATE_KEY: 'op-log-state',
}));

vi.mock('@/lib/queries/schedulingSettings', () => ({
  useSchedulingSettings: () => ({ data: undefined, isLoading: false }),
  useUpdateSchedulingSettings: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('@/lib/queries/visitMoveWeekOnly', () => ({
  useVisitMoveWeekOnly: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), back: vi.fn() }),
  usePathname: () => '/schedule',
  useSearchParams: () => new URLSearchParams(),
}));
const mockUpdateEventDrag = vi.fn();
vi.mock('@/lib/queries/staff-events', () => ({
  useWeekStaffEvents: () => ({ data: [], isLoading: false }),
  buildStaffEventsMap: () => new Map(),
  useUpdateEventForDrag: () => ({ mutateAsync: mockUpdateEventDrag, isPending: false }),
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

const mockFrequencyWeek = vi.fn();
vi.mock('@/lib/queries/poolOverview', () => ({
  useVisitFrequencyWeek: (...args: unknown[]) => mockFrequencyWeek(...args),
  usePoolOverviewMutation: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    data: undefined,
    isPending: false,
    isSuccess: false,
    error: null,
  }),
}));

import { CourseDayTablePanel } from '../CourseDayTablePanel';

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
  mockPatients.mockReturnValue({
    data: { items: opts.patients ?? [] },
    isLoading: false,
  });
  mockStaffList.mockReturnValue({
    data: opts.staff ?? [],
    isLoading: false,
  });
  mockUseQueries.mockReturnValue([{ data: opts.templates ?? [], isLoading: false }]);
  mockVisits.mockReturnValue({
    data: { items: opts.visits ?? [], truncated: false },
    isLoading: false,
  });
  mockCourses.mockReturnValue({
    data: opts.courses ?? [],
    isLoading: false,
  });
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

const COMMON_TPL = { id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl };
const COMMON_COURSE = {
  id: 'course-A',
  iso_year: 2026,
  iso_week: 22,
  weekday: 0,
  code: 'A',
  office_id: 'office-honten',
  assigned_staff_id: null,
  course_status: 'course_fixed',
  deleted_at: null,
};

/** 全テスト共通: QueryClientProvider 配下で panel を描画する. */
function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 25)}
        officeId="office-honten"
        canEdit={true}
      />
    </QueryClientProvider>,
  );
}

const AB_PID = 'aaaaaaaa-1111-1111-1111-111111111111'; // 隔週・火=A・金=B・週 2 回
const OFF_PID = 'bbbbbbbb-2222-2222-2222-222222222222'; // 隔週・この週はお休み
const EVERY_PID = 'cccccccc-3333-3333-3333-333333333333'; // 毎週・週 2 回

function visitOf(id: string, patientId: string, date: string) {
  return {
    id,
    patient_id: patientId,
    patient_name: 'x',
    visit_date: date,
    start_time: '10:00:00',
    end_time: '10:30:00',
    primary_staff_id: null,
    course_id: 'course-A',
    required_staff_count: 1,
    visit_group_id: null,
    type: 'regular',
    status: 'planned',
    source: 'allocate',
  };
}

const PATIENTS = [
  {
    id: AB_PID,
    name: '隔週 花子',
    kana: null,
    status: 'active',
    requires_multiple_staff: false,
    weekly_pattern: {
      frequency_per_week: 2,
      service_minutes: 30,
      visit_frequency: 'biweekly',
      biweekly_anchor: '2026-05-25',
    },
  },
  {
    id: OFF_PID,
    name: '隔週 次郎',
    kana: null,
    status: 'active',
    requires_multiple_staff: false,
    weekly_pattern: {
      frequency_per_week: 1,
      service_minutes: 30,
      visit_frequency: 'biweekly',
      biweekly_anchor: '2026-05-18',
    },
  },
  {
    id: EVERY_PID,
    name: '毎週 三郎',
    kana: null,
    status: 'active',
    requires_multiple_staff: false,
    weekly_pattern: { frequency_per_week: 2, service_minutes: 30 },
  },
];

function setupWeek(frequencyItems: Array<Record<string, unknown>> | null) {
  setupHooks({
    templates: [COMMON_TPL],
    courses: [COMMON_COURSE],
    visits: [visitOf('v-ab', AB_PID, '2026-05-26'), visitOf('v-ev', EVERY_PID, '2026-05-26')],
    patients: PATIENTS,
  });
  mockFrequencyWeek.mockReturnValue({
    data: frequencyItems == null ? undefined : { week_start: '2026-05-25', items: frequencyItems },
    isLoading: false,
  });
}

describe('CourseDayTablePanel — 訪問頻度と保留プール (visit-frequency-week)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('最初の表示から: A/B の方は希望 1 で満ち、お休みの週の方は数えず、毎週の方は今までどおり', () => {
    setupWeek([
      { patient_id: AB_PID, off_week: false, desired_count: 1 },
      { patient_id: OFF_PID, off_week: true, desired_count: 0 },
    ]);
    renderPanel();
    // 週の月曜で問い合わせる
    expect(mockFrequencyWeek).toHaveBeenCalledWith('2026-05-25', { enabled: true });
    // 隔週 A/B: 希望 1 / 配置 1 → 足りなくない (プールに出ない)
    expect(screen.queryByTestId(`patient-card-shortage-badge-${AB_PID}`)).toBeNull();
    // お休みの週: 足りないに数えず、「お休みの週 1名」に出す
    expect(screen.queryByTestId(`patient-card-shortage-badge-${OFF_PID}`)).toBeNull();
    expect(screen.getByTestId('pool-off-week-chip').textContent).toMatch(/お休みの週 1名/);
    // 毎週: 希望 2 / 配置 1 → 不足 1 (変わらない)
    const badge = screen.getByTestId(`patient-card-shortage-badge-${EVERY_PID}`);
    expect(badge.textContent).toMatch(/不足 1/);
  });

  it('判定がまだ無い (読み込み中・権限なし) ときは今までどおり frequency_per_week で数える', () => {
    setupWeek(null);
    renderPanel();
    expect(screen.getByTestId(`patient-card-shortage-badge-${AB_PID}`).textContent).toMatch(
      /不足 1/,
    );
    expect(screen.getByTestId(`patient-card-shortage-badge-${OFF_PID}`).textContent).toMatch(
      /不足 1/,
    );
    expect(screen.queryByTestId('pool-off-week-chip')).toBeNull();
  });

  it('前の週の判定 (week_start が違う) は使わない', () => {
    setupWeek(null);
    mockFrequencyWeek.mockReturnValue({
      data: {
        week_start: '2026-05-18',
        items: [{ patient_id: OFF_PID, off_week: true, desired_count: 0 }],
      },
      isLoading: false,
    });
    renderPanel();
    expect(screen.queryByTestId('pool-off-week-chip')).toBeNull();
    expect(screen.getByTestId(`patient-card-shortage-badge-${OFF_PID}`)).toBeTruthy();
  });

  it('週のタブでも、この週がお休みの週の方を薄く添える (設計 §4)', () => {
    setupWeek([
      { patient_id: AB_PID, off_week: false, desired_count: 1 },
      { patient_id: OFF_PID, off_week: true, desired_count: 0 },
    ]);
    renderPanel();
    fireEvent.click(screen.getByTestId('course-day-tab-week'));
    const note = screen.getByTestId('week-off-week-note');
    expect(note.textContent).toContain('隔週 次郎（隔週・お休みの週）');
    expect(note.textContent).not.toContain('隔週 花子');
  });
});
