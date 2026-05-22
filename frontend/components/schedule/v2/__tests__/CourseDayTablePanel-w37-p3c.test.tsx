/**
 * CourseDayTablePanel — Wave 37 Phase 3-C テスト.
 *
 * カバーするシナリオ:
 *  P3C-1. 通常患者 (requires_multiple_staff=false) の D&D → staff_count=1 +
 *         course_template_id (旧形式単数) で place-and-fix 呼出 (regression)
 *  P3C-2. 複数対応患者 (requires_multiple_staff=true) の D&D →
 *         PartnerCourseDialog が表示される (place-and-fix はまだ呼ばない)
 *  P3C-3. ダイアログで相方を確定 → staff_count=2 + course_template_ids: [a, b]
 *         配列形式で place-and-fix 呼出
 *  P3C-4. 同 office に他 template が無い → ダイアログにエラー文が出て確定不可
 *  P3C-5. visit_group_id 持ちの visit を D&D 移動しようとする → toast 警告で阻止
 *  P3C-6. visit_group_id なしの通常 visit の移動は既存挙動維持 (regression)
 *  P3C-7. assignedSlotsByPatient マップ (data 属性 シリアライズ) が正しく構築:
 *         - visit_group_id 持ち (2 件) → slot 0/1 両方埋まり
 *         - 単独 visit → slot 0 のみ
 *  P3C-8. visit_group_id 持ちの occupant に ①/② バッジが描画される
 *  P3C-9. requires_multiple_staff=true で partner 不在 → 「複数 ① のみ」表示
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

// ─── モック (CourseDayTablePanel.test.tsx と同じ構成) ────────────────────────

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

// shadcn/ui Dialog: Radix Portal は jsdom で扱いにくいので素朴な div に差し替え.
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

// ─── Hooks ───
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

const PATIENT_UUID = '99999999-9999-9999-9999-999999999999';
const PATIENT_UUID_2 = '88888888-8888-8888-8888-888888888888';

// ─── Tests ──────────────────────────────────────────────────────────────────

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

describe('CourseDayTablePanel — W37 Phase 3-C', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('P3C-1. 通常患者 (requires_multiple_staff=false) の D&D → staff_count=1 + course_template_id (単数) で呼出 (regression)', async () => {
    mockPlaceAndFix.mockResolvedValue({
      visit: {},
      fixed_visit: null,
      visits: [],
      fixed_visits: [],
      visit_group_id: null,
    });
    setupHooks({
      templates: [
        { id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl },
        { id: 'tpl-B', office_id: 'office-honten', label: 'B', ...baseTpl },
      ],
      patients: [
        {
          id: PATIENT_UUID,
          name: '鈴木 花子',
          kana: null,
          status: 'active',
          weekly_pattern: { service_minutes: 60 },
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
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: `pool-patient:${PATIENT_UUID}` },
      over: { id: 'course-day-cell:0:tpl-A:09:30' },
    });
    expect(mockPlaceAndFix).toHaveBeenCalledOnce();
    const arg = mockPlaceAndFix.mock.calls[0][0];
    expect(arg.staff_count).toBe(1);
    expect(arg.course_template_id).toBe('tpl-A');
    // 旧形式 (単数) を使うため course_template_ids は付かない
    expect(arg.course_template_ids).toBeUndefined();
    // ダイアログは出ない
    expect(screen.queryByTestId('partner-course-dialog')).not.toBeInTheDocument();
  });

  it('P3C-2. 複数対応患者 (requires_multiple_staff=true) の D&D → ダイアログ表示 + place-and-fix は呼ばない', async () => {
    setupHooks({
      templates: [
        { id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl },
        { id: 'tpl-B', office_id: 'office-honten', label: 'B', ...baseTpl },
      ],
      patients: [
        {
          id: PATIENT_UUID,
          name: '田中 太郎',
          kana: null,
          status: 'active',
          weekly_pattern: { service_minutes: 60 },
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
    await act(async () => {
      await dndState.capturedHandlers.onDragEnd!({
        active: { id: `pool-patient:${PATIENT_UUID}` },
        over: { id: 'course-day-cell:0:tpl-A:10:00' },
      });
    });
    // ダイアログが表示される
    expect(screen.getByTestId('partner-course-dialog')).toBeInTheDocument();
    // 候補に tpl-B が含まれる (tpl-A は除外)
    const select = screen.getByTestId('partner-course-select') as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toContain('tpl-B');
    expect(options).not.toContain('tpl-A');
    // place-and-fix はまだ呼ばれない
    expect(mockPlaceAndFix).not.toHaveBeenCalled();
  });

  it('P3C-3. ダイアログで相方を確定 → staff_count=2 + course_template_ids 配列で place-and-fix 呼出', async () => {
    mockPlaceAndFix.mockResolvedValue({
      visit: {},
      fixed_visit: null,
      visits: [],
      fixed_visits: [],
      visit_group_id: 'group-uuid',
    });
    setupHooks({
      templates: [
        { id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl },
        { id: 'tpl-B', office_id: 'office-honten', label: 'B', ...baseTpl },
      ],
      patients: [
        {
          id: PATIENT_UUID,
          name: '田中 太郎',
          kana: null,
          status: 'active',
          weekly_pattern: { service_minutes: 90 },
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
    await act(async () => {
      await dndState.capturedHandlers.onDragEnd!({
        active: { id: `pool-patient:${PATIENT_UUID}` },
        over: { id: 'course-day-cell:0:tpl-A:10:00' },
      });
    });
    expect(screen.getByTestId('partner-course-dialog')).toBeInTheDocument();
    // tpl-B を選択
    const select = screen.getByTestId('partner-course-select') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'tpl-B' } });
    // 確定ボタンを押す
    const confirmBtn = screen.getByTestId('partner-course-confirm');
    expect(confirmBtn).not.toBeDisabled();
    await act(async () => {
      fireEvent.click(confirmBtn);
    });
    expect(mockPlaceAndFix).toHaveBeenCalledOnce();
    const arg = mockPlaceAndFix.mock.calls[0][0];
    expect(arg.patient_id).toBe(PATIENT_UUID);
    expect(arg.staff_count).toBe(2);
    expect(arg.course_template_ids).toEqual(['tpl-A', 'tpl-B']);
    // 旧形式は同時送信しない (Zod superRefine でエラーになるため)
    expect(arg.course_template_id).toBeUndefined();
    expect(arg.weekday).toBe(0);
    expect(arg.start_time).toBe('10:00');
    expect(arg.duration_min).toBe(90);
    expect(arg.fix_pattern).toBe(true);
  });

  it('P3C-4. 同 office に他 template が無い → ダイアログにエラー文 + 確定不可', async () => {
    setupHooks({
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      patients: [
        {
          id: PATIENT_UUID,
          name: '田中 太郎',
          kana: null,
          status: 'active',
          weekly_pattern: { service_minutes: 60 },
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
    await act(async () => {
      await dndState.capturedHandlers.onDragEnd!({
        active: { id: `pool-patient:${PATIENT_UUID}` },
        over: { id: 'course-day-cell:0:tpl-A:10:00' },
      });
    });
    expect(screen.getByTestId('partner-course-dialog')).toBeInTheDocument();
    expect(screen.getByTestId('partner-course-no-candidates')).toBeInTheDocument();
    // select は描画されない
    expect(screen.queryByTestId('partner-course-select')).not.toBeInTheDocument();
    // 確定ボタンは disabled
    const confirmBtn = screen.getByTestId('partner-course-confirm');
    expect(confirmBtn).toBeDisabled();
  });

  it('P3C-5. visit_group_id 持ちの visit を D&D 移動しようとする → toast 警告で阻止', async () => {
    setupHooks({
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      courses: [
        {
          id: 'course-1',
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
          id: 'v-pair-1',
          patient_id: PATIENT_UUID,
          patient_name: '田中 太郎',
          visit_date: '2026-05-04',
          start_time: '10:00:00',
          primary_staff_id: null,
          course_id: 'course-1',
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
          id: PATIENT_UUID,
          name: '田中 太郎',
          kana: null,
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
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: 'visit:v-pair-1' },
      over: { id: 'course-day-cell:1:tpl-A:11:00' },
    });
    // toast.warning が呼ばれて止まる
    expect(mockToast.warning).toHaveBeenCalled();
    expect(mockToast.warning.mock.calls[0][0]).toMatch(/2 名体制/);
    // delete も place-and-fix も呼ばれない
    expect(mockDeleteVisit).not.toHaveBeenCalled();
    expect(mockPlaceAndFix).not.toHaveBeenCalled();
  });

  it('P3C-6. visit_group_id なしの通常 visit の移動 → 既存挙動 (delete + place-and-fix)', async () => {
    mockDeleteVisit.mockResolvedValue(undefined);
    mockPlaceAndFix.mockResolvedValue({
      visit: {},
      fixed_visit: null,
      visits: [],
      fixed_visits: [],
      visit_group_id: null,
    });
    setupHooks({
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      courses: [
        {
          id: 'course-1',
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
          id: 'v-1',
          patient_id: PATIENT_UUID,
          patient_name: '田中 太郎',
          visit_date: '2026-05-04',
          start_time: '10:00:00',
          primary_staff_id: null,
          course_id: 'course-1',
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
          id: PATIENT_UUID,
          name: '田中 太郎',
          kana: null,
          status: 'active',
          requires_multiple_staff: false,
          weekly_pattern: { service_minutes: 30 },
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
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: 'visit:v-1' },
      over: { id: 'course-day-cell:1:tpl-A:11:00' },
    });
    expect(mockDeleteVisit).toHaveBeenCalledOnce();
    expect(mockPlaceAndFix).toHaveBeenCalledOnce();
    const placeArg = mockPlaceAndFix.mock.calls[0][0];
    expect(placeArg.staff_count).toBe(1);
    expect(placeArg.course_template_id).toBe('tpl-A');
  });

  it('P3C-7. assignedSlotsByPatient: visit_group_id 持ちペア → slot 0/1 両方埋まり、単独 visit → slot 0 のみ', () => {
    setupHooks({
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
        // P1: ペア visit (group=grp-1, 2 件)
        {
          id: 'v-pair-a',
          patient_id: PATIENT_UUID,
          patient_name: '田中',
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
          id: 'v-pair-b',
          patient_id: PATIENT_UUID,
          patient_name: '田中',
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
        // P2: 単独 visit (group=null)
        {
          id: 'v-solo',
          patient_id: PATIENT_UUID_2,
          patient_name: '佐藤',
          visit_date: '2026-05-04',
          start_time: '11:00:00',
          primary_staff_id: null,
          course_id: 'course-A',
          required_staff_count: 1,
          visit_group_id: null,
          type: 'regular',
          status: 'planned',
          source: 'allocate',
          end_time: '11:30:00',
        },
      ],
      patients: [
        {
          id: PATIENT_UUID,
          name: '田中',
          status: 'active',
          requires_multiple_staff: true,
        },
        {
          id: PATIENT_UUID_2,
          name: '佐藤',
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
    const pane = screen.getByTestId('course-day-pool-pane');
    const serialized = pane.getAttribute('data-assigned-slots') ?? '';
    // 田中 (PATIENT_UUID) → slot 0 と 1
    expect(serialized).toContain(`${PATIENT_UUID}:0`);
    expect(serialized).toContain(`${PATIENT_UUID}:1`);
    // 佐藤 (PATIENT_UUID_2) → slot 0 のみ
    expect(serialized).toContain(`${PATIENT_UUID_2}:0`);
    expect(serialized).not.toContain(`${PATIENT_UUID_2}:1`);
  });

  it('P3C-8. visit_group_id 持ちの occupant に ①/② バッジが描画される', () => {
    setupHooks({
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
          // id 文字列の昇順で 1 番目 → slot 1 (①)
          id: 'aaaa-pair',
          patient_id: PATIENT_UUID,
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
          // id 文字列の昇順で 2 番目 → slot 2 (②)
          id: 'zzzz-pair',
          patient_id: PATIENT_UUID,
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
          id: PATIENT_UUID,
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
    const badge1 = screen.getByTestId('course-occupant-group-badge-aaaa-pair');
    const badge2 = screen.getByTestId('course-occupant-group-badge-zzzz-pair');
    expect(badge1.textContent).toBe('①');
    expect(badge2.textContent).toBe('②');
    // CareFlow #UX-2026W21: 「複数」列はアイコン (👥) 表記 + title/aria-label に ①/② を持つ.
    const multi1 = screen.getByTestId('course-occupant-multi-aaaa-pair');
    expect(multi1.textContent).toBe('👥');
    expect(multi1.getAttribute('data-multi-staff')).toBe('true');
    expect(multi1.getAttribute('title')).toContain('①');
    const multi2 = screen.getByTestId('course-occupant-multi-zzzz-pair');
    expect(multi2.textContent).toBe('👥');
    expect(multi2.getAttribute('data-multi-staff')).toBe('true');
    expect(multi2.getAttribute('title')).toContain('②');
  });

  it('P3C-9. requires_multiple_staff=true で partner 不在 (group_id なし) → 「複数 ① のみ」 + 警告色', () => {
    setupHooks({
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
          patient_id: PATIENT_UUID,
          patient_name: '山田 花子',
          visit_date: '2026-05-04',
          start_time: '10:00:00',
          primary_staff_id: null,
          course_id: 'course-A',
          required_staff_count: 1, // BE が 1 で書いた未完成の片割れ
          visit_group_id: null,
          type: 'regular',
          status: 'planned',
          source: 'allocate',
          end_time: '11:00:00',
        },
      ],
      patients: [
        {
          id: PATIENT_UUID,
          name: '山田 花子',
          status: 'active',
          requires_multiple_staff: true, // 患者マスタは 2 名体制
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
    const multiCell = screen.getByTestId('course-occupant-multi-v-orphan');
    // CareFlow #UX-2026W21: 「複数」列は印 (👥) + data-attribute + title 表記.
    expect(multiCell.textContent).toContain('👥');
    expect(multiCell.getAttribute('data-multi-staff')).toBe('true');
    expect(multiCell.getAttribute('data-partner-missing')).toBe('true');
    expect(multiCell.getAttribute('title')).toContain('複数 ① のみ');
    // 警告色クラス (text-warning) が付与される
    expect(multiCell.className).toContain('text-warning');
  });

  // ─── W37 hotfix M-3: slot 0/1 順は course.code 順で決定論 ───────────
  it('M3. visit_group 内 slot 0/1 は course.code 順 (A→①, B→②) — id 文字列順とは独立', () => {
    setupHooks({
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
        // 意図的に id 順 ≠ course.code 順 のケース.
        // id "aaaa-pair" を course-B (code=B) に, "zzzz-pair" を course-A (code=A) に.
        // id-sort なら aaaa→①, zzzz→② だが、code-sort では zzzz(A)→①, aaaa(B)→② になる.
        {
          id: 'aaaa-pair',
          patient_id: PATIENT_UUID,
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
        {
          id: 'zzzz-pair',
          patient_id: PATIENT_UUID,
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
      ],
      patients: [
        {
          id: PATIENT_UUID,
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
    // course.code 順なので zzzz-pair (course A) → ①, aaaa-pair (course B) → ②
    const badgeZ = screen.getByTestId('course-occupant-group-badge-zzzz-pair');
    expect(badgeZ.textContent).toBe('①');
    const badgeA = screen.getByTestId('course-occupant-group-badge-aaaa-pair');
    expect(badgeA.textContent).toBe('②');
    // 「複数」列も同様 (CareFlow #UX-2026W21: 印 + title で slot 識別).
    const multiZ = screen.getByTestId('course-occupant-multi-zzzz-pair');
    expect(multiZ.textContent).toContain('👥');
    expect(multiZ.getAttribute('title')).toContain('複数訪問 ①');
    const multiA = screen.getByTestId('course-occupant-multi-aaaa-pair');
    expect(multiA.textContent).toContain('👥');
    expect(multiA.getAttribute('title')).toContain('複数訪問 ②');
  });

  // ─── W37 hotfix M-1: visit_group_id 持ち visit を pool drop しても block ───
  it('M1. visit_group_id 持ちの visit を pool ドロップ → toast 警告で阻止 (delete 呼ばれない)', async () => {
    setupHooks({
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      courses: [
        {
          id: 'course-1',
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
          id: 'v-pair-1',
          patient_id: PATIENT_UUID,
          patient_name: '田中 太郎',
          visit_date: '2026-05-04',
          start_time: '10:00:00',
          primary_staff_id: null,
          course_id: 'course-1',
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
          id: PATIENT_UUID,
          name: '田中 太郎',
          kana: null,
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
    // pool drop (over.id === 'pool')
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: 'visit:v-pair-1' },
      over: { id: 'pool' },
    });
    // toast.warning が呼ばれて止まる
    expect(mockToast.warning).toHaveBeenCalled();
    expect(mockToast.warning.mock.calls[0][0]).toMatch(/プール|ペア配置/);
    // delete は呼ばれない (= partner が誤って削除されない)
    expect(mockDeleteVisit).not.toHaveBeenCalled();
  });
});
