/**
 * CourseDayTablePanel — Wave 37 Phase 3-C テスト.
 *
 * Phase 2 (日テーブル撤去): プールカードのドロップ先は `course-day-cell:` から
 *   タイムライン列 `tl-col:{templateId}:{weekday}` + Y オフセットへ移行した
 *   (`dropPatientOnColumn` ヘルパー参照). 配置フロー本体は不変。
 *   テーブル固有だった P3C-5 / P3C-6 / P3C-8 / P3C-9 / M1 / M3 は削除 (末尾の
 *   コメントに根拠を記載)。
 *
 * カバーするシナリオ:
 *  P3C-1. 通常患者 (requires_multiple_staff=false) の D&D → staff_count=1 +
 *         course_template_id (旧形式単数) で place-and-fix 呼出 (regression)
 *  P3C-2. 複数対応患者 (requires_multiple_staff=true) の D&D →
 *         PartnerCourseDialog が表示される (place-and-fix はまだ呼ばない)
 *  P3C-3. ダイアログで相方を確定 → staff_count=2 + course_template_ids: [a, b]
 *         配列形式で place-and-fix 呼出
 *  P3C-4. 同 office に他 template が無い → ダイアログにエラー文が出て確定不可
 *  P3C-7. assignedSlotsByPatient マップ (data 属性 シリアライズ) が正しく構築:
 *         - visit_group_id 持ち (2 件) → slot 0/1 両方埋まり
 *         - 単独 visit → slot 0 のみ
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ApiError } from '@/lib/api-client';
import { timeToY } from '@/lib/scheduling/timeline';

// ─── モック (CourseDayTablePanel.test.tsx と同じ構成) ────────────────────────

const { specialState } = vi.hoisted(() => ({
  specialState: {
    /** GET /special-visit-marks/pool の戻り (⭐ チケット). */
    tickets: [] as unknown[],
    /** POST /special-visit-marks/{id}/place. */
    place: vi.fn(),
  },
}));

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
// Phase G-21 T4: useTogglePfvPin は内部で useMutation を呼ぶため必須.
vi.mock('@/lib/queries/g21', () => ({
  useTogglePfvPin: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  // Phase G-47: PinScopeMenu の「全曜日」スコープ用 bulk hook (panel 内で使用).
  useBulkPinPfvs: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
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

// ⭐特別訪問週間 (`special-ticket-dnd-design-2026-09-08.md`): プールのチケット取得と
// place だけ差し替える (他のフックは実物のままで良い = プール側セクションが使う)。
vi.mock('@/lib/queries/specialVisitWeek', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type Mod = typeof import('@/lib/queries/specialVisitWeek');
  const actual = await importOriginal<Mod>();
  return {
    ...actual,
    useSpecialVisitPool: () => ({
      data: specialState.tickets,
      isLoading: false,
      isError: false,
    }),
    usePlaceSpecialMark: () => ({ mutateAsync: specialState.place, isPending: false }),
  };
});

vi.mock('@/lib/queries/visitMoveWeekOnly', () => ({
  useVisitMoveWeekOnly: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), back: vi.fn() }),
  usePathname: () => '/schedule',
  useSearchParams: () => new URLSearchParams(),
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

/** 全テスト共通: QueryClientProvider 配下で panel を描画する. */
function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CourseDayTablePanel weekStart={monday(2026, 5, 4)} officeId="office-honten" canEdit={true} />
    </QueryClientProvider>,
  );
}

/**
 * プールカード → タイムライン列 (`tl-col:{templateId}:{weekday}`) のドロップ引数を作る.
 * Phase 2 で日テーブル (`course-day-cell:` droppable) を撤去したため、ドロップ先の
 * 時刻は「列 rect 上端からの Y オフセット」で表現する (snapYOffsetToMinutes の逆算)。
 */
function dropPatientOnColumn(patientId: string, templateId: string, weekday: number, hm: string) {
  const overTop = 10;
  return {
    active: {
      id: `pool-patient:${patientId}`,
      rect: { current: { translated: { top: overTop + (timeToY(hm) ?? 0) } } },
    },
    over: { id: `tl-col:${templateId}:${weekday}`, rect: { top: overTop } },
  };
}

