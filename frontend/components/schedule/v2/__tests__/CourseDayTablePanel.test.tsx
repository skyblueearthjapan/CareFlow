/**
 * CourseDayTablePanel — Wave 17 Phase B テスト.
 *
 * カバーするシナリオ:
 *   1. 表示対象コースが 0 件のとき案内文が出る
 *   2. active コーステンプレート 2 個でテーブルが 2 個描画される
 *   3. canEdit=true で「週を生成」「自動割付」ボタンが両方描画される
 *   4. canEdit=false で両ボタンが非表示
 *   5. 「週を生成」をクリックすると useGenerateWeek.mutateAsync が呼ばれる
 *   6. 「自動割付」をクリックすると useAssignStaffOnly.mutateAsync が呼ばれる
 *   7. 時刻軸が 9:30〜18:00 / 15min / 35 行で描画される
 *   8. 月〜土の 6 つの曜日タブが描画される (日曜なし)
 *   9. visit が当該コース・スロットに描画される (start_time → 15min 切り下げ)
 *  10. ドロップで place-and-fix が course_template_id 付きで呼ばれる
 *  11. 担当 dropdown を変更すると useUpdateCourse が assigned_staff_id 付きで呼ばれる
 *  12. findCourseForTemplate の純関数テスト (label → code 解決)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// ─── モック ──────────────────────────────────────────────────────────────────

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
  User: () => <span />,
  Users: () => <span />,
  X: () => <span />,
  Plus: () => <span />,
  ChevronDown: () => <span />,
  Star: () => <span />,
  Loader2: () => <span data-testid="loader" />,
  RefreshCw: () => <span data-testid="refresh-icon" />,
  UserCheck: () => <span data-testid="user-check-icon" />,
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
}));
vi.mock('@/lib/queries/assign_staff_only', () => ({
  useAssignStaffOnly: () => ({ mutateAsync: mockAssignStaffOnly, isPending: false }),
}));

// ─── Subject under test ─────────────────────────────────────────────────────

import { CourseDayTablePanel, findCourseForTemplate } from '../CourseDayTablePanel';

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

// ─── Tests ──────────────────────────────────────────────────────────────────

describe('CourseDayTablePanel (Wave 17 Phase B)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. 表示対象コースが 0 件のとき案内文を表示する', () => {
    setupHooks({ templates: [] });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    expect(screen.getByText(/表示対象コースがありません/)).toBeInTheDocument();
  });

  it('2. active コーステンプレート 2 個でテーブルが 2 個描画される', () => {
    setupHooks({
      templates: [
        { id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl },
        { id: 'tpl-B', office_id: 'office-honten', label: 'B', ...baseTpl },
      ],
    });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    // activeWeekday=0 (月)
    expect(screen.getByTestId('course-day-table-0-tpl-A')).toBeInTheDocument();
    expect(screen.getByTestId('course-day-table-0-tpl-B')).toBeInTheDocument();
  });

  it('3. canEdit=true で「週を生成」「自動割付」ボタンが描画される', () => {
    setupHooks({ templates: [] });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    expect(screen.getByTestId('generate-week-button')).toBeInTheDocument();
    expect(screen.getByTestId('assign-staff-only-button')).toBeInTheDocument();
  });

  it('4. canEdit=false で両ボタンが非表示', () => {
    setupHooks({ templates: [] });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={false}
        showAcceptanceLayer={false}
      />,
    );
    expect(screen.queryByTestId('generate-week-button')).not.toBeInTheDocument();
    expect(screen.queryByTestId('assign-staff-only-button')).not.toBeInTheDocument();
  });

  it('5. 「週を生成」をクリックすると useGenerateWeek.mutateAsync が呼ばれる', async () => {
    mockGenerateWeek.mockResolvedValue({
      iso_year: 2026,
      iso_week: 19,
      visits_created: 12,
      message: 'ok',
    });
    setupHooks({ templates: [] });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    fireEvent.click(screen.getByTestId('generate-week-button'));
    await Promise.resolve();
    expect(mockGenerateWeek).toHaveBeenCalledOnce();
    const arg = mockGenerateWeek.mock.calls[0][0];
    expect(arg.iso_year).toBe(2026);
    expect(arg.iso_week).toBe(19);
    expect(arg.office_id).toBe('office-honten');
  });

  it('6. 「自動割付」をクリックすると useAssignStaffOnly.mutateAsync が呼ばれる', async () => {
    mockAssignStaffOnly.mockResolvedValue({
      iso_year: 2026,
      iso_week: 19,
      courses_assigned: 4,
      message: 'ok',
    });
    setupHooks({ templates: [] });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    fireEvent.click(screen.getByTestId('assign-staff-only-button'));
    await Promise.resolve();
    expect(mockAssignStaffOnly).toHaveBeenCalledOnce();
    const arg = mockAssignStaffOnly.mock.calls[0][0];
    expect(arg.iso_year).toBe(2026);
    expect(arg.iso_week).toBe(19);
  });

  it('7. 時刻軸が 9:30〜18:00 / 15min / 35 行で描画される', () => {
    setupHooks({
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
    });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    // 代表値: 09:30 / 12:00 / 18:00
    expect(screen.getAllByText('09:30').length).toBeGreaterThan(0);
    expect(screen.getAllByText('12:00').length).toBeGreaterThan(0);
    expect(screen.getAllByText('18:00').length).toBeGreaterThan(0);
    // 9:00 や 19:00 は時刻スロットラベルとしては存在しない (範囲外)
    // 全 droppable cell 数 = 35 slot (1 テンプレ × 1 曜日)
    const cells = document.querySelectorAll('[data-droppable^="course-day-cell:0:tpl-A:"]');
    expect(cells.length).toBe(35);
  });

  it('8. 月〜土の 6 つの曜日タブが描画される (日曜なし)', () => {
    setupHooks({ templates: [] });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    expect(screen.getByTestId('course-day-tab-0')).toBeInTheDocument();
    expect(screen.getByTestId('course-day-tab-1')).toBeInTheDocument();
    expect(screen.getByTestId('course-day-tab-2')).toBeInTheDocument();
    expect(screen.getByTestId('course-day-tab-3')).toBeInTheDocument();
    expect(screen.getByTestId('course-day-tab-4')).toBeInTheDocument();
    expect(screen.getByTestId('course-day-tab-5')).toBeInTheDocument();
    expect(screen.queryByTestId('course-day-tab-6')).not.toBeInTheDocument();
  });

  it('9. visit が当該コース・スロットに描画される (start_time → 15min 切り下げ)', () => {
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
          patient_id: 'p-1',
          patient_name: '鈴木 一郎',
          visit_date: '2026-05-04',
          start_time: '09:37:00', // 09:30 にスロット切り下げ
          primary_staff_id: null,
          course_id: 'course-1',
          type: 'regular',
          status: 'planned',
          source: 'allocate',
          end_time: '10:30:00',
        },
      ],
      patients: [
        {
          id: 'p-1',
          name: '鈴木 一郎',
          kana: null,
          status: 'active',
          address: '千葉県千葉市',
        },
      ],
    });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    expect(screen.getAllByText('鈴木 一郎').length).toBeGreaterThan(0);
    expect(screen.getAllByText('千葉県千葉市').length).toBeGreaterThan(0);
  });

  it('10. ドロップで place-and-fix が course_template_id 付きで呼ばれる', async () => {
    mockPlaceAndFix.mockResolvedValue({ visit: {}, fixed_visit: null });
    setupHooks({
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      patients: [
        {
          id: '99999999-9999-9999-9999-999999999999',
          name: '鈴木',
          kana: null,
          status: 'active',
          weekly_pattern: { service_minutes: 45 },
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
    expect(dndState.capturedHandlers.onDragEnd).toBeDefined();
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: 'pool-patient:99999999-9999-9999-9999-999999999999' },
      over: { id: 'course-day-cell:0:tpl-A:09:30' },
    });
    expect(mockPlaceAndFix).toHaveBeenCalledOnce();
    const arg = mockPlaceAndFix.mock.calls[0][0];
    expect(arg.patient_id).toBe('99999999-9999-9999-9999-999999999999');
    expect(arg.course_template_id).toBe('tpl-A');
    expect(arg.weekday).toBe(0);
    expect(arg.start_time).toBe('09:30');
    expect(arg.duration_min).toBe(45);
    expect(arg.fix_pattern).toBe(true);
  });

  it('11. 担当 dropdown を変更すると useUpdateCourse が assigned_staff_id 付きで呼ばれる', async () => {
    mockUpdateCourse.mockResolvedValue({});
    setupHooks({
      offices: [{ id: 'office-honten', name: '本店' }],
      staff: [
        {
          id: 'staff-1',
          name: '田中 一郎',
          kana: 'タナカイチロウ',
          status: 'active',
          role: 'staff',
          primary_office_id: 'office-honten',
          is_trainee: false,
        },
      ],
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
    });
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    const select = screen.getByTestId('course-staff-select-0-tpl-A') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'staff-1' } });
    await Promise.resolve();
    expect(mockUpdateCourse).toHaveBeenCalledOnce();
    const arg = mockUpdateCourse.mock.calls[0][0];
    expect(arg.id).toBe('course-1');
    expect(arg.patch.assigned_staff_id).toBe('staff-1');
  });
});

// ─── pure helper unit tests ─────────────────────────────────────────────────

describe('findCourseForTemplate', () => {
  it('label の頭文字 (大文字) と Course.code が一致する行を返す', () => {
    const result = findCourseForTemplate({
      template: {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        id: 'tpl-B',
        office_id: 'office-1',
        label: 'B',
        ...baseTpl,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any,
      weekday: 2,
      isoYear: 2026,
      isoWeek: 19,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      courses: [
        {
          id: 'c-1',
          iso_year: 2026,
          iso_week: 19,
          weekday: 2,
          code: 'B',
          office_id: 'office-1',
          assigned_staff_id: null,
          course_status: 'course_fixed',
          note: null,
          course_fixed_at: null,
          staff_assigned_at: null,
          created_at: '',
          updated_at: '',
          deleted_at: null,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
        } as any,
      ],
    });
    expect(result?.id).toBe('c-1');
  });

  it('該当 Course が無いと null を返す', () => {
    const result = findCourseForTemplate({
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      template: {
        id: 'tpl-A',
        office_id: 'office-1',
        label: 'A',
        ...baseTpl,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any,
      weekday: 0,
      isoYear: 2026,
      isoWeek: 19,
      courses: [],
    });
    expect(result).toBeNull();
  });

  it('別 office の course は無視', () => {
    const result = findCourseForTemplate({
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      template: {
        id: 'tpl-A',
        office_id: 'office-1',
        label: 'A',
        ...baseTpl,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any,
      weekday: 0,
      isoYear: 2026,
      isoWeek: 19,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      courses: [
        {
          id: 'c-other',
          iso_year: 2026,
          iso_week: 19,
          weekday: 0,
          code: 'A',
          office_id: 'office-other',
          assigned_staff_id: null,
          course_status: 'course_fixed',
          deleted_at: null,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
        } as any,
      ],
    });
    expect(result).toBeNull();
  });
});
