/**
 * CourseDayTablePanel — Wave 17 Phase B テスト.
 *
 * カバーするシナリオ:
 *   1. 表示対象コースが 0 件のとき案内文が出る
 *   2. active コーステンプレート 2 個でタイムライン列が 2 本描画される
 *   3-6. (Phase G-40 で削除) 主要 4 ボタンは page.tsx (Card 1) へ移設のため panel 側では未描画
 *   7,9,10,12,13,13b,14,14b,14c,15,16,17,18.
 *       (Phase 2 で削除) いずれも旧 CourseDayTable の格子セル描画 / テーブル DnD
 *       (`course-day-cell:` / `visit:` id) を検証していた。テーブル UI 撤去に伴い
 *       production 側に対応経路が無くなったため削除。代替は T-6P1 / G4-1..3 /
 *       timeline/__tests__/TimelineDayBoard.test.tsx。
 *   8. 月〜土の 6 つの曜日タブが描画される (日曜なし)
 *  11. 担当 dropdown (タイムライン列ヘッダ) 変更で useUpdateCourse が呼ばれる
 *  19. 「週」タブで CourseWeekOverview ペインに切替
 *  +.  findCourseForTemplate の純関数テスト (label → code 解決)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { snapYOffsetToMinutes } from '@/lib/scheduling/timeline';

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

// CreatePatientDialog が useRouter を呼ぶ (App Router 未マウントの jsdom では例外)。
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), back: vi.fn() }),
  usePathname: () => '/schedule',
  useSearchParams: () => new URLSearchParams(),
}));

// アイコンは追加のたびにモック不足で落ちるため、testid が必要なものだけ明示し
// 残りは Proxy フォールバックで自動的に空 span を返す。
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

// スタッフ数連動 (auto-schedule 統一) の hook. 既存テストは A-E コースが
// 開講している前提なので staffCountFor は十分な人数 (=5) を返す.
vi.mock('@/lib/queries/weekday_staff_capacity', () => ({
  useWeekdayStaffCapacityLookup: () => ({
    staffCountFor: () => 5,
    managerCountFor: () => 0,
    courseCodesMax: 5,
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
// Phase G-21 T4: useTogglePfvPin は内部で useMutation を呼ぶため、
// QueryClientProvider 無しのテスト環境で例外になる. ボタンを叩かないテストでは
// noop で十分.
vi.mock('@/lib/queries/g21', () => ({
  useTogglePfvPin: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  // Phase G-47: PinScopeMenu の「全曜日」スコープ用 bulk hook (panel 内で使用).
  useBulkPinPfvs: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
}));
// Wave 41 v2: autoScheduleV2 モック (useMutation を直接呼ぶため QueryClient が無いと
// テスト環境で例外になる。ボタンを叩かないテストでは noop で十分).
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
// Wave 39: staff-events モック (W39 で useUpdateEventForDrag が追加されたため必須).
// T-6P2: イベント DnD テスト用に buildStaffEventsMap を差し替え可能にする (既定=空 Map)。
const mockUpdateEventDrag = vi.fn();
const mockBuildStaffEventsMap = vi.fn(() => new Map());
vi.mock('@/lib/queries/staff-events', () => ({
  useWeekStaffEvents: () => ({ data: [], isLoading: false }),
  buildStaffEventsMap: (...args: unknown[]) => mockBuildStaffEventsMap(...args),
  useUpdateEventForDrag: () => ({ mutateAsync: mockUpdateEventDrag, isPending: false }),
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

// Wave U-3: op-log 関連フック (QueryClient 不要にするためモック)
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

// T-2 ②-b: タイムライン DnD 移動 (この週だけ) の mutation.
vi.mock('@/lib/queries/visitMoveWeekOnly', () => ({
  useVisitMoveWeekOnly: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

// スケジューリング設定 (useQuery 直呼び). data undefined で既定挙動になる.
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

/** 既定 props + QueryClientProvider で panel を描画する共通ヘルパー. */
function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CourseDayTablePanel weekStart={monday(2026, 5, 4)} officeId={null} canEdit={true} />
    </QueryClientProvider>,
  );
}

