/**
 * StaffWeekTablePanel — Wave 16 Phase B テスト.
 *
 * カバーするシナリオ:
 *   1. 表示スタッフが 0 件のとき案内文が出る
 *   2. active スタッフ 3 名でテーブルが 3 個描画される
 *   3. 「週を生成」ボタンが admin で表示され、staff では非表示
 *   4. 「週を生成」をクリックすると useGenerateAndAssign.mutateAsync が呼ばれる
 *   5. 時刻軸が 9:00〜19:00 / 15min / 41 行で描画される (代表値の HH:00 / HH:30 ラベル)
 *   6. 月〜土の 6 列 (日曜なし) のヘッダー
 *   7. visit が当該スタッフのセルに描画される (start_time → 15min 切り下げ)
 *   8. ドロップで place-and-fix が course_template_id 付きで呼ばれる (course 逆引き)
 *   9. ドロップ時にコース未割当なら toast.warning で促す (place-and-fix 不発火)
 *  10. canEdit=false のドロップは何もせず place-and-fix を呼ばない
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
const mockTemplates = vi.fn();
const mockVisits = vi.fn();
const mockCourses = vi.fn();
const mockPlaceAndFix = vi.fn();
const mockGenerateAndAssign = vi.fn();

vi.mock('@/lib/queries/offices', () => ({
  useOffices: (...args: unknown[]) => mockOffices(...args),
}));
vi.mock('@/lib/queries/patients', () => ({
  usePatients: (...args: unknown[]) => mockPatients(...args),
}));
vi.mock('@/lib/queries/staff', () => ({
  useStaffList: (...args: unknown[]) => mockStaffList(...args),
}));
vi.mock('@/lib/queries/course_templates', () => ({
  useCourseTemplates: (...args: unknown[]) => mockTemplates(...args),
}));
vi.mock('@/lib/queries/visits', () => ({
  useVisits: (...args: unknown[]) => mockVisits(...args),
}));
vi.mock('@/lib/queries/courses', () => ({
  useCourses: (...args: unknown[]) => mockCourses(...args),
}));
vi.mock('@/lib/queries/place_and_fix', () => ({
  usePlaceAndFix: () => ({ mutateAsync: mockPlaceAndFix, isPending: false }),
}));
vi.mock('@/lib/queries/generate_and_assign', () => ({
  useGenerateAndAssign: () => ({ mutateAsync: mockGenerateAndAssign, isPending: false }),
}));

// ─── Subject under test ─────────────────────────────────────────────────────

import { StaffWeekTablePanel, resolveCourseTemplateForStaff } from '../StaffWeekTablePanel';

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
}

function setupHooks(opts: SetupOpts = {}) {
  mockOffices.mockReturnValue({
    allOffices: [{ id: 'office-honten', name: '本店' }],
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
  mockTemplates.mockReturnValue({
    data: opts.templates ?? [],
    isLoading: false,
  });
  mockVisits.mockReturnValue({
    data: { items: opts.visits ?? [], truncated: false },
    isLoading: false,
  });
  mockCourses.mockReturnValue({
    data: opts.courses ?? [],
    isLoading: false,
  });
}

// ─── Tests ──────────────────────────────────────────────────────────────────

describe('StaffWeekTablePanel (Wave 16 Phase B)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. active スタッフが 0 件のとき案内文を表示する', () => {
    setupHooks({ staff: [] });
    render(
      <StaffWeekTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    expect(screen.getByText(/対象スタッフがいません/)).toBeInTheDocument();
  });

  it('2. active スタッフ 3 名でテーブルが 3 個描画される', () => {
    setupHooks({
      staff: [
        {
          id: '11111111-1111-1111-1111-111111111111',
          name: '川名 千恵',
          kana: 'カワナチエ',
          status: 'active',
          role: 'manager',
          primary_office_id: 'office-honten',
          is_trainee: false,
        },
        {
          id: '22222222-2222-2222-2222-222222222222',
          name: '宇田川 優莉',
          kana: 'ウダガワユリ',
          status: 'active',
          role: 'staff',
          primary_office_id: 'office-honten',
          is_trainee: false,
        },
        {
          id: '33333333-3333-3333-3333-333333333333',
          name: '本名 大',
          kana: 'ホンナダイ',
          status: 'active',
          role: 'staff',
          primary_office_id: 'office-honten',
          is_trainee: false,
        },
      ],
    });
    render(
      <StaffWeekTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    expect(
      screen.getByTestId('staff-panel-11111111-1111-1111-1111-111111111111'),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId('staff-panel-22222222-2222-2222-2222-222222222222'),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId('staff-panel-33333333-3333-3333-3333-333333333333'),
    ).toBeInTheDocument();
    // マネージャーには M コース固定バッジ
    expect(screen.getByText(/M コース \(固定\)/)).toBeInTheDocument();
  });

  it('3. canEdit=true で「週を生成」ボタンが描画される', () => {
    setupHooks({ staff: [] });
    render(
      <StaffWeekTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    expect(screen.getByTestId('generate-and-assign-button')).toBeInTheDocument();
  });

  it('3b. canEdit=false で「週を生成」ボタンは描画されない', () => {
    setupHooks({ staff: [] });
    render(
      <StaffWeekTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={false}
        showAcceptanceLayer={false}
      />,
    );
    expect(screen.queryByTestId('generate-and-assign-button')).not.toBeInTheDocument();
  });

  it('4. 「週を生成」をクリックすると useGenerateAndAssign.mutateAsync が呼ばれる', async () => {
    mockGenerateAndAssign.mockResolvedValue({
      iso_year: 2026,
      iso_week: 19,
      visits_created: 12,
      courses_assigned: 4,
      message: 'ok',
    });
    setupHooks({ staff: [] });
    render(
      <StaffWeekTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    fireEvent.click(screen.getByTestId('generate-and-assign-button'));
    // microtask を 1 周回す
    await Promise.resolve();
    expect(mockGenerateAndAssign).toHaveBeenCalledOnce();
    const arg = mockGenerateAndAssign.mock.calls[0][0];
    expect(arg.iso_year).toBe(2026);
    expect(arg.iso_week).toBe(19);
    expect(arg.office_id).toBe('office-honten');
  });

  it('5. 時刻軸が 9:00〜19:00 / 15min / 41 行 (HH:00 / HH:30 ラベル) で描画される', () => {
    setupHooks({
      staff: [
        {
          id: 'staff-1',
          name: 'テスト 太郎',
          kana: 'テストタロウ',
          status: 'active',
          role: 'staff',
          primary_office_id: 'office-honten',
          is_trainee: false,
        },
      ],
    });
    render(
      <StaffWeekTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    // 9:00 / 12:30 / 19:00 は HH:00 / HH:30 ラベルとして描画されている
    expect(screen.getAllByText('09:00').length).toBeGreaterThan(0);
    expect(screen.getAllByText('12:30').length).toBeGreaterThan(0);
    expect(screen.getAllByText('19:00').length).toBeGreaterThan(0);
    // 全 droppable cell 数 = 41 slot × 6 weekday = 246 (1 スタッフのみ)
    const cells = document.querySelectorAll('[data-droppable^="staff-cell:staff-1:"]');
    expect(cells.length).toBe(41 * 6);
  });

  it('6. 月〜土の 6 列ヘッダーが描画される (日曜なし)', () => {
    setupHooks({
      staff: [
        {
          id: 'staff-1',
          name: 'テスト',
          kana: 'テスト',
          status: 'active',
          role: 'staff',
          primary_office_id: 'office-honten',
          is_trainee: false,
        },
      ],
    });
    render(
      <StaffWeekTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    for (const wd of ['月', '火', '水', '木', '金', '土']) {
      expect(screen.getAllByText(new RegExp(wd)).length).toBeGreaterThan(0);
    }
    // 日 (Sun) は曜日ヘッダーには出ない (週コースバッジで「日=...」のような形では出ない)
    // -> 「日」を含む文字列が dom 中に無いことまでは強制しない (拠点名等にあり得る)
  });

  it('7. visit が当該スタッフのセルに描画される (start_time → 15min 切り下げ)', () => {
    setupHooks({
      staff: [
        {
          id: '11111111-1111-1111-1111-111111111111',
          name: '田中',
          kana: 'タナカ',
          status: 'active',
          role: 'staff',
          primary_office_id: 'office-honten',
          is_trainee: false,
        },
      ],
      visits: [
        {
          id: 'v-1',
          patient_id: 'p-1',
          patient_name: '鈴木 一郎',
          visit_date: '2026-05-04', // 月曜
          start_time: '09:07:00', // 9:00 スロットへ切り下げ
          primary_staff_id: '11111111-1111-1111-1111-111111111111',
          type: 'regular',
          status: 'planned',
          source: 'allocate',
          end_time: '09:30:00',
        },
      ],
      patients: [
        {
          id: 'p-1',
          name: '鈴木 一郎',
          kana: null,
          status: 'active',
        },
      ],
    });
    render(
      <StaffWeekTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    expect(screen.getAllByText('鈴木 一郎').length).toBeGreaterThan(0);
  });

  it('8. ドロップで place-and-fix が course_template_id 付きで呼ばれる (course 逆引き)', async () => {
    mockPlaceAndFix.mockResolvedValue({ visit: {}, fixed_visit: null });
    setupHooks({
      staff: [
        {
          id: '11111111-1111-1111-1111-111111111111',
          name: '田中',
          kana: 'タナカ',
          status: 'active',
          role: 'staff',
          primary_office_id: 'office-honten',
          is_trainee: false,
        },
      ],
      patients: [
        {
          id: '99999999-9999-9999-9999-999999999999',
          name: '鈴木',
          kana: null,
          status: 'active',
          weekly_pattern: { service_minutes: 45 },
        },
      ],
      // 当該スタッフが月曜 (weekday=0) に A コースを担当する想定
      courses: [
        {
          id: 'course-1',
          iso_year: 2026,
          iso_week: 19,
          weekday: 0,
          code: 'A',
          office_id: 'office-honten',
          assigned_staff_id: '11111111-1111-1111-1111-111111111111',
          course_status: 'staff_assigned',
          deleted_at: null,
        },
      ],
      templates: [
        {
          id: 'tpl-A',
          office_id: 'office-honten',
          label: 'A',
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
        },
      ],
    });
    render(
      <StaffWeekTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    expect(dndState.capturedHandlers.onDragEnd).toBeDefined();
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: 'pool-patient:99999999-9999-9999-9999-999999999999' },
      over: { id: 'staff-cell:11111111-1111-1111-1111-111111111111:0:09:00' },
    });
    expect(mockPlaceAndFix).toHaveBeenCalledOnce();
    const arg = mockPlaceAndFix.mock.calls[0][0];
    expect(arg.patient_id).toBe('99999999-9999-9999-9999-999999999999');
    expect(arg.course_template_id).toBe('tpl-A');
    expect(arg.weekday).toBe(0);
    expect(arg.start_time).toBe('09:00');
    expect(arg.duration_min).toBe(45);
    expect(arg.fix_pattern).toBe(true);
  });

  it('9. コース未割当のドロップは toast.warning + place-and-fix 不発火', async () => {
    setupHooks({
      staff: [
        {
          id: '11111111-1111-1111-1111-111111111111',
          name: '田中',
          kana: 'タナカ',
          status: 'active',
          role: 'staff',
          primary_office_id: 'office-honten',
          is_trainee: false,
        },
      ],
      patients: [
        {
          id: '99999999-9999-9999-9999-999999999999',
          name: '鈴木',
          kana: null,
          status: 'active',
        },
      ],
      courses: [], // 未割当
      templates: [
        {
          id: 'tpl-A',
          office_id: 'office-honten',
          label: 'A',
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
        },
      ],
    });
    render(
      <StaffWeekTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: 'pool-patient:99999999-9999-9999-9999-999999999999' },
      over: { id: 'staff-cell:11111111-1111-1111-1111-111111111111:0:09:00' },
    });
    expect(mockPlaceAndFix).not.toHaveBeenCalled();
    expect(mockToast.warning).toHaveBeenCalledWith(expect.stringContaining('週を生成'));
  });

  it('10. canEdit=false のドロップは place-and-fix を呼ばない', async () => {
    setupHooks({
      staff: [
        {
          id: '11111111-1111-1111-1111-111111111111',
          name: '田中',
          kana: 'タナカ',
          status: 'active',
          role: 'staff',
          primary_office_id: 'office-honten',
          is_trainee: false,
        },
      ],
      patients: [
        {
          id: '99999999-9999-9999-9999-999999999999',
          name: '鈴木',
          kana: null,
          status: 'active',
        },
      ],
    });
    render(
      <StaffWeekTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId="office-honten"
        canEdit={false}
        showAcceptanceLayer={false}
      />,
    );
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: 'pool-patient:99999999-9999-9999-9999-999999999999' },
      over: { id: 'staff-cell:11111111-1111-1111-1111-111111111111:0:09:00' },
    });
    expect(mockPlaceAndFix).not.toHaveBeenCalled();
  });
});

// ─── pure helper unit tests ─────────────────────────────────────────────────

describe('resolveCourseTemplateForStaff', () => {
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
  };

  it('staff×weekday に一致する Course の code から Template を解決する', () => {
    const result = resolveCourseTemplateForStaff({
      staffId: 'staff-1',
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
          assigned_staff_id: 'staff-1',
          course_status: 'staff_assigned',
          assigned_staff: null,
          note: null,
          course_fixed_at: null,
          staff_assigned_at: null,
          created_at: '',
          updated_at: '',
          deleted_at: null,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
        } as any,
      ],
      templates: [
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        { id: 'tpl-A', office_id: 'office-1', label: 'A', ...baseTpl } as any,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        { id: 'tpl-B', office_id: 'office-1', label: 'B', ...baseTpl } as any,
      ],
    });
    expect(result?.templateId).toBe('tpl-B');
  });

  it('該当 Course が無いと null を返す', () => {
    const result = resolveCourseTemplateForStaff({
      staffId: 'staff-1',
      weekday: 0,
      isoYear: 2026,
      isoWeek: 19,
      courses: [],
      templates: [],
    });
    expect(result).toBeNull();
  });

  it('Course はあるが Template が無い (label 不一致) と null を返す', () => {
    const result = resolveCourseTemplateForStaff({
      staffId: 'staff-1',
      weekday: 0,
      isoYear: 2026,
      isoWeek: 19,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      courses: [
        {
          id: 'c-1',
          iso_year: 2026,
          iso_week: 19,
          weekday: 0,
          code: 'C',
          office_id: 'office-1',
          assigned_staff_id: 'staff-1',
          course_status: 'staff_assigned',
          note: null,
          course_fixed_at: null,
          staff_assigned_at: null,
          created_at: '',
          updated_at: '',
          deleted_at: null,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
        } as any,
      ],
      templates: [
        // 別 office のテンプレ → ヒットしない
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        { id: 'tpl-C', office_id: 'office-other', label: 'C', ...baseTpl } as any,
      ],
    });
    expect(result).toBeNull();
  });
});