describe('CourseDayTablePanel — W37 Phase 3-C', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // ⭐ チケットは既定で 0 件 (= 既存テストに影響しない)。
    specialState.tickets = [];
    specialState.place.mockReset();
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
    renderPanel();
    await dndState.capturedHandlers.onDragEnd!({
      ...dropPatientOnColumn(PATIENT_UUID, 'tpl-A', 0, '09:30'),
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
    renderPanel();
    await act(async () => {
      await dndState.capturedHandlers.onDragEnd!({
        ...dropPatientOnColumn(PATIENT_UUID, 'tpl-A', 0, '10:00'),
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
    renderPanel();
    await act(async () => {
      await dndState.capturedHandlers.onDragEnd!({
        ...dropPatientOnColumn(PATIENT_UUID, 'tpl-A', 0, '10:00'),
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
    // Wave U-2 D-2 既定B: 「この週だけ配置」なので型 (固定枠) は作らない。
    // 昇格は toast の導線 or 週→型同期 (bulk-sync) で行う。
    expect(arg.fix_pattern).toBe(false);
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
    renderPanel();
    await act(async () => {
      await dndState.capturedHandlers.onDragEnd!({
        ...dropPatientOnColumn(PATIENT_UUID, 'tpl-A', 0, '10:00'),
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

  // Phase 2 (日テーブル撤去) で削除したテスト:
  //   P3C-5 / P3C-6 : `visit:` → `course-day-cell:` のテーブル間移動 (D&D 移動 /
  //     visit_group_id ガード)。production から当該 id 名前空間ごと消滅した。
  //     ペア visit のプール戻しガードは CourseDayTablePanel.test.tsx の G4-1/G4-2
  //     (tl-visit / tl-pair → プール) が担保する。

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
    renderPanel();
    const pane = screen.getByTestId('course-day-pool-pane');
    const serialized = pane.getAttribute('data-assigned-slots') ?? '';
    // 田中 (PATIENT_UUID) → slot 0 と 1
    expect(serialized).toContain(`${PATIENT_UUID}:0`);
    expect(serialized).toContain(`${PATIENT_UUID}:1`);
    // 佐藤 (PATIENT_UUID_2) → slot 0 のみ
    expect(serialized).toContain(`${PATIENT_UUID_2}:0`);
    expect(serialized).not.toContain(`${PATIENT_UUID_2}:1`);
  });

  // ── 2026-09-07: 列の外で離したときの案内 (プール配置の行き止まり対策) ──────
  // 黙って戻すと「壊れている」と読まれるので、どこで離せばよいかを伝える。

  /** 案内テスト用の最小セットアップ (通常患者 1 名 + template 1 件)。 */
  function setupPlainPatient() {
    setupHooks({
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
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
    renderPanel();
  }

  it('プールカードを over なし (列の外) で離したら案内トーストを出す', async () => {
    setupPlainPatient();
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: `pool-patient:${PATIENT_UUID}`, rect: { current: { translated: null } } },
      over: null,
    });
    expect(mockToast.warning).toHaveBeenCalledWith(
      '列の上で離してください（コースの列にカードを重ねると配置できます）',
    );
    expect(mockPlaceAndFix).not.toHaveBeenCalled();
  });

  it('プールカードを列以外の droppable で離しても案内トーストを出す', async () => {
    setupPlainPatient();
    await dndState.capturedHandlers.onDragEnd!({
      active: {
        id: `pool-patient:${PATIENT_UUID}`,
        rect: { current: { translated: { top: 100 } } },
      },
      over: { id: 'not-a-timeline-column', rect: { top: 10 } },
    });
    expect(mockToast.warning).toHaveBeenCalledWith(
      '列の上で離してください（コースの列にカードを重ねると配置できます）',
    );
    expect(mockPlaceAndFix).not.toHaveBeenCalled();
  });

  it('プールカードをプール自身へ戻したときは案内を出さない (正規の操作)', async () => {
    setupPlainPatient();
    await dndState.capturedHandlers.onDragEnd!({
      active: {
        id: `pool-patient:${PATIENT_UUID}`,
        rect: { current: { translated: { top: 100 } } },
      },
      over: { id: 'pool', rect: { top: 10 } },
    });
    expect(mockToast.warning).not.toHaveBeenCalled();
  });

  // ── 2026-09-08: ⭐特別訪問週間チケットの DnD ────────────────────────────
  // 設計 `docs/plans/special-ticket-dnd-design-2026-09-08.md` §4。
  // 盤面の曜日タブ既定は 'week' → activeWeekday=0 (月) なので、weekday=0 の
  // チケットが「表示中の曜日と同じ = 掴める」ケースになる。

  const MARK_ID = '55555555-5555-4555-8555-555555555555';

  /** ⭐ チケット 1 枚 (プール API の戻り相当). */
  function makeTicket(over: { weekday?: number; requiresMulti?: boolean } = {}) {
    return {
      mark: {
        id: MARK_ID,
        period_id: 'period-1',
        patient_id: PATIENT_UUID,
        iso_year: 2026,
        iso_week: 19,
        weekday: over.weekday ?? 0,
        kind: 'extra',
        status: 'pool',
        placed_visit_id: null,
        placed_summary: null,
      },
      patient: {
        id: PATIENT_UUID,
        name: '中尾 要太',
        code: 'P-001',
        sex: 'male',
        sex_restriction: null,
        requires_multiple_staff: over.requiresMulti ?? false,
        lat: null,
        lng: null,
        primary_office_id: 'office-honten',
      },
      period: { id: 'period-1', weekly_target: 5, end_date: '2026-05-30' },
      last_placement: null,
      service_minutes: 45,
    };
  }

  /** ⭐ チケット → タイムライン列のドロップ引数 (`special-ticket:{markId}`). */
  function dropTicketOnColumn(templateId: string, weekday: number, hm: string) {
    const overTop = 10;
    return {
      active: {
        id: `special-ticket:${MARK_ID}`,
        rect: { current: { translated: { top: overTop + (timeToY(hm) ?? 0) } } },
      },
      over: { id: `tl-col:${templateId}:${weekday}`, rect: { top: overTop } },
    };
  }

  /** ⭐ テスト用の最小セットアップ (template 1 件 + チケット 1 枚). */
  function setupTicket(over: { weekday?: number; requiresMulti?: boolean } = {}) {
    specialState.tickets = [makeTicket(over)];
    setupHooks({
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      patients: [],
    });
    renderPanel();
  }

  it('ST-1. ⭐ チケットを列にドロップ → place を {course_template_id, start_time} で 1 回だけ呼ぶ', async () => {
    specialState.place.mockResolvedValue({ mark: {}, visit_id: 'v-1' });
    setupTicket();
    await act(async () => {
      await dndState.capturedHandlers.onDragEnd!(dropTicketOnColumn('tpl-A', 0, '10:15'));
    });
    expect(specialState.place).toHaveBeenCalledOnce();
    expect(specialState.place.mock.calls[0][0]).toEqual({
      markId: MARK_ID,
      payload: { course_template_id: 'tpl-A', start_time: '10:15' },
    });
    expect(mockToast.success).toHaveBeenCalledWith(
      '中尾 要太 様を 月曜 10:15 に配置しました（この週のみ・固定化しません）',
    );
  });

  it('ST-2. 9:00〜18:00 の外にドロップしたら警告のみ (place を呼ばない)', async () => {
    setupTicket();
    await act(async () => {
      // 所要 45 分なので 17:30 開始は 18:15 終わり = 範囲外。
      await dndState.capturedHandlers.onDragEnd!(dropTicketOnColumn('tpl-A', 0, '17:30'));
    });
    expect(specialState.place).not.toHaveBeenCalled();
    expect(mockToast.warning).toHaveBeenCalledWith(
      'この位置には置けません（9:00〜18:00 の範囲に収まるように配置してください）',
    );
  });

  it('ST-3. 表示中の曜日タブと違う曜日のチケットは警告のみ (曜日の罠ガード)', async () => {
    setupTicket({ weekday: 3 }); // 木曜のチケットを月曜タブで離す
    await act(async () => {
      await dndState.capturedHandlers.onDragEnd!(dropTicketOnColumn('tpl-A', 0, '10:15'));
    });
    expect(specialState.place).not.toHaveBeenCalled();
    expect(mockToast.warning).toHaveBeenCalledWith('木曜のカードは 木曜タブでのみ配置できます');
  });

  it('ST-4. 2 名体制の患者はドラッグ配置を塞ぎ、クリック導線へ誘導する', async () => {
    setupTicket({ requiresMulti: true });
    await act(async () => {
      await dndState.capturedHandlers.onDragEnd!(dropTicketOnColumn('tpl-A', 0, '10:15'));
    });
    expect(specialState.place).not.toHaveBeenCalled();
    expect(mockToast.warning).toHaveBeenCalledWith(
      '2名体制の患者はドラッグでは配置できません。カードをクリックして「配置先を決める」から入れてください',
    );
  });

  it('ST-4b. ⭐ チケットをプール自身へ戻したときは何も起きない (noop)', async () => {
    setupTicket();
    await act(async () => {
      await dndState.capturedHandlers.onDragEnd!({
        active: {
          id: `special-ticket:${MARK_ID}`,
          rect: { current: { translated: { top: 100 } } },
        },
        over: { id: 'pool', rect: { top: 10 } },
      });
    });
    expect(specialState.place).not.toHaveBeenCalled();
    expect(mockToast.warning).not.toHaveBeenCalled();
    expect(mockToast.error).not.toHaveBeenCalled();
  });

  it('ST-4c. ⭐ チケットを over なし (列の外) で離したら案内トーストを出す', async () => {
    setupTicket();
    await act(async () => {
      await dndState.capturedHandlers.onDragEnd!({
        active: { id: `special-ticket:${MARK_ID}`, rect: { current: { translated: null } } },
        over: null,
      });
    });
    expect(specialState.place).not.toHaveBeenCalled();
    expect(mockToast.warning).toHaveBeenCalledWith(
      '列の上で離してください（コースの列にカードを重ねると配置できます）',
    );
  });

  it('ST-5. 422 constraint_confirmation_required → 確認ダイアログ → acknowledge 付きで再送', async () => {
    specialState.place
      .mockRejectedValueOnce(
        new ApiError('Unprocessable Entity', 422, {
          detail: {
            code: 'constraint_confirmation_required',
            warnings: [
              {
                kind: 'ng_staff',
                patient_id: PATIENT_UUID,
                patient_name: '中尾 要太',
                staff_id: '77777777-7777-4777-8777-777777777777',
                staff_name: '熊澤 妙子',
                note: null,
              },
            ],
          },
        }),
      )
      .mockResolvedValueOnce({ mark: {}, visit_id: 'v-1' });
    setupTicket();
    await act(async () => {
      await dndState.capturedHandlers.onDragEnd!(dropTicketOnColumn('tpl-A', 0, '10:15'));
    });
    const dialog = await screen.findByTestId('constraint-override-confirm');
    expect(dialog).toBeInTheDocument();
    expect(screen.getByTestId('constraint-override-ok')).toHaveTextContent('配置する');
    await act(async () => {
      fireEvent.click(screen.getByTestId('constraint-override-ok'));
    });
    expect(specialState.place).toHaveBeenCalledTimes(2);
    expect(specialState.place.mock.calls[1][0]).toEqual({
      markId: MARK_ID,
      payload: {
        course_template_id: 'tpl-A',
        start_time: '10:15',
        acknowledge_constraint_warnings: true,
      },
    });
  });

  // Phase 2 (日テーブル撤去) で削除したテスト:
  //   P3C-8 / P3C-9 / M3 : `course-occupant-multi-*` セル DOM (①/② バッジ・
  //     「複数 ① のみ」警告色) の描画検証。テーブル固有の表示で、タイムライン /
  //     日リストには対応表示が無いため移行先が存在しない。
  //   M1 : `visit:` id のプールドロップガード。G4-1/G4-2 (tl-visit/tl-pair) が担保。
});
