/**
 * CourseDayTablePanel — Wave 39 統合テスト.
 *
 * 「スタッフイベント D&D で時刻スライド + 担当者変更」のハンドラ分岐検証.
 *
 * Phase 2 (日テーブル撤去): ドロップ先は `course-day-cell:` から
 *   タイムライン列 `tl-col:{templateId}:{weekday}` + Y オフセットへ移行した
 *   (`dropEventOnColumn` ヘルパー参照). ハンドラの後段 (案 X/Q/K) は不変。
 *
 * カバーするシナリオ:
 *   W39-D-1. event drop / 同曜日 / 担当割当済み → PATCH に new_staff_id +
 *            start_time/end_time (duration 維持) が含まれる
 *   W39-D-2. 表示中の曜日に無い列 (案 X) → PATCH 呼ばれない
 *   W39-D-3. 担当未割当 (案 Q) → toast 警告 + PATCH 呼ばれない
 *   W39-D-4. 衝突 — 同 staff の他 event 重複 → toast 警告 + PATCH 呼ばれない (案 K)
 *   W39-D-5. 衝突 — 同 staff 担当 visit と重複 → toast 警告 + PATCH 呼ばれない (案 K)
 *   W39-D-6. 同一 staff へのスライド (担当者変更なし) → new_staff_id 省略で PATCH
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { timeToY } from '@/lib/scheduling/timeline';

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
// 残りは Proxy フォールバックで自動的に空 span を返す (Panel.test.tsx と同方式)。
vi.mock('lucide-react', () => {
  const named: Record<string, () => React.ReactElement> = {
    Loader2: () => <span data-testid="loader" />,
    RefreshCw: () => <span data-testid="refresh-icon" />,
    UserCheck: () => <span data-testid="user-check-icon" />,
    Pin: () => <span data-testid="pin-icon" />,
  };
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
const mockUpdateEventDrag = vi.fn();
// staff-events 用に「staffId → EventRead[]」を返すための制御変数
let mockEventsByStaff: Map<string, unknown[]> = new Map();

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

vi.mock('@/lib/queries/visitMoveWeekOnly', () => ({
  useVisitMoveWeekOnly: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/staff-events', () => ({
  useWeekStaffEvents: () => ({ data: [], isLoading: false }),
  buildStaffEventsMap: () => mockEventsByStaff,
  useUpdateEventForDrag: () => ({ mutateAsync: mockUpdateEventDrag, isPending: false }),
}));

import { CourseDayTablePanel } from '../CourseDayTablePanel';

function monday(year: number, month: number, day: number): Date {
  const d = new Date(year, month - 1, day, 0, 0, 0, 0);
  const dow = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - dow);
  return d;
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

const STAFF_OLD = '11111111-1111-1111-1111-111111111111';
const STAFF_NEW = '22222222-2222-2222-2222-222222222222';
const EVENT_ID = '33333333-3333-3333-3333-333333333333';

interface SetupOpts {
  staff?: Array<Record<string, unknown>>;
  visits?: Array<Record<string, unknown>>;
  courses?: Array<Record<string, unknown>>;
  templates?: Array<Record<string, unknown>>;
  patients?: Array<Record<string, unknown>>;
  offices?: Array<{ id: string; name: string }>;
  eventsByStaff?: Map<string, unknown[]>;
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
  mockEventsByStaff = opts.eventsByStaff ?? new Map();
}

function defaultSetupForEventDrag(opts: { eventsByStaff?: Map<string, unknown[]> } = {}) {
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
        assigned_staff_id: STAFF_OLD,
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
        assigned_staff_id: STAFF_NEW,
        course_status: 'course_fixed',
        deleted_at: null,
      },
    ],
    staff: [
      { id: STAFF_OLD, name: '元担当', primary_office_id: 'office-honten', status: 'active' },
      { id: STAFF_NEW, name: '新担当', primary_office_id: 'office-honten', status: 'active' },
    ],
    eventsByStaff:
      opts.eventsByStaff ??
      new Map([
        [
          STAFF_OLD,
          [
            {
              id: EVENT_ID,
              staff_id: STAFF_OLD,
              date: '2026-05-04', // 月曜
              title: '研修',
              start_time: '10:00',
              end_time: '11:00',
              type: '研修',
            },
          ],
        ],
      ]),
  });
}

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
 * イベント帯 → タイムライン列 (`tl-col:{templateId}:{weekday}`) のドロップ引数を作る.
 * Phase 2 で日テーブル (`course-day-cell:` droppable) を撤去したため、ドロップ先の
 * 時刻は「列 rect 上端からの Y オフセット」で表現する (snapYOffsetToMinutes の逆算)。
 */
function dropEventOnColumn(eventId: string, templateId: string, weekday: number, hm: string) {
  const overTop = 10;
  return {
    active: {
      id: `event:${eventId}`,
      rect: { current: { translated: { top: overTop + (timeToY(hm) ?? 0) } } },
    },
    over: { id: `tl-col:${templateId}:${weekday}`, rect: { top: overTop } },
  };
}