describe('CourseDayTablePanel (Wave 17 Phase B)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. 表示対象コースが 0 件のとき案内文を表示する', () => {
    setupHooks({ templates: [] });
    renderPanel();
    // 既定タブは「週」。案内文は曜日タブパネル内にあるので月曜へ切替える。
    fireEvent.click(screen.getByTestId('course-day-tab-0'));
    expect(screen.getByText(/表示対象コースがありません/)).toBeInTheDocument();
  });

  it('2. active コーステンプレート 2 個でタイムライン列が 2 本描画される', () => {
    setupHooks({
      templates: [
        { id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl },
        { id: 'tpl-B', office_id: 'office-honten', label: 'B', ...baseTpl },
      ],
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <CourseDayTablePanel weekStart={monday(2026, 5, 4)} officeId={null} canEdit={true} />
      </QueryClientProvider>,
    );
    // 既定タブは「週」なので月曜 (activeWeekday=0) へ切替。
    fireEvent.click(screen.getByTestId('course-day-tab-0'));
    // 列 key = `${templateId}:${weekday}` (TimelineDayBoard の tl-col-{key})。
    expect(screen.getByTestId('tl-col-tpl-A:0')).toBeInTheDocument();
    expect(screen.getByTestId('tl-col-tpl-B:0')).toBeInTheDocument();
  });

  // Phase G-40: テスト #3〜#6 は削除済.
  // 主要 4 ボタン (週生成 / 自動割付 / 全面最適化 / プール投入) は CourseDayTablePanel から
  // page.tsx (Card 1) へ移設されたため、panel 単体ではボタンを描画しなくなった.
  // これらのボタンのカバレッジは将来 schedule/page.tsx 側の test に移管する.

  // Phase 2 (日テーブル撤去): テスト #7 (35 行の時刻軸 + course-day-cell droppable 35 個)
  //   は CourseDayTable 固有の格子を検証していたため削除. タイムラインの時刻軸は
  //   timeline/__tests__/TimelineDayBoard.test.tsx が担保する.

  it('8. 月〜土の 6 つの曜日タブが描画される (日曜なし)', () => {
    setupHooks({ templates: [] });
    renderPanel();
    expect(screen.getByTestId('course-day-tab-0')).toBeInTheDocument();
    expect(screen.getByTestId('course-day-tab-1')).toBeInTheDocument();
    expect(screen.getByTestId('course-day-tab-2')).toBeInTheDocument();
    expect(screen.getByTestId('course-day-tab-3')).toBeInTheDocument();
    expect(screen.getByTestId('course-day-tab-4')).toBeInTheDocument();
    expect(screen.getByTestId('course-day-tab-5')).toBeInTheDocument();
    expect(screen.queryByTestId('course-day-tab-6')).not.toBeInTheDocument();
  });

  it('19. 「週」タブが描画され、選択すると CourseWeekOverview ペインに切替 (B-6)', () => {
    setupHooks({
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
    });
    renderPanel();
    // 「週」タブが存在
    const weekTab = screen.getByTestId('course-day-tab-week');
    expect(weekTab).toBeInTheDocument();
    // 月曜タブを選ぶと曜日タブパネル (course-day-table-list) が表示される
    fireEvent.click(screen.getByTestId('course-day-tab-0'));
    expect(screen.getByTestId('course-day-table-list')).toBeInTheDocument();
    // 「週」タブをクリック → week-overview-panel が表示
    fireEvent.click(weekTab);
    expect(screen.getByTestId('course-week-overview-panel')).toBeInTheDocument();
    expect(screen.queryByTestId('course-day-table-list')).not.toBeInTheDocument();
  });

  // G1: 担当スタッフ変更 select は列ヘッダ (tl-staff-select-{colKey}) へ移設済。
  it('11. 担当 dropdown (タイムライン列ヘッダ) を変更すると useUpdateCourse が assigned_staff_id 付きで呼ばれる', async () => {
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
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <CourseDayTablePanel
          weekStart={monday(2026, 5, 4)}
          officeId="office-honten"
          canEdit={true}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByTestId('course-day-tab-0'));
    const select = screen.getByTestId('tl-staff-select-tpl-A:0') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'staff-1' } });
    await Promise.resolve();
    expect(mockUpdateCourse).toHaveBeenCalledOnce();
    const arg = mockUpdateCourse.mock.calls[0][0];
    expect(arg.id).toBe('course-1');
    expect(arg.patch.assigned_staff_id).toBe('staff-1');
  });

  // ─── Wave 19: 2 ペイン レイアウト ────────────────────────────────────────

  it('W19-1. 2 ペイン構造でレンダーされる (course-day-two-pane が存在)', () => {
    setupHooks({ templates: [] });
    renderPanel();
    expect(screen.getByTestId('course-day-two-pane')).toBeInTheDocument();
  });

  it('W19-2. プールが右ペイン (course-day-pool-pane) に表示される', () => {
    setupHooks({ templates: [] });
    renderPanel();
    const poolPane = screen.getByTestId('course-day-pool-pane');
    expect(poolPane).toBeInTheDocument();
    // プールコンポーネントが右ペイン内に含まれる
    expect(poolPane.querySelector('[data-testid="pool-grouped-by-weekday"]')).toBeInTheDocument();
  });

  // ─── 日タイムラインの高さチェーン (下端切れ回帰・2026-07-08) ──────────────
  // 左ペイン (lg:overflow-hidden) → タブパネル → course-day-timeline-view →
  // 盤面 (lg:h-full) の全リンクに flex チェーンが必要。1 つでも欠けると盤面が
  // 内容高で描画され、下端が overflow-hidden に切り捨てられてスクロール不能になる。
  // jsdom はレイアウト計算をしないため className 契約で担保する。
  it('T-2S. 日タイムライン表示時にタブパネルが高さチェーンを繋ぐ', () => {
    setupHooks({
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <CourseDayTablePanel weekStart={monday(2026, 5, 4)} officeId={null} canEdit={true} />
      </QueryClientProvider>,
    );
    // 既定 activeTab='week' → 月曜タブへ切替 (weekdayViewMode 既定='timeline')
    fireEvent.click(screen.getByTestId('course-day-tab-0'));
    const view = screen.getByTestId('course-day-timeline-view');
    for (const cls of ['lg:min-h-0', 'lg:flex-1']) {
      expect(view.className).toContain(cls);
    }
    // 中間のタブパネル (flex チェーン欠落が下端切れの正体だったリンク)
    const tabpanel = screen.getByTestId('course-day-table-list');
    for (const cls of ['lg:flex', 'lg:min-h-0', 'lg:flex-1', 'lg:flex-col']) {
      expect(tabpanel.className).toContain(cls);
    }
    // 左ペイン = タブパネルの親。盤面へスクロール委譲する固定高ペイン
    const pane = tabpanel.parentElement as HTMLElement;
    for (const cls of ['lg:flex', 'lg:flex-col', 'lg:overflow-hidden', 'lg:min-h-0']) {
      expect(pane.className).toContain(cls);
    }
    // 盤面自身は内部スクロール + 親高さいっぱい
    const board = screen.getByTestId('timeline-day-board');
    expect(board.className).toContain('overflow-auto');
    expect(board.className).toContain('lg:h-full');

    // 負のケース: リストモードではペインスクロール (lg:overflow-y-auto) に戻り、
    // タブパネルに flex チェーンを付けない (無条件付与への退行を防ぐ)。
    fireEvent.click(screen.getByTestId('course-day-mode-list'));
    const listTabpanel = screen.getByTestId('course-day-table-list');
    expect(listTabpanel.className).not.toContain('lg:flex');
    expect((listTabpanel.parentElement as HTMLElement).className).toContain('lg:overflow-y-auto');
  });

  // ─── T-6 パリティ①: プールカード → タイムライン列の直接ドロップ配置 ────────
  it('T-6P1. プールカードをタイムライン列へドロップすると15分スナップ位置で place-and-fix が呼ばれる', async () => {
    setupHooks({
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      patients: [
        {
          id: 'p-tl',
          name: 'タイムライン 太郎',
          weekly_pattern: { service_minutes: 30 },
          requires_multiple_staff: false,
        },
      ],
    });
    mockPlaceAndFix.mockResolvedValue({});
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <CourseDayTablePanel weekStart={monday(2026, 5, 4)} officeId={null} canEdit={true} />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByTestId('course-day-tab-0'));
    expect(screen.getByTestId('course-day-timeline-view')).toBeInTheDocument();

    // 列 droppable (tl-col:{templateId}:{weekday}) へ、盤面上端から 130px の位置に
    // プールカードをドロップ。snapYOffsetToMinutes と同じ算法で期待値を導出する。
    const expectedMin = snapYOffsetToMinutes(130 - 10);
    const hh = String(Math.floor(expectedMin / 60)).padStart(2, '0');
    const mm = String(expectedMin % 60).padStart(2, '0');
    await dndState.capturedHandlers.onDragEnd!({
      active: {
        id: 'pool-patient:p-tl',
        rect: { current: { translated: { top: 130 } } },
      },
      over: { id: 'tl-col:tpl-A:0', rect: { top: 10 } },
    });
    expect(mockPlaceAndFix).toHaveBeenCalledWith(
      expect.objectContaining({
        patient_id: 'p-tl',
        course_template_id: 'tpl-A',
        weekday: 0,
        start_time: `${hh}:${mm}`,
        duration_min: 30,
        fix_pattern: false,
      }),
    );
  });

  // ─── T-6 パリティ②: イベント帯 → タイムライン列の DnD 移動 ────────────────
  it('T-6P2. イベント帯をタイムライン列へドロップすると15分スナップ位置で update が呼ばれる', async () => {
    mockUpdateEventDrag.mockResolvedValue({});
    // buildStaffEventsMap は再レンダー毎に呼ばれるため Once では 2 回目以降が空に戻る。
    // 恒常差し替え + finally で既定実装 (空 Map) へ戻す (後続テストへの漏れ防止)。
    mockBuildStaffEventsMap.mockReturnValue(
      new Map([
        [
          'staff-1',
          [
            {
              id: 'ev-1',
              date: '2026-05-04', // 月曜 (weekday 0) — 案X の同曜日チェックを通す
              start_time: '10:00',
              end_time: '11:00',
              type: '会議',
              title: null,
            },
          ],
        ],
      ]),
    );
    setupHooks({
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
          assigned_staff_id: 'staff-1', // 案Q: drop 先コースの担当 (= 移動先 staff)
          course_status: 'course_fixed',
          deleted_at: null,
        },
      ],
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <CourseDayTablePanel weekStart={monday(2026, 5, 4)} officeId={null} canEdit={true} />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByTestId('course-day-tab-0'));

    const expectedMin = snapYOffsetToMinutes(130 - 10);
    const hh = String(Math.floor(expectedMin / 60)).padStart(2, '0');
    const mm = String(expectedMin % 60).padStart(2, '0');
    const endMin = expectedMin + 60; // duration 60分 維持
    const eh = String(Math.floor(endMin / 60)).padStart(2, '0');
    const em = String(endMin % 60).padStart(2, '0');
    await dndState.capturedHandlers.onDragEnd!({
      active: {
        id: 'event:ev-1',
        rect: { current: { translated: { top: 130 } } },
      },
      over: { id: 'tl-col:tpl-A:0', rect: { top: 10 } },
    });
    expect(mockUpdateEventDrag).toHaveBeenCalledWith(
      expect.objectContaining({
        staffId: 'staff-1',
        eventId: 'ev-1',
        payload: expect.objectContaining({
          start_time: `${hh}:${mm}`,
          end_time: `${eh}:${em}`,
        }),
      }),
    );
    // 同一スタッフのため new_staff_id は送らない (時刻スライドのみ)。
    const payload = mockUpdateEventDrag.mock.calls[0]![0].payload as Record<string, unknown>;
    expect(payload.new_staff_id).toBeUndefined();
    // 既定実装 (空 Map) へ戻す (後続テストへの漏れ防止)。
    mockBuildStaffEventsMap.mockImplementation(() => new Map());
  });

  // ─── G4: タイムラインカード → プールへドロップして外す ─────────────────────
  const tlCourse = {
    id: 'course-1',
    iso_year: 2026,
    iso_week: 19,
    weekday: 0,
    code: 'A',
    office_id: 'office-honten',
    assigned_staff_id: null,
    course_status: 'course_fixed' as const,
    deleted_at: null,
  };

  it('G4-1. tl-visit をプールへドロップすると DELETE /visits/{id} (cascade=false) が呼ばれる', async () => {
    mockDeleteVisit.mockResolvedValue(undefined);
    setupHooks({
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      courses: [tlCourse],
      visits: [
        {
          id: 'v-1',
          patient_id: 'p-1',
          patient_name: '田中 太郎',
          visit_date: '2026-05-04',
          start_time: '10:00:00',
          primary_staff_id: null,
          course_id: 'course-1',
          required_staff_count: 1,
          type: 'regular',
          status: 'planned',
          source: 'allocate',
          end_time: '10:30:00',
        },
      ],
      patients: [{ id: 'p-1', name: '田中 太郎', kana: null, status: 'active', address: '' }],
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <CourseDayTablePanel
          weekStart={monday(2026, 5, 4)}
          officeId="office-honten"
          canEdit={true}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByTestId('course-day-tab-0'));
    expect(screen.getByTestId('course-day-timeline-view')).toBeInTheDocument();

    await dndState.capturedHandlers.onDragEnd!({
      active: { id: 'tl-visit:v-1' },
      over: { id: 'pool' },
    });
    expect(mockDeleteVisit).toHaveBeenCalledOnce();
    expect(mockDeleteVisit.mock.calls[0][0]).toEqual(
      expect.objectContaining({ id: 'v-1', cascadeFixedVisit: false }),
    );
    // プール戻しは delete のみ (再配置しない)。
    expect(mockPlaceAndFix).not.toHaveBeenCalled();
    expect(mockToast.success).toHaveBeenCalledWith('田中 太郎 をプールに戻しました');
  });

  it('G4-2. tl-pair (同住所2名) をプールへドロップすると 2 件とも同一 op_group_id で削除される', async () => {
    mockDeleteVisit.mockResolvedValue(undefined);
    setupHooks({
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      courses: [tlCourse],
      visits: [
        {
          id: 'v-1',
          patient_id: 'p-1',
          patient_name: '田中 太郎',
          visit_date: '2026-05-04',
          start_time: '10:00:00',
          primary_staff_id: null,
          course_id: 'course-1',
          required_staff_count: 1,
          type: 'regular',
          status: 'planned',
          source: 'allocate',
          end_time: '10:30:00',
        },
        {
          id: 'v-2',
          patient_id: 'p-2',
          patient_name: '田中 花子',
          visit_date: '2026-05-04',
          start_time: '10:00:00',
          primary_staff_id: null,
          course_id: 'course-1',
          required_staff_count: 1,
          type: 'regular',
          status: 'planned',
          source: 'allocate',
          end_time: '10:30:00',
        },
      ],
      patients: [
        {
          id: 'p-1',
          name: '田中 太郎',
          kana: null,
          status: 'active',
          address: '同じ家',
          lat: 35.6,
          lng: 140.1,
        },
        {
          id: 'p-2',
          name: '田中 花子',
          kana: null,
          status: 'active',
          address: '同じ家',
          lat: 35.6,
          lng: 140.1,
        },
      ],
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <CourseDayTablePanel
          weekStart={monday(2026, 5, 4)}
          officeId="office-honten"
          canEdit={true}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByTestId('course-day-tab-0'));

    await dndState.capturedHandlers.onDragEnd!({
      active: { id: 'tl-pair:v-1:v-2' },
      over: { id: 'pool' },
    });
    expect(mockDeleteVisit).toHaveBeenCalledTimes(2);
    const c0 = mockDeleteVisit.mock.calls[0][0] as Record<string, unknown>;
    const c1 = mockDeleteVisit.mock.calls[1][0] as Record<string, unknown>;
    expect(c0).toEqual(expect.objectContaining({ id: 'v-1', cascadeFixedVisit: false }));
    expect(c1).toEqual(expect.objectContaining({ id: 'v-2', cascadeFixedVisit: false }));
    // 1 ユーザー操作 = 同一 op_group_id (undo でまとめて戻る)。
    expect(c0.op_group_id).toBeTruthy();
    expect(c0.op_group_id).toBe(c1.op_group_id);
  });

  it('G4-3. tl-visit をタイムライン外 (プールでも列でもない場所) に落としても何も起きない', async () => {
    mockDeleteVisit.mockResolvedValue(undefined);
    setupHooks({
      templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      courses: [tlCourse],
      visits: [
        {
          id: 'v-1',
          patient_id: 'p-1',
          patient_name: '田中 太郎',
          visit_date: '2026-05-04',
          start_time: '10:00:00',
          primary_staff_id: null,
          course_id: 'course-1',
          required_staff_count: 1,
          type: 'regular',
          status: 'planned',
          source: 'allocate',
          end_time: '10:30:00',
        },
      ],
      patients: [{ id: 'p-1', name: '田中 太郎', kana: null, status: 'active', address: '' }],
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <CourseDayTablePanel
          weekStart={monday(2026, 5, 4)}
          officeId="office-honten"
          canEdit={true}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByTestId('course-day-tab-0'));
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: 'tl-visit:v-1' },
      over: { id: 'something-else' },
    });
    expect(mockDeleteVisit).not.toHaveBeenCalled();
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
