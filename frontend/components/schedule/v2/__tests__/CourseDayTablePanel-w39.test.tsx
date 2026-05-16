/**
 * CourseDayTablePanel — Wave 39 統合テスト.
 *
 * 「スタッフイベント D&D で時刻スライド + 担当者変更」のハンドラ分岐検証.
 *
 * カバーするシナリオ:
 *   W39-D-1. event drop / 同曜日 / 担当割当済み → PATCH に new_staff_id +
 *            start_time/end_time (duration 維持) が含まれる
 *   W39-D-2. 別曜日 (案 X) → toast 警告 + PATCH 呼ばれない
 *   W39-D-3. 担当未割当 (案 Q) → toast 警告 + PATCH 呼ばれない
 *   W39-D-4. 衝突 — 同 staff の他 event 重複 → toast 警告 + PATCH 呼ばれない (案 K)
 *   W39-D-5. 衝突 — 同 staff 担当 visit と重複 → toast 警告 + PATCH 呼ばれない (案 K)
 *   W39-D-6. 同一 staff へのスライド (担当者変更なし) → new_staff_id 省略で PATCH
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';

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

describe('CourseDayTablePanel — Wave 39 event D&D', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    dndState.capturedHandlers.onDragEnd = undefined;
    mockEventsByStaff = new Map();
  });

  it('W39-D-1. event drop (同曜日 + 担当割当済み) → PATCH に new_staff_id + 時刻が含まれる', async () => {
    defaultSetupForEventDrag();
    mockUpdateEventDrag.mockResolvedValue({});
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    // event を tpl-A (担当 STAFF_OLD) → tpl-B (担当 STAFF_NEW) の 13:00 にドロップ
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: `event:${EVENT_ID}` },
      over: { id: 'course-day-cell:0:tpl-B:13:00' },
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

  it('W39-D-2. 別曜日への drop (案 X) → 警告 + PATCH 呼ばれない', async () => {
    defaultSetupForEventDrag();
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    // event の date は月曜 (weekday=0) なのに weekday=2 (水曜) のセルへドロップ
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: `event:${EVENT_ID}` },
      over: { id: 'course-day-cell:2:tpl-B:13:00' },
    });
    expect(mockUpdateEventDrag).not.toHaveBeenCalled();
    expect(mockToast.warning).toHaveBeenCalledWith(expect.stringMatching(/別の曜日|同じ曜日/));
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
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: `event:${EVENT_ID}` },
      over: { id: 'course-day-cell:0:tpl-B:13:00' },
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
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: `event:${EVENT_ID}` },
      over: { id: 'course-day-cell:0:tpl-B:13:00' },
    });
    expect(mockUpdateEventDrag).not.toHaveBeenCalled();
    expect(mockToast.warning).toHaveBeenCalledWith(expect.stringMatching(/他のイベント/));
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
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    // event を 13:00 (-14:00) に move → STAFF_NEW 担当の visit (13:15-14:00) と重複
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: `event:${EVENT_ID}` },
      over: { id: 'course-day-cell:0:tpl-B:13:00' },
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
    render(
      <CourseDayTablePanel
        weekStart={monday(2026, 5, 4)}
        officeId="office-honten"
        canEdit={true}
        showAcceptanceLayer={false}
      />,
    );
    // 同じ tpl-A (担当 STAFF_OLD) の 14:00 にドロップ → スライドのみ
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: `event:${EVENT_ID}` },
      over: { id: 'course-day-cell:0:tpl-A:14:00' },
    });
    expect(mockUpdateEventDrag).toHaveBeenCalledOnce();
    const call = mockUpdateEventDrag.mock.calls[0][0];
    expect(call.staffId).toBe(STAFF_OLD);
    expect(call.payload.start_time).toBe('14:00');
    expect(call.payload.end_time).toBe('14:30'); // 30 分維持
    expect(call.payload.new_staff_id).toBeUndefined(); // 同一 staff のため省略
  });
});
