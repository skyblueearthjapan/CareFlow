/**
 * CourseDayTablePanel — Wave 38 統合テスト.
 *
 * 「相方の現在地」可視化 (FE-only, BE 変更なし) のデータ配線を検証.
 *
 * カバーするシナリオ:
 *   W38-1. visit_group_id ペア (両 slot 配置済) → 各 visit セルに
 *          「相方: 本店-{他コース} {time}」が描画される (cell mode)
 *   W38-2. multi 患者で片方のみ配置 + 相方 pool 残存 →
 *          配置済み visit セルに「相方: プール」 + 警告色
 *   W38-3. multi 患者で片方のみ配置 → プール残カードに
 *          「{office}-{label} {time}」併記 (PatientCard partnerLocationLabel)
 *   W38-4. 通常患者 (multi=false) の visit セル → 相方注記なし + 左ボーダーなし (regression)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

// ── モック (CourseDayTablePanel-w37-p3c.test.tsx と同じ構成) ────────────────────

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

vi.mock('lucide-react', () => ({
  ChevronLeft: () => <span />,
  ChevronRight: () => <span />,
  Inbox: () => <span />,
  AlertTriangle: () => <span />,
  Info: () => <span />,
  User: () => <span />,
  Users: () => <span />,
  X: () => <span />,
  Plus: () => <span />,
  ChevronDown: () => <span />,
  Star: () => <span />,
  Loader2: () => <span data-testid="loader" />,
  RefreshCw: () => <span data-testid="refresh-icon" />,
  UserCheck: () => <span data-testid="user-check-icon" />,
  Pin: () => <span data-testid="pin-icon" />,
  ArrowRight: () => <span />,
  CheckCircle2: () => <span />,
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

vi.mock('@/components/ui/badge', () => ({
  Badge: ({
    children,
    className,
    variant,
    ...rest
  }: {
    children: React.ReactNode;
    className?: string;
    variant?: string;
    [k: string]: unknown;
  }) => (
    <span data-badge-variant={variant} className={className} {...rest}>
      {children}
    </span>
  ),
}));

vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ children, open }: { children: React.ReactNode; open?: boolean }) =>
    open ? <div data-testid="dialog-root">{children}</div> : null,
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
// Phase G-21 T4: useTogglePfvPin は内部で useMutation を呼ぶため必須.
vi.mock('@/lib/queries/g21', () => ({
  useTogglePfvPin: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
}));
// Wave 41 v2: autoScheduleV2 モック (useMutation を直接呼ぶため必須).
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
}));
// Wave 39: staff-events モック (W39 で useUpdateEventForDrag が追加されたため必須).
const mockUpdateEventDrag = vi.fn();
vi.mock('@/lib/queries/staff-events', () => ({
  useWeekStaffEvents: () => ({ data: [], isLoading: false }),
  buildStaffEventsMap: () => new Map(),
  useUpdateEventForDrag: () => ({ mutateAsync: mockUpdateEventDrag, isPending: false }),
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

const PATIENT_MULTI = '11111111-1111-1111-1111-111111111111';
const PATIENT_NORMAL = '22222222-2222-2222-2222-222222222222';

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

describe('CourseDayTablePanel — Wave 38 「相方の現在地」可視化', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('W38-1. visit_group ペア (両 slot 配置済) → 各 visit セルに「相方: 本店-X HH:MM」が描画される', () => {
    setupHooks({
      offices: [{ id: 'office-honten', name: '本店' }],
      templates: [
        { id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl },
        { id: 'tpl-B', office_id: 'office-honten', label: 'B', ...baseTpl },
      ],
      courses: [
        {
          id: 'course-A',
          iso_year: 2026,
          iso_week: 19,
          weekday: 0,
          code: 'A',
          office_id: 'office-honten',
          assigned_staff_id: null,
          course_status: 'course_fixed',
          deleted_at: null,
        },
        {
          id: 'course-B',
          iso_year: 2026,
          iso_week: 19,
          weekday: 0,
          code: 'B',
          office_id: 'office-honten',
          assigned_staff_id: null,
          course_status: 'course_fixed',
          deleted_at: null,
        },
      ],
      visits: [
        {
          id: 'v-pair-A',
          patient_id: PATIENT_MULTI,
          patient_name: '田中 太郎',
          visit_date: '2026-05-04',
          start_time: '10:00:00',
          primary_staff_id: null,
          course_id: 'course-A',
          required_staff_count: 2,
          visit_group_id: 'grp-1',
          type: 'regular',
          status: 'planned',
          source: 'allocate',
          end_time: '11:00:00',
        },
        {
          id: 'v-pair-B',
          patient_id: PATIENT_MULTI,
          patient_name: '田中 太郎',
          visit_date: '2026-05-04',
          start_time: '10:00:00',
          primary_staff_id: null,
          course_id: 'course-B',
          required_staff_count: 2,
          visit_group_id: 'grp-1',
          type: 'regular',
          status: 'planned',
          source: 'allocate',
          end_time: '11:00:00',
        },
      ],
      patients: [
        {
          id: PATIENT_MULTI,
          name: '田中 太郎',
          status: 'active',
          requires_multiple_staff: true,
        },
      ],
    });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    // v-pair-A セル → 相方は course-B (label B) → "相方: 本店-B 10:00"
    const partnerA = screen.getByTestId('course-occupant-partner-location-v-pair-A');
    expect(partnerA.textContent).toBe('相方: 本店-B 10:00');
    expect(partnerA.getAttribute('data-partner-location-kind')).toBe('cell');
    // v-pair-B セル → 相方は course-A (label A) → "相方: 本店-A 10:00"
    const partnerB = screen.getByTestId('course-occupant-partner-location-v-pair-B');
    expect(partnerB.textContent).toBe('相方: 本店-A 10:00');
    expect(partnerB.getAttribute('data-partner-location-kind')).toBe('cell');
  });

  it('W38-2. multi 患者で片方のみ配置 + 相方 pool → visit セルに「相方: プール」 + 警告色', () => {
    setupHooks({
      offices: [{ id: 'office-honten', name: '本店' }],
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      courses: [
        {
          id: 'course-A',
          iso_year: 2026,
          iso_week: 19,
          weekday: 0,
          code: 'A',
          office_id: 'office-honten',
          assigned_staff_id: null,
          course_status: 'course_fixed',
          deleted_at: null,
        },
      ],
      visits: [
        {
          id: 'v-orphan',
          patient_id: PATIENT_MULTI,
          patient_name: '山田 花子',
          visit_date: '2026-05-04',
          start_time: '10:00:00',
          primary_staff_id: null,
          course_id: 'course-A',
          required_staff_count: 1,
          visit_group_id: null,
          type: 'regular',
          status: 'planned',
          source: 'allocate',
          end_time: '11:00:00',
        },
      ],
      patients: [
        {
          id: PATIENT_MULTI,
          name: '山田 花子',
          status: 'active',
          requires_multiple_staff: true,
        },
      ],
    });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    const partnerEl = screen.getByTestId('course-occupant-partner-location-v-orphan');
    expect(partnerEl.textContent).toBe('相方: プール');
    expect(partnerEl.className).toContain('text-warning');
    expect(partnerEl.getAttribute('data-partner-location-kind')).toBe('pool');
  });

  it('W38-3. multi 患者の片方のみ配置 → プール残カードに "本店-A 10:00" 併記', () => {
    setupHooks({
      offices: [{ id: 'office-honten', name: '本店' }],
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      courses: [
        {
          id: 'course-A',
          iso_year: 2026,
          iso_week: 19,
          weekday: 0,
          code: 'A',
          office_id: 'office-honten',
          assigned_staff_id: null,
          course_status: 'course_fixed',
          deleted_at: null,
        },
      ],
      visits: [
        {
          id: 'v-orphan',
          patient_id: PATIENT_MULTI,
          patient_name: '山田 花子',
          visit_date: '2026-05-04',
          start_time: '10:00:00',
          primary_staff_id: null,
          course_id: 'course-A',
          required_staff_count: 1,
          visit_group_id: null,
          type: 'regular',
          status: 'planned',
          source: 'allocate',
          end_time: '11:00:00',
        },
      ],
      patients: [
        {
          id: PATIENT_MULTI,
          name: '山田 花子',
          status: 'active',
          requires_multiple_staff: true,
          weekly_pattern: { service_minutes: 60 },
        },
      ],
    });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    // プール残カード = slot 1 (slot 0 が配置済)。
    // PatientCard 内に partner-location ラベルが描画される.
    const locEl = screen.getByTestId(`patient-card-partner-location-${PATIENT_MULTI}`);
    expect(locEl).toBeInTheDocument();
    expect(locEl.textContent).toBe('本店-A 10:00');
  });

  it('W38-4. 通常患者 (multi=false) の visit セル → 相方注記なし + 左ボーダーなし (regression)', () => {
    setupHooks({
      offices: [{ id: 'office-honten', name: '本店' }],
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      courses: [
        {
          id: 'course-A',
          iso_year: 2026,
          iso_week: 19,
          weekday: 0,
          code: 'A',
          office_id: 'office-honten',
          assigned_staff_id: null,
          course_status: 'course_fixed',
          deleted_at: null,
        },
      ],
      visits: [
        {
          id: 'v-norm',
          patient_id: PATIENT_NORMAL,
          patient_name: '佐藤 一郎',
          visit_date: '2026-05-04',
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
          id: PATIENT_NORMAL,
          name: '佐藤 一郎',
          status: 'active',
          requires_multiple_staff: false,
        },
      ],
    });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    // 注記なし
    expect(screen.queryByTestId('course-occupant-partner-location-v-norm')).not.toBeInTheDocument();
    // 左ボーダーなし (= multi-staff 属性が付かない)
    const nameEl = screen.getByTestId('course-occupant-name-v-norm');
    const groupParent = nameEl.closest('[data-multi-staff="true"]') as HTMLElement | null;
    expect(groupParent).toBeNull();
  });
});
