/**
 * CourseDayTablePanel — Phase G-44 テスト.
 *
 * 「希望訪問パターン (= weekly_pattern.frequency_per_week) を正としたプール判定」
 * の動作検証. ディレクター指示:
 *   - 希望 N 回 vs 実 X 件で「不足あり」患者だけ pool に残す
 *   - PatientCard に「希望 N / 配置 X、 不足 Y」 表示
 *   - 過不足なし (actual >= desired) は pool から除外
 *   - 希望未設定 (frequency_per_week=0) は pool 対象外
 *   - requires_multiple_staff=true は slot 単位判定 (= 既存挙動維持)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

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
  const Stub = () => <span />;
  return {
    ChevronLeft: Stub,
    ChevronRight: Stub,
    Inbox: Stub,
    AlertTriangle: Stub,
    Info: Stub,
    User: Stub,
    Users: Stub,
    X: Stub,
    Plus: Stub,
    ChevronDown: Stub,
    Star: Stub,
    Loader2: Stub,
    RefreshCw: Stub,
    UserCheck: Stub,
    Pin: Stub,
    ArrowRight: Stub,
    CheckCircle2: Stub,
    DatabaseZap: Stub,
    Lock: Stub,
    Unlock: Stub,
    Sparkles: Stub,
    Pencil: Stub,
    Bell: Stub,
    Check: Stub,
    ExternalLink: Stub,
    UserX: Stub,
    CalendarRange: Stub,
    ListChecks: Stub,
  };
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

vi.mock('@/lib/queries/offices', () => ({
  useOffices: (...args: unknown[]) => mockOffices(...args),
}));
vi.mock('@/lib/queries/patients', () => ({
  usePatients: (...args: unknown[]) => mockPatients(...args),
}));
vi.mock('@/lib/queries/staff', () => ({
  useStaffList: (...args: unknown[]) => mockStaffList(...args),
}));
vi.mock('@/lib/queries/visits', () => ({
  useVisits: (...args: unknown[]) => mockVisits(...args),
  useDeleteVisit: () => ({ mutateAsync: mockDeleteVisit, isPending: false }),
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
}));
vi.mock('@/lib/queries/g21', () => ({
  useTogglePfvPin: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  useBulkPinPfvs: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
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

const SHORTAGE_PID = '11111111-1111-1111-1111-111111111111'; // 希望 3 / 配置 1 (不足 2)
const FULFILLED_PID = '22222222-2222-2222-2222-222222222222'; // 希望 2 / 配置 2 (不足 0)
const NO_DESIRE_PID = '33333333-3333-3333-3333-333333333333'; // 希望未設定
const MULTI_PID = '44444444-4444-4444-4444-444444444444'; // requires_multiple_staff

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

describe('CourseDayTablePanel — Phase G-44 「希望 vs 実」 pool 判定', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('G44-1. 希望 3 回 vs 実 1 件 (不足あり) → pool に表示され「不足 2」 + 「希望 3 / 配置 1」が出る', () => {
    setupHooks({
      templates: [COMMON_TPL],
      courses: [COMMON_COURSE],
      visits: [
        {
          id: 'v-1',
          patient_id: SHORTAGE_PID,
          patient_name: '不足さん',
          visit_date: '2026-05-25',
          start_time: '10:00:00',
          primary_staff_id: null,
          course_id: 'course-A',
          required_staff_count: 1,
          visit_group_id: null,
          type: 'regular',
          status: 'planned',
          source: 'allocate',
          end_time: '10:30:00',
        },
      ],
      patients: [
        {
          id: SHORTAGE_PID,
          name: '不足 太郎',
          kana: null,
          status: 'active',
          requires_multiple_staff: false,
          weekly_pattern: { frequency_per_week: 3, service_minutes: 30 },
        },
      ],
    });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 25)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    // 不足バッジ
    const badge = screen.getByTestId(`patient-card-shortage-badge-${SHORTAGE_PID}`);
    expect(badge.textContent).toMatch(/不足 2/);
    // 「希望 N / 配置 X」 ラベル
    const label = screen.getByTestId(`patient-card-shortage-label-${SHORTAGE_PID}`);
    expect(label.textContent).toMatch(/希望 3/);
    expect(label.textContent).toMatch(/配置 1/);
    // shortage コンテナの data-shortage 属性
    const container = screen.getByTestId(`patient-card-shortage-${SHORTAGE_PID}`);
    expect(container.getAttribute('data-shortage')).toBe('2');
  });

  it('G44-2. 希望 2 回 vs 実 2 件 (過不足なし) → pool から除外される (PatientCard 自体描画なし)', () => {
    setupHooks({
      templates: [COMMON_TPL],
      courses: [COMMON_COURSE],
      visits: [
        {
          id: 'v-1',
          patient_id: FULFILLED_PID,
          patient_name: '充足さん',
          visit_date: '2026-05-25',
          start_time: '10:00:00',
          primary_staff_id: null,
          course_id: 'course-A',
          required_staff_count: 1,
          visit_group_id: null,
          type: 'regular',
          status: 'planned',
          source: 'allocate',
          end_time: '10:30:00',
        },
        {
          id: 'v-2',
          patient_id: FULFILLED_PID,
          patient_name: '充足さん',
          visit_date: '2026-05-26',
          start_time: '10:00:00',
          primary_staff_id: null,
          course_id: 'course-A',
          required_staff_count: 1,
          visit_group_id: null,
          type: 'regular',
          status: 'planned',
          source: 'allocate',
          end_time: '10:30:00',
        },
      ],
      patients: [
        {
          id: FULFILLED_PID,
          name: '充足 花子',
          kana: null,
          status: 'active',
          requires_multiple_staff: false,
          weekly_pattern: { frequency_per_week: 2, service_minutes: 30 },
        },
      ],
    });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 25)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    // pool 内に PatientCard 自体が描画されない (= pool 対象外).
    // shortage badge / label が無いことで間接検証.
    expect(screen.queryByTestId(`patient-card-shortage-${FULFILLED_PID}`)).toBeNull();
    expect(screen.queryByTestId(`patient-card-shortage-badge-${FULFILLED_PID}`)).toBeNull();
  });

  it('G44-3. 希望未設定 (frequency_per_week=0) → pool 対象外 (= 既存挙動維持)', () => {
    setupHooks({
      templates: [COMMON_TPL],
      patients: [
        {
          id: NO_DESIRE_PID,
          name: '希望なし 一郎',
          kana: null,
          status: 'active',
          requires_multiple_staff: false,
          weekly_pattern: { frequency_per_week: 0, service_minutes: 30 },
        },
      ],
    });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 25)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    // pool に表示されない (= shortage 表示の testid が存在しない).
    expect(screen.queryByTestId(`patient-card-shortage-${NO_DESIRE_PID}`)).toBeNull();
  });

  it('G44-4. requires_multiple_staff=true 患者は希望判定をスキップし、 shortage 表示を出さない', () => {
    setupHooks({
      templates: [COMMON_TPL],
      patients: [
        {
          id: MULTI_PID,
          name: 'マルチ 次郎',
          kana: null,
          status: 'active',
          requires_multiple_staff: true,
          // frequency_per_week は設定済みでも slot 単位判定が優先.
          weekly_pattern: { frequency_per_week: 2, service_minutes: 60 },
        },
      ],
    });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 25)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    // 複数体制患者は slot 単位 (slot-0, slot-1) で別カード描画され、
    // shortage コンテナは出ない.
    expect(screen.queryByTestId(`patient-card-shortage-${MULTI_PID}`)).toBeNull();
    // 代わりに slot 別カード testid が存在する (= multi-staff 描画継続の証拠).
    expect(screen.getByTestId(`patient-card-slot-0-${MULTI_PID}`)).toBeInTheDocument();
    expect(screen.getByTestId(`patient-card-slot-1-${MULTI_PID}`)).toBeInTheDocument();
  });

  it('G44-5. 希望 3 / 配置 0 (= 全くまだ配置されていない) → 不足 3 表示', () => {
    setupHooks({
      templates: [COMMON_TPL],
      patients: [
        {
          id: SHORTAGE_PID,
          name: '未配置 太郎',
          kana: null,
          status: 'active',
          requires_multiple_staff: false,
          weekly_pattern: { frequency_per_week: 3, service_minutes: 30 },
        },
      ],
    });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 25)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    const badge = screen.getByTestId(`patient-card-shortage-badge-${SHORTAGE_PID}`);
    expect(badge.textContent).toMatch(/不足 3/);
    const label = screen.getByTestId(`patient-card-shortage-label-${SHORTAGE_PID}`);
    expect(label.textContent).toMatch(/配置 0/);
  });
});