describe('CourseDayTablePanel — Wave 39 event D&D', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    dndState.capturedHandlers.onDragEnd = undefined;
    mockEventsByStaff = new Map();
  });

  it('W39-D-1. event drop (同曜日 + 担当割当済み) → PATCH に new_staff_id + 時刻が含まれる', async () => {
    defaultSetupForEventDrag();
    mockUpdateEventDrag.mockResolvedValue({});
    renderPanel();
    // event を tpl-A (担当 STAFF_OLD) → tpl-B (担当 STAFF_NEW) の 13:00 にドロップ
    await dndState.capturedHandlers.onDragEnd!({
      ...dropEventOnColumn(EVENT_ID, 'tpl-B', 0, '13:00'),
    });
    expect(mockUpdateEventDrag).toHaveBeenCalledOnce();
    const call = mockUpdateEventDrag.mock.calls[0][0];
    expect(call.staffId).toBe(STAFF_OLD); // 元担当 (URL)
    expect(call.eventId).toBe(EVENT_ID);
    expect(call.payload.new_staff_id).toBe(STAFF_NEW); // 移動先 (body)
    expect(call.payload.start_time).toBe('13:00');
    // duration 60 分維持
    expect(call.payload.end_time).toBe('14:00');
    expect(mockToast.success).toHaveBeenCalled();
  });

  it('W39-D-2. 表示中の曜日に無い列への drop → PATCH 呼ばれない (案 X)', async () => {
    defaultSetupForEventDrag();
    renderPanel();
    // Phase 2: 日テーブル撤去後、ドロップ先は「表示中の曜日のタイムライン列」だけ。
    // 列 id に別曜日 (weekday=2) を指定しても該当列が存在せず仮想セルが合成されないため
    // 何も起きない (= 案 X「同じ曜日内でのみスライド可能」が構造的に保証される)。
    // 旧テーブルはどの曜日のセルにも落とせたため toast 警告で弾く必要があった。
    await dndState.capturedHandlers.onDragEnd!({
      ...dropEventOnColumn(EVENT_ID, 'tpl-B', 2, '13:00'),
    });
    expect(mockUpdateEventDrag).not.toHaveBeenCalled();
  });

  it('W39-D-3. drop 先 course の担当未割当 (案 Q) → 警告 + PATCH 呼ばれない', async () => {
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
          assigned_staff_id: STAFF_OLD,
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
          assigned_staff_id: null, // 未割当
          course_status: 'course_fixed',
          deleted_at: null,
        },
      ],
      staff: [
        { id: STAFF_OLD, name: '元担当', primary_office_id: 'office-honten', status: 'active' },
      ],
      eventsByStaff: new Map([
        [
          STAFF_OLD,
          [
            {
              id: EVENT_ID,
              staff_id: STAFF_OLD,
              date: '2026-05-04',
              title: '研修',
              start_time: '10:00',
              end_time: '11:00',
              type: '研修',
            },
          ],
        ],
      ]),
    });
    renderPanel();
    await dndState.capturedHandlers.onDragEnd!({
      ...dropEventOnColumn(EVENT_ID, 'tpl-B', 0, '13:00'),
    });
    expect(mockUpdateEventDrag).not.toHaveBeenCalled();
    expect(mockToast.warning).toHaveBeenCalledWith(expect.stringMatching(/未割当/));
  });

  it('W39-D-4. 衝突 — 同 staff の他 event 重複 (案 K) → 警告 + PATCH 呼ばれない', async () => {
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
          assigned_staff_id: STAFF_OLD,
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
          assigned_staff_id: STAFF_NEW,
          course_status: 'course_fixed',
          deleted_at: null,
        },
      ],
      staff: [
        { id: STAFF_OLD, name: '元担当', primary_office_id: 'office-honten', status: 'active' },
        { id: STAFF_NEW, name: '新担当', primary_office_id: 'office-honten', status: 'active' },
      ],
      eventsByStaff: new Map([
        [
          STAFF_OLD,
          [
            {
              id: EVENT_ID,
              staff_id: STAFF_OLD,
              date: '2026-05-04',
              title: '研修',
              start_time: '10:00',
              end_time: '11:00',
              type: '研修',
            },
          ],
        ],
        [
          STAFF_NEW,
          [
            {
              id: 'event-blocker',
              staff_id: STAFF_NEW,
              date: '2026-05-04',
              title: '別予定',
              start_time: '12:30',
              end_time: '13:30', // 13:00-14:00 と重複
              type: '研修',
            },
          ],
        ],
      ]),
    });
    renderPanel();
    await dndState.capturedHandlers.onDragEnd!({
      ...dropEventOnColumn(EVENT_ID, 'tpl-B', 0, '13:00'),
    });
    expect(mockUpdateEventDrag).not.toHaveBeenCalled();
    expect(mockToast.warning).toHaveBeenCalledWith(expect.stringMatching(/他のイベント/));
  });

  it('W39-D-4b. 今週だけ外した (cancelled_at) イベントは衝突扱いしない', async () => {
    // 週空間 Phase E (D2): 外したイベントは「予定」ではないので邪魔をしない。
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
          assigned_staff_id: STAFF_OLD,
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
          assigned_staff_id: STAFF_NEW,
          course_status: 'course_fixed',
          deleted_at: null,
        },
      ],
      staff: [
        { id: STAFF_OLD, name: '元担当', primary_office_id: 'office-honten', status: 'active' },
        { id: STAFF_NEW, name: '新担当', primary_office_id: 'office-honten', status: 'active' },
      ],
      eventsByStaff: new Map([
        [
          STAFF_OLD,
          [
            {
              id: EVENT_ID,
              staff_id: STAFF_OLD,
              date: '2026-05-04',
              title: '研修',
              start_time: '10:00',
              end_time: '11:00',
              type: '研修',
            },
          ],
        ],
        [
          STAFF_NEW,
          [
            {
              id: 'event-blocker',
              staff_id: STAFF_NEW,
              date: '2026-05-04',
              title: '別予定',
              start_time: '12:30',
              end_time: '13:30', // 時間帯は重なるが、今週だけ外している
              type: '研修',
              cancelled_at: '2026-05-03T00:00:00Z',
            },
          ],
        ],
      ]),
    });
    renderPanel();
    await dndState.capturedHandlers.onDragEnd!({
      ...dropEventOnColumn(EVENT_ID, 'tpl-B', 0, '13:00'),
    });
    expect(mockToast.warning).not.toHaveBeenCalledWith(expect.stringMatching(/他のイベント/));
    expect(mockUpdateEventDrag).toHaveBeenCalled();
  });

  it('W39-D-5. 衝突 — 同 staff 担当 visit と重複 (案 K) → 警告 + PATCH 呼ばれない', async () => {
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
          assigned_staff_id: STAFF_OLD,
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
          assigned_staff_id: STAFF_NEW,
          course_status: 'course_fixed',
          deleted_at: null,
        },
      ],
      staff: [
        { id: STAFF_OLD, name: '元担当', primary_office_id: 'office-honten', status: 'active' },
        { id: STAFF_NEW, name: '新担当', primary_office_id: 'office-honten', status: 'active' },
      ],
      visits: [
        {
          id: 'v-block',
          patient_id: 'pat-1',
          patient_name: '田中',
          visit_date: '2026-05-04',
          start_time: '13:15:00',
          end_time: '14:00:00',
          course_id: 'course-B', // STAFF_NEW 担当のコース
          required_staff_count: 1,
          visit_group_id: null,
          type: 'regular',
          status: 'planned',
          source: 'allocate',
        },
      ],
      eventsByStaff: new Map([
        [
          STAFF_OLD,
          [
            {
              id: EVENT_ID,
              staff_id: STAFF_OLD,
              date: '2026-05-04',
              title: '研修',
              start_time: '10:00',
              end_time: '11:00',
              type: '研修',
            },
          ],
        ],
      ]),
    });
    renderPanel();
    // event を 13:00 (-14:00) に move → STAFF_NEW 担当の visit (13:15-14:00) と重複
    await dndState.capturedHandlers.onDragEnd!({
      ...dropEventOnColumn(EVENT_ID, 'tpl-B', 0, '13:00'),
    });
    expect(mockUpdateEventDrag).not.toHaveBeenCalled();
    expect(mockToast.warning).toHaveBeenCalledWith(expect.stringMatching(/訪問予定/));
  });

  it('W39-D-6. 同一 staff へのスライド (担当者変更なし) → new_staff_id は body に含まない', async () => {
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
          assigned_staff_id: STAFF_OLD,
          course_status: 'course_fixed',
          deleted_at: null,
        },
      ],
      staff: [
        { id: STAFF_OLD, name: '元担当', primary_office_id: 'office-honten', status: 'active' },
      ],
      eventsByStaff: new Map([
        [
          STAFF_OLD,
          [
            {
              id: EVENT_ID,
              staff_id: STAFF_OLD,
              date: '2026-05-04',
              title: '研修',
              start_time: '10:00',
              end_time: '10:30',
              type: '研修',
            },
          ],
        ],
      ]),
    });
    mockUpdateEventDrag.mockResolvedValue({});
    renderPanel();
    // 同じ tpl-A (担当 STAFF_OLD) の 14:00 にドロップ → スライドのみ
    await dndState.capturedHandlers.onDragEnd!({
      ...dropEventOnColumn(EVENT_ID, 'tpl-A', 0, '14:00'),
    });
    expect(mockUpdateEventDrag).toHaveBeenCalledOnce();
    const call = mockUpdateEventDrag.mock.calls[0][0];
    expect(call.staffId).toBe(STAFF_OLD);
    expect(call.payload.start_time).toBe('14:00');
    expect(call.payload.end_time).toBe('14:30'); // 30 分維持
    expect(call.payload.new_staff_id).toBeUndefined(); // 同一 staff のため省略
  });
});
