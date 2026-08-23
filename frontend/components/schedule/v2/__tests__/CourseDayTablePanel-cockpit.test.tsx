/**
 * CourseDayTablePanel — 職員スケジュールタブ「今週の運転席」の結線 (Phase E / FE-C)。
 *
 * カバーするシナリオ:
 *   (a) 訪問行クリックでメニューが開き、「今週だけ取消」が visit-cancel-week を呼ぶ
 *   (b) セルの「🛌 休みにする」→ staff-off-week を 1 回だけ呼ぶ (据え置き件数 / NG 422 の確認も)
 *   (c) 表示切替 [リスト|タイムライン]
 *   (d) 同期バーで選んだ**訪問**差分が盤面ゴースト (青点線=今ここ / 紫実線=こう変わる) になる
 *   (g) スタッフ入れ替え (氏名 ⠿ DnD → 確認ダイアログ) が
 *       コース丸ごと = PATCH /courses / 混在 = visit-assign を件数分 (同一 op_group_id)
 *
 * 既存の CourseDayTablePanel テストと同じ作法 (query hook を全部モックし、
 * QueryClientProvider で包む) に倣う。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// ─── モック ──────────────────────────────────────────────────────────────────

const { mockToast, callOrder, invalidatedKeys } = vi.hoisted(() => ({
  mockToast: { warning: vi.fn(), success: vi.fn(), error: vi.fn(), info: vi.fn() },
  callOrder: [] as string[],
  /** invalidateQueries に渡った queryKey の記録 (失効漏れの回帰検知)。 */
  invalidatedKeys: [] as unknown[],
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
  DndContext: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DragOverlay: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  PointerSensor: class PointerSensor {},
  TouchSensor: class TouchSensor {},
  useSensor: (cls: unknown, opts?: unknown) => ({ sensor: cls, opts }),
  useSensors: (...args: unknown[]) => args,
}));
vi.mock('@dnd-kit/utilities', () => ({ CSS: { Translate: { toString: () => '' } } }));
vi.mock('sonner', () => ({ toast: mockToast }));
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: null, status: 'unauthenticated' }),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), back: vi.fn() }),
  usePathname: () => '/schedule',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('lucide-react', () => {
  const named: Record<string, () => React.ReactElement> = {};
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
  Card: ({ children, ...rest }: { children: React.ReactNode; [k: string]: unknown }) => (
    <div {...rest}>{children}</div>
  ),
}));
vi.mock('@/components/ui/skeleton', () => ({ Skeleton: () => <div data-testid="skeleton" /> }));
vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

// 同期バーは RPA / 未送信 API を叩くため、差分を 1 件流すだけのスタブに置き換える。
vi.mock('../cockpit/SyncBar', () => ({
  SyncBar: ({
    onSelectDiff,
    onUnsentChange,
  }: {
    onSelectDiff: (m: unknown) => void;
    onUnsentChange?: (keys: Set<string>) => void;
  }) => (
    <>
      <button
        type="button"
        data-testid="stub-report-unsent"
        // 月曜 09:00 の田中様が ●未送信 (氏名は全角スペース入りで渡し正規化を見る)。
        onClick={() => onUnsentChange?.(new Set([`v|${MONDAY_ISO}|09:00|田中`]))}
      >
        未送信を報告
      </button>
      <button
        type="button"
        data-testid="stub-report-unsent-empty"
        onClick={() => onUnsentChange?.(new Set())}
      >
        未送信ゼロを報告
      </button>
      <button
        type="button"
        data-testid="stub-select-diff"
        onClick={() =>
          onSelectDiff({
            kind: 'visit',
            action: 'update',
            externalId: 'diff-1',
            title: '田中',
            patient_name: '田中',
            start: '10:30',
            end: '11:30',
            beforeStart: '09:00',
            beforeEnd: '10:00',
            before: {
              staff_id: STAFF_1,
              staff_name: '佐藤',
              date: MONDAY_ISO,
              start: '09:00',
              end: '10:00',
              course_label: '身体1',
            },
            after: {
              staff_id: STAFF_1,
              staff_name: '佐藤',
              date: MONDAY_ISO,
              start: '10:30',
              end: '11:30',
              course_label: '身体1',
            },
          })
        }
      >
        差分を選ぶ
      </button>
    </>
  ),
}));

// ─── query hooks ───
const mockOffices = vi.fn();
const mockPatients = vi.fn();
const mockStaffList = vi.fn();
const mockUseQueries = vi.fn();
const mockVisits = vi.fn();
const mockCourses = vi.fn();
const mockVisitCancelWeek = vi.fn().mockResolvedValue({ id: 'v1', status: 'cancelled' });
const mockVisitServiceOverride = vi
  .fn()
  .mockResolvedValue({ id: 'v1', kaipoke_service_override: '基本療養費Ⅰ・准看' });
/**
 * 🛌 休みにする (PO 決定 2026-08-23) は **1 リクエスト**。休みの登録と付け替えを
 * BE が 1 トランザクションで行うので、FE 側の呼び出し順の心配が要らなくなった。
 */
const mockStaffOffWeek = vi.fn().mockImplementation(async (payload: { to_staff_id: unknown }) => {
  callOrder.push('staff-off-week');
  return {
    override_id: '00000000-0000-4000-8000-0000000000aa',
    moved_visit_ids: ['v1'],
    moved_course_ids: [],
    skipped_visit_ids: [],
    to_staff_id: payload.to_staff_id ?? null,
    op_group_id: '00000000-0000-4000-8000-0000000000bb',
  };
});
const mockAssignStaffWeek = vi.fn().mockImplementation(async () => {
  callOrder.push('assign');
  return { changed: true };
});
/** PATCH /courses/{id} (コース丸ごとの担当変更)。入れ替えの経路判定に使う。 */
const mockUpdateCourse = vi.fn().mockImplementation(async () => {
  callOrder.push('update-course');
  return { id: 'course-A-mon' };
});

/**
 * 「担当なし」からの投入提案 (Phase 2-B)。ツールバー「◎ 提案を見る」が
 * 叩く命令的 fetch と、ポップオーバー内の query の両方をここで供給する。
 */
const ASSIGN_CANDIDATES_RESULT = {
  absent_staff: null,
  date: '',
  weekday: 0,
  groups: [] as unknown[],
  warnings: [] as string[],
  whole_ok_staff_ids: [] as string[],
  /** BE 契約 2026-08-23: course_ids のときのコース別内訳 (バッジの件数の正)。 */
  whole_ok_by_course: {} as Record<string, string[]>,
};
const mockFetchAssignCandidates = vi.fn(
  async (_params: Record<string, unknown>) => ASSIGN_CANDIDATES_RESULT,
);
const mockUseAssignCandidates = vi.fn(() => ({
  data: ASSIGN_CANDIDATES_RESULT,
  isPending: false,
  isError: false,
  error: null,
  fetchStatus: 'idle',
  refetch: vi.fn(),
}));

vi.mock('@tanstack/react-query', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type TanstackQuery = typeof import('@tanstack/react-query');
  const actual = await importOriginal<TanstackQuery>();
  return {
    ...actual,
    useQueries: (...args: unknown[]) => mockUseQueries(...args),
    // invalidateQueries の queryKey を記録する薄いラッパ (失効漏れの検知)。
    useQueryClient: () => {
      const qc = actual.useQueryClient();
      const invalidate = qc.invalidateQueries.bind(qc);
      return Object.assign(Object.create(Object.getPrototypeOf(qc) as object), qc, {
        invalidateQueries: (filters?: { queryKey?: unknown }) => {
          invalidatedKeys.push(filters?.queryKey);
          return invalidate(filters as never);
        },
      }) as typeof qc;
    },
  };
});
vi.mock('@/lib/api/fetcher', () => ({ fetcher: vi.fn() }));
vi.mock('@/lib/queries/weekday_staff_capacity', () => ({
  useWeekdayStaffCapacityLookup: () => ({
    staffCountFor: () => 5,
    managerCountFor: () => 0,
    courseCodesMax: 5,
    isLoading: false,
  }),
}));
vi.mock('@/lib/queries/pfv_course_presence', () => ({
  usePfvCoursePresenceLookup: () => ({ pfvCountFor: () => 0, isLoading: false }),
}));
vi.mock('@/lib/queries/offices', () => ({
  useOffices: (...args: unknown[]) => mockOffices(...args),
}));
vi.mock('@/lib/queries/patients', () => ({
  usePatients: (...args: unknown[]) => mockPatients(...args),
  useCreatePatient: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/staff', () => ({
  useStaffList: (...args: unknown[]) => mockStaffList(...args),
}));
vi.mock('@/lib/queries/visits', () => ({
  useVisits: (...args: unknown[]) => mockVisits(...args),
  useDeleteVisit: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateVisit: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/courses', () => ({
  useCourses: (...args: unknown[]) => mockCourses(...args),
  useUpdateCourse: () => ({ mutateAsync: mockUpdateCourse, isPending: false }),
}));
vi.mock('@/lib/queries/place_and_fix', () => ({
  usePlaceAndFix: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/generate_week', () => ({
  useGenerateWeek: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useGenerateWeekOnly: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/assign_staff_only', () => ({
  useAssignStaffOnly: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useApplyStaffReview: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/g21', () => ({
  useTogglePfvPin: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  useBulkPinPfvs: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/autoScheduleV2', () => {
  const noop = () => ({
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    data: undefined,
    error: null,
    isSuccess: false,
  });
  return {
    useDiffAddProposalsMutation: noop,
    useFullOptimizeMutation: noop,
    useApplyIndividualMutation: noop,
    useResetToFixedMutation: noop,
    useApplyWeekOnlyMutation: noop,
    useUnassignAllStaffMutation: noop,
  };
});
vi.mock('@/lib/queries/staff-events', () => ({
  useWeekStaffEvents: () => ({ data: [], isLoading: false }),
  buildStaffEventsMap: () => new Map(),
  useUpdateEventForDrag: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/opLog', () => ({
  useOpLogState: () => ({ data: undefined, isLoading: false }),
  useUndoOpLog: () => ({ mutateAsync: vi.fn().mockResolvedValue(undefined), isPending: false }),
  useRedoOpLog: () => ({ mutateAsync: vi.fn().mockResolvedValue(undefined), isPending: false }),
  useInvalidateOpLog: () => vi.fn(),
  OP_LOG_STATE_KEY: 'op-log-state',
}));
vi.mock('@/lib/queries/visitMoveWeekOnly', () => ({
  useVisitMoveWeekOnly: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/visitAssignStaffWeek', () => ({
  useVisitAssignStaffWeek: () => ({ mutateAsync: mockAssignStaffWeek, isPending: false }),
}));
vi.mock('@/lib/queries/schedulingSettings', () => ({
  useSchedulingSettings: () => ({ data: undefined, isLoading: false }),
  useUpdateSchedulingSettings: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/api/patientSync', () => {
  const noop = () => ({
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    error: null,
    isSuccess: false,
  });
  return {
    useBulkSyncWeekToFixedMutation: noop,
    useBulkApplyWeekOnlyVisitChangesMutation: noop,
    useSyncWeekVisitsToFixedMutation: noop,
  };
});
vi.mock('@/lib/queries/staff-overrides', () => ({
  useWeekStaffOverrides: () => ({ data: [], isLoading: false }),
}));
// 運転席の新 API (契約書 §2)。代替候補は 1 グループ (コース無し = 訪問1件) を返す。
vi.mock('@/lib/queries/cockpit', () => ({
  useVisitCancelWeek: () => ({ mutateAsync: mockVisitCancelWeek, isPending: false }),
  useVisitServiceOverride: () => ({ mutateAsync: mockVisitServiceOverride, isPending: false }),
  useEventCancelWeek: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useStaffOffWeek: () => ({ mutateAsync: mockStaffOffWeek, isPending: false }),
  useUnsentSummary: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useReverseSheet: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSubstituteCandidates: () => ({
    data: {
      absent_staff: { id: STAFF_1, name: '佐藤' },
      date: MONDAY_ISO,
      weekday: 0,
      groups: [
        {
          course_id: null,
          course_label: '臨時',
          visits: [
            {
              visit_id: 'v1',
              patient_id: 'p1',
              patient_name: '田中',
              start_time: '09:00',
              end_time: '10:00',
              week_pinned: false,
              status: 'planned',
            },
          ],
          candidates: [
            {
              staff_id: STAFF_2,
              name: '鈴木',
              sex: 'female',
              office_name: '本店',
              status: 'ok',
              reasons: [],
              score: 1.5,
              load_today: 2,
            },
          ],
        },
      ],
      warnings: [],
    },
    isPending: false,
    isError: false,
    fetchStatus: 'idle',
  }),
  // 「担当なし」からの投入提案 (Phase 2-B/2-C)。既定は「鈴木さんが丸ごと ◎」。
  ASSIGN_CANDIDATES_KEY: 'assign-candidates',
  useAssignCandidates: (...args: unknown[]) => mockUseAssignCandidates(...args),
  useAssignCandidatesFetcher: () => mockFetchAssignCandidates,
}));

// ─── Subject under test ─────────────────────────────────────────────────────

import { ApiError } from '@/lib/api-client';

import { CourseDayTablePanel } from '../CourseDayTablePanel';

// ─── fixtures ───────────────────────────────────────────────────────────────

const STAFF_1 = '00000000-0000-4000-8000-000000000001';
const STAFF_2 = '00000000-0000-4000-8000-000000000002';

/** 過去日ガードに掛からないよう、対象週は常に「来週」を使う。 */
function nextMonday(): Date {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() + ((8 - d.getDay()) % 7 || 7));
  return d;
}
const WEEK_START = nextMonday();
const MONDAY_ISO = `${WEEK_START.getFullYear()}-${String(WEEK_START.getMonth() + 1).padStart(2, '0')}-${String(WEEK_START.getDate()).padStart(2, '0')}`;

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

function setupHooks(opts: { visits?: Array<Record<string, unknown>>; keepStaff?: boolean } = {}) {
  mockOffices.mockReturnValue({
    allOffices: [{ id: 'office-honten', name: '本店' }],
    isLoading: false,
  });
  mockPatients.mockReturnValue({
    data: {
      items: [{ id: 'p1', name: '田中', status: 'active', sex: 'female', weekly_pattern: null }],
    },
    isLoading: false,
  });
  if (!opts.keepStaff)
    mockStaffList.mockReturnValue({
      data: [
        {
          id: STAFF_1,
          name: '佐藤',
          status: 'active',
          primary_office_id: 'office-honten',
          is_trainee: false,
        },
        {
          id: STAFF_2,
          name: '鈴木',
          status: 'active',
          primary_office_id: 'office-honten',
          is_trainee: false,
        },
      ],
      isLoading: false,
    });
  mockUseQueries.mockReturnValue([
    {
      data: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      isLoading: false,
    },
  ]);
  mockCourses.mockReturnValue({
    data: [
      {
        id: 'course-A-mon',
        office_id: 'office-honten',
        code: 'A',
        weekday: 0,
        assigned_staff_id: STAFF_1,
        deleted_at: null,
      },
    ],
    isLoading: false,
  });
  mockVisits.mockReturnValue({
    data: {
      items: opts.visits ?? [
        {
          id: 'v1',
          patient_id: 'p1',
          patient_name: '田中',
          course_id: 'course-A-mon',
          visit_date: MONDAY_ISO,
          start_time: '09:00:00',
          end_time: '10:00:00',
          primary_staff_id: STAFF_1,
          status: 'planned',
          source: 'auto',
        },
      ],
      truncated: false,
    },
    isLoading: false,
  });
}

/** BE の 422 `constraint_confirmation_required` (NG スタッフ / 性別制限)。 */
function constraintError(): ApiError {
  return new ApiError('Unprocessable Entity', 422, {
    detail: {
      code: 'constraint_confirmation_required',
      warnings: [{ kind: 'ng_staff', patient_id: 'p1', patient_name: '田中', staff_id: STAFF_2 }],
    },
  });
}

/** 入れ替えテスト用の訪問 (月曜・同じコース・担当だけ違う)。 */
function swapVisit(
  id: string,
  staffId: string,
  start: string,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    id,
    patient_id: 'p1',
    patient_name: '田中',
    course_id: 'course-A-mon',
    visit_date: MONDAY_ISO,
    start_time: start,
    end_time: start,
    primary_staff_id: staffId,
    status: 'planned',
    source: 'auto',
    ...extra,
  };
}

/**
 * 佐藤 1 件 / 鈴木 1 件。鈴木側は `manual_staff_override=true`
 * (= コース伝播では動かない訪問) にして、訪問単位で動くことを実証する。
 */
const SWAP_VISITS = [
  swapVisit('v1', STAFF_1, '09:00:00'),
  swapVisit('v2', STAFF_2, '11:00:00', { manual_staff_override: true, source: 'kaipoke' }),
];

function renderStaffTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <CourseDayTablePanel weekStart={WEEK_START} officeId={null} canEdit={true} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByTestId('course-day-tab-staff'));
  /** 週セレクタを動かす代わりに weekStart prop を差し替える (H1 の検証用)。 */
  const setWeek = (weekStart: Date) =>
    view.rerender(
      <QueryClientProvider client={qc}>
        <CourseDayTablePanel weekStart={weekStart} officeId={null} canEdit={true} />
      </QueryClientProvider>,
    );
  return Object.assign(view, { qc, setWeek });
}

/** jsdom は DataTransfer 非実装なので、必要な API だけの替え玉を作る。 */
function makeDataTransfer() {
  const store = new Map<string, string>();
  return {
    effectAllowed: 'none',
    dropEffect: 'none',
    setData: (type: string, value: string) => void store.set(type, value),
    getData: (type: string) => store.get(type) ?? '',
  };
}

/**
 * 「担当なし」に 2 件ある月曜のコース A (Phase 2-B のテスト用)。
 * コース行の担当も外し、訪問の primary_staff_id も null にする。
 */
function setupUnassignedHooks(mondayIso: string = MONDAY_ISO) {
  setupHooks({
    visits: [
      {
        id: 'v1',
        patient_id: 'p1',
        patient_name: '田中',
        course_id: 'course-A-mon',
        visit_date: mondayIso,
        start_time: '09:30:00',
        end_time: '10:05:00',
        primary_staff_id: null,
        status: 'planned',
        source: 'auto',
      },
      {
        id: 'v2',
        patient_id: 'p1',
        patient_name: '田中',
        course_id: 'course-A-mon',
        visit_date: mondayIso,
        start_time: '11:30:00',
        end_time: '12:05:00',
        primary_staff_id: null,
        status: 'planned',
        source: 'auto',
      },
    ],
  });
  mockCourses.mockReturnValue({
    data: [
      {
        id: 'course-A-mon',
        office_id: 'office-honten',
        code: 'A',
        weekday: 0,
        assigned_staff_id: null,
        deleted_at: null,
      },
    ],
    isLoading: false,
  });
  // 鈴木さんだけが「コース丸ごと ◎」。
  ASSIGN_CANDIDATES_RESULT.whole_ok_staff_ids = [STAFF_2];
  ASSIGN_CANDIDATES_RESULT.whole_ok_by_course = { 'course-A-mon': [STAFF_2] };
  ASSIGN_CANDIDATES_RESULT.groups = [
    {
      course_id: 'course-A-mon',
      course_label: '本店 A',
      visits: [],
      candidates: [
        {
          staff_id: STAFF_2,
          name: '鈴木',
          sex: 'female',
          office_name: '本店',
          status: 'ok',
          reasons: [],
          score: 2,
          load_today: 1,
        },
      ],
    },
  ];
}

/** タイムラインへ切り替え、佐藤 ⠿ → 鈴木 の入れ替え確認ダイアログを開く。 */
async function openSwapDialog() {
  fireEvent.click(screen.getByTestId('staff-tab-mode-timeline'));
  const dt = makeDataTransfer();
  fireEvent.dragStart(screen.getByTestId(`tl-swap-grip-${STAFF_1}`), { dataTransfer: dt });
  fireEvent.drop(screen.getByTestId(`tl-name-${STAFF_2}`), { dataTransfer: dt });
  return screen.findByTestId('cockpit-staff-swap-dialog');
}

// ─── Tests ──────────────────────────────────────────────────────────────────

describe('CourseDayTablePanel — 職員スケジュールタブ (運転席・FE-C 結線)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    callOrder.length = 0;
    invalidatedKeys.length = 0;
    mockVisitCancelWeek.mockResolvedValue({ id: 'v1', status: 'cancelled' });
    mockAssignStaffWeek.mockImplementation(async () => {
      callOrder.push('assign');
      return { changed: true };
    });
    mockStaffOffWeek.mockImplementation(async (payload: { to_staff_id: unknown }) => {
      callOrder.push('staff-off-week');
      return {
        override_id: '00000000-0000-4000-8000-0000000000aa',
        moved_visit_ids: ['v1'],
        moved_course_ids: [],
        skipped_visit_ids: [],
        to_staff_id: payload.to_staff_id ?? null,
        op_group_id: '00000000-0000-4000-8000-0000000000bb',
      };
    });
    mockUpdateCourse.mockImplementation(async () => {
      callOrder.push('update-course');
      return { id: 'course-A-mon' };
    });
    ASSIGN_CANDIDATES_RESULT.groups = [];
    ASSIGN_CANDIDATES_RESULT.whole_ok_staff_ids = [];
    ASSIGN_CANDIDATES_RESULT.whole_ok_by_course = {};
    mockFetchAssignCandidates.mockImplementation(async () => ASSIGN_CANDIDATES_RESULT);
    mockUseAssignCandidates.mockImplementation(() => ({
      data: ASSIGN_CANDIDATES_RESULT,
      isPending: false,
      isError: false,
      error: null,
      fetchStatus: 'idle',
      refetch: vi.fn(),
    }));
  });

  it('(a) 訪問行クリックでメニューが開き「今週だけ取消」が visit-cancel-week を呼ぶ', async () => {
    setupHooks();
    renderStaffTab();

    fireEvent.click(screen.getByTestId('staff-week-visit-v1'));
    expect(await screen.findByTestId('visit-action-menu')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('visit-action-cancel'));
    expect(mockVisitCancelWeek).toHaveBeenCalledTimes(1);
    expect(mockVisitCancelWeek.mock.calls[0]?.[0]).toMatchObject({
      visit_id: 'v1',
      cancel: true,
    });
  });

  it('(b) 「🛌 休みにする」→ 休みと付け替えは staff-off-week 1 回で終わる', async () => {
    setupHooks();
    renderStaffTab();

    fireEvent.click(screen.getByTestId(`staff-week-off-action-${STAFF_1}-0`));
    expect(await screen.findByTestId('substitute-panel')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId(`substitute-cand-${STAFF_2}`));

    // 旧実装の 2 段階 (override → assign を訪問ごと) は廃止。1 リクエストのみ。
    await vi.waitFor(() => expect(callOrder).toEqual(['staff-off-week']));
    expect(mockStaffOffWeek).toHaveBeenCalledTimes(1);
    expect(mockStaffOffWeek.mock.calls[0]?.[0]).toMatchObject({
      staff_id: STAFF_1,
      date: MONDAY_ISO,
      to_staff_id: STAFF_2,
    });
    expect(mockAssignStaffWeek).not.toHaveBeenCalled();
  });

  it('(b2) 休みの成功で週の休み一覧 (staff-overrides-week) が失効する', async () => {
    setupHooks();
    renderStaffTab();

    fireEvent.click(screen.getByTestId(`staff-week-off-action-${STAFF_1}-0`));
    expect(await screen.findByTestId('substitute-panel')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('substitute-unassign'));

    await vi.waitFor(() => expect(mockStaffOffWeek).toHaveBeenCalled());
    expect(mockStaffOffWeek.mock.calls[0]?.[0]).toMatchObject({ to_staff_id: null });
    // 盤面の網掛け (週一括キー) が古いまま残る回帰を防ぐ。
    await vi.waitFor(() =>
      expect(invalidatedKeys.some((k) => Array.isArray(k) && k[0] === 'staff-overrides-week')).toBe(
        true,
      ),
    );
  });

  it('(b3) 休みの成功トーストは 件数 + 「戻るで復元」を伝える', async () => {
    setupHooks();
    renderStaffTab();

    fireEvent.click(screen.getByTestId(`staff-week-off-action-${STAFF_1}-0`));
    expect(await screen.findByTestId('substitute-panel')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('substitute-unassign'));

    await vi.waitFor(() => expect(mockToast.success).toHaveBeenCalled());
    const msg = String(mockToast.success.mock.calls[0]?.[0]);
    expect(msg).toContain('休みに');
    expect(msg).toContain('予定 1件');
    expect(msg).toContain('戻るで復元');
    // 実行後はモーダルを閉じる。
    await vi.waitFor(() => expect(screen.queryByTestId('substitute-panel')).toBeNull());
  });

  it('(b4) 据え置き (打刻済み・完了) があればトーストで件数を伝える', async () => {
    setupHooks();
    mockStaffOffWeek.mockImplementation(async (payload: { to_staff_id: unknown }) => {
      callOrder.push('staff-off-week');
      return {
        override_id: '00000000-0000-4000-8000-0000000000aa',
        moved_visit_ids: ['v1'],
        moved_course_ids: [],
        skipped_visit_ids: ['v9', 'v10'],
        to_staff_id: payload.to_staff_id ?? null,
        op_group_id: '00000000-0000-4000-8000-0000000000bb',
      };
    });
    renderStaffTab();

    fireEvent.click(screen.getByTestId(`staff-week-off-action-${STAFF_1}-0`));
    expect(await screen.findByTestId('substitute-panel')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('substitute-unassign'));

    await vi.waitFor(() => expect(mockToast.success).toHaveBeenCalled());
    expect(String(mockToast.success.mock.calls[0]?.[0])).toContain(
      '2件は打刻済み・完了のため据え置き',
    );
  });

  it('(b5) NG/性別 422 → 確認 → ack 付きで再送 (同一 op_group_id)', async () => {
    setupHooks();
    mockStaffOffWeek.mockImplementation(async (payload: Record<string, unknown>) => {
      callOrder.push('staff-off-week');
      if (payload.acknowledge_constraint_warnings !== true) throw constraintError();
      return {
        override_id: '00000000-0000-4000-8000-0000000000aa',
        moved_visit_ids: ['v1'],
        moved_course_ids: [],
        skipped_visit_ids: [],
        to_staff_id: payload.to_staff_id ?? null,
        op_group_id: '00000000-0000-4000-8000-0000000000bb',
      };
    });
    renderStaffTab();

    fireEvent.click(screen.getByTestId(`staff-week-off-action-${STAFF_1}-0`));
    expect(await screen.findByTestId('substitute-panel')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId(`substitute-cand-${STAFF_2}`));

    // 422 は確認ダイアログへ (エラートーストにしない)。
    expect(await screen.findByTestId('constraint-override-confirm')).toBeInTheDocument();
    expect(mockToast.error).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('constraint-override-ok'));

    await vi.waitFor(() => expect(mockStaffOffWeek).toHaveBeenCalledTimes(2));
    const calls = mockStaffOffWeek.mock.calls.map((c) => c[0] as Record<string, unknown>);
    expect(calls[1]).toMatchObject({ acknowledge_constraint_warnings: true });
    // 確認をまたいでも op_group_id は同じ (「戻る」1 回で戻せる 1 手)。
    expect(new Set(calls.map((c) => c.op_group_id)).size).toBe(1);
    await vi.waitFor(() => expect(mockToast.success).toHaveBeenCalled());
  });

  it('(c) 表示切替 [リスト|タイムライン] が効く', () => {
    setupHooks();
    renderStaffTab();

    expect(screen.getByTestId('staff-week-board')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('staff-tab-mode-timeline'));
    expect(screen.getByTestId('staff-timeline-view')).toBeInTheDocument();
    expect(screen.queryByTestId('staff-week-board')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('staff-tab-mode-list'));
    expect(screen.getByTestId('staff-week-board')).toBeInTheDocument();
  });

  it('(d) 同期バーで選んだ訪問差分が盤面ゴースト (今ここ / こう変わる) になる', () => {
    setupHooks();
    renderStaffTab();

    expect(screen.queryByTestId('reconcile-ghost-diff-1-before')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('stub-select-diff'));

    const before = screen.getByTestId('reconcile-ghost-diff-1-before');
    const after = screen.getByTestId('reconcile-ghost-diff-1-after');
    expect(before).toHaveTextContent('今ここ');
    expect(before).toHaveTextContent('09:00');
    expect(after).toHaveTextContent('こう変わる');
    expect(after).toHaveTextContent('10:30');
  });

  it('(e) 同期バーの報告で ●未送信ドットが出る / ゼロ報告で消える', () => {
    setupHooks();
    renderStaffTab();

    // まだ数えていない間はドットを出さない (「送信済み」と断定しない)。
    expect(screen.queryByTestId('staff-week-visit-unsent-v1')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('stub-report-unsent'));
    expect(screen.getByTestId('staff-week-visit-unsent-v1')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('stub-report-unsent-empty'));
    expect(screen.queryByTestId('staff-week-visit-unsent-v1')).not.toBeInTheDocument();
  });

  it('(g) スタッフ入れ替え: 常に visit-assign を訪問単位で呼ぶ (PATCH /courses は使わない)', async () => {
    setupHooks({ visits: SWAP_VISITS });
    renderStaffTab();
    await openSwapDialog();

    expect(screen.getByTestId('cockpit-staff-swap-summary')).toHaveTextContent('佐藤');
    fireEvent.click(screen.getByTestId('cockpit-staff-swap-confirm'));

    await vi.waitFor(() => expect(mockAssignStaffWeek).toHaveBeenCalledTimes(2));
    // コース経路 (op_log inverse が primary を戻さない / manual_staff_override を
    // 動かせない / 取消・実施済みも巻き込む) は使わない。
    expect(mockUpdateCourse).not.toHaveBeenCalled();

    const calls = mockAssignStaffWeek.mock.calls.map((c) => c[0] as Record<string, unknown>);
    // 佐藤の v1 → 鈴木 / 鈴木の v2 (manual_staff_override) → 佐藤
    expect(calls).toEqual([
      expect.objectContaining({ visit_id: 'v1', staff_id: STAFF_2 }),
      expect.objectContaining({ visit_id: 'v2', staff_id: STAFF_1 }),
    ]);
    // 「戻る」1 回でまとめて戻せるよう、双方向とも同じ op_group_id
    expect(calls[0]?.op_group_id).toBe(calls[1]?.op_group_id);
    expect(typeof calls[0]?.op_group_id).toBe('string');
  });

  it('(g2) スタッフ入れ替え: 取消済み・実施済みの訪問は動かさず注記する', async () => {
    setupHooks({
      visits: [
        ...SWAP_VISITS,
        swapVisit('v3', STAFF_1, '13:00:00', { status: 'cancelled' }),
        swapVisit('v4', STAFF_2, '14:00:00', { status: 'completed' }),
      ],
    });
    renderStaffTab();
    await openSwapDialog();

    expect(screen.getByTestId('cockpit-staff-swap-summary')).toHaveTextContent(
      '取消済み・実施済みの訪問 2 件は入れ替えません',
    );
    fireEvent.click(screen.getByTestId('cockpit-staff-swap-confirm'));

    await vi.waitFor(() => expect(mockAssignStaffWeek).toHaveBeenCalledTimes(2));
    const ids = mockAssignStaffWeek.mock.calls.map((c) => (c[0] as { visit_id: string }).visit_id);
    expect(ids).toEqual(['v1', 'v2']);
  });

  it('(g3) スタッフ入れ替え: 青ピン / 新人 / 当日以前 は実行できない', async () => {
    setupHooks({ visits: [swapVisit('v1', STAFF_1, '09:00:00', { week_pinned: true })] });
    renderStaffTab();
    await openSwapDialog();

    expect(screen.getByTestId('cockpit-staff-swap-summary')).toHaveTextContent('青ピン');
    expect(screen.getByTestId('cockpit-staff-swap-confirm')).toBeDisabled();
  });

  it('(g4) スタッフ入れ替え: 新人は入れ替え相手にできない (⠿ を出さない)', () => {
    mockStaffList.mockReturnValue({
      data: [
        {
          id: STAFF_1,
          name: '佐藤',
          status: 'active',
          primary_office_id: 'office-honten',
          is_trainee: false,
        },
        {
          id: STAFF_2,
          name: '鈴木',
          status: 'active',
          primary_office_id: 'office-honten',
          is_trainee: true,
        },
      ],
      isLoading: false,
    });
    setupHooks({ visits: SWAP_VISITS, keepStaff: true });
    renderStaffTab();
    fireEvent.click(screen.getByTestId('staff-tab-mode-timeline'));

    expect(screen.getByTestId(`tl-swap-grip-${STAFF_1}`)).toBeInTheDocument();
    expect(screen.queryByTestId(`tl-swap-grip-${STAFF_2}`)).not.toBeInTheDocument();
  });

  it('(g5) スタッフ入れ替え: 両者 0 件なら API を呼ばず toast.info で終わる', async () => {
    setupHooks({ visits: [] });
    renderStaffTab();
    await openSwapDialog();

    fireEvent.click(screen.getByTestId('cockpit-staff-swap-confirm'));

    await vi.waitFor(() => expect(mockToast.info).toHaveBeenCalled());
    expect(mockAssignStaffWeek).not.toHaveBeenCalled();
    expect(mockToast.info.mock.calls[0]?.[0]).toContain('入れ替えられる予定がありません');
    // 何も適用していないので「元に戻す」は案内しない
    expect(mockToast.success).not.toHaveBeenCalled();
  });

  it('(g6) スタッフ入れ替え: 途中で失敗したら toast.error で「戻る」を案内する', async () => {
    setupHooks({ visits: SWAP_VISITS });
    let n = 0;
    mockAssignStaffWeek.mockImplementation(async () => {
      n += 1;
      if (n === 2) throw new Error('boom');
      return { changed: true };
    });
    renderStaffTab();
    await openSwapDialog();

    fireEvent.click(screen.getByTestId('cockpit-staff-swap-confirm'));

    await vi.waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    const msgs = mockToast.error.mock.calls.map((c) => String(c[0]));
    // 失敗は件数で集約し、適用済みがあるので「戻る」を案内する
    expect(msgs.some((m) => m.includes('1 件失敗しました') && m.includes('「戻る」'))).toBe(true);
    expect(mockToast.error).toHaveBeenCalledTimes(1);
    expect(mockToast.success).not.toHaveBeenCalled();
  });

  it('(g7) スタッフ入れ替え: 422 (NG/性別) は残りを中断 → 確認 → ack 付きで再開する', async () => {
    setupHooks({ visits: SWAP_VISITS });
    let n = 0;
    mockAssignStaffWeek.mockImplementation(async (payload: Record<string, unknown>) => {
      n += 1;
      // 1 件目だけ 422。ack 付きの再送 (2 回目) は通す。
      if (n === 1 && payload.acknowledge_constraint_warnings !== true) throw constraintError();
      return { changed: true };
    });
    renderStaffTab();
    await openSwapDialog();
    fireEvent.click(screen.getByTestId('cockpit-staff-swap-confirm'));

    // 1 件目で止まる = 2 件目 (v2) はまだ呼ばれていない
    expect(await screen.findByTestId('constraint-override-confirm')).toBeInTheDocument();
    expect(mockAssignStaffWeek).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId('constraint-override-ok'));

    // 再送 (ack 付き) → 続きの v2 まで流れる
    await vi.waitFor(() => expect(mockAssignStaffWeek).toHaveBeenCalledTimes(3));
    const calls = mockAssignStaffWeek.mock.calls.map((c) => c[0] as Record<string, unknown>);
    expect(calls[1]).toMatchObject({ visit_id: 'v1', acknowledge_constraint_warnings: true });
    expect(calls[2]).toMatchObject({ visit_id: 'v2', staff_id: STAFF_1 });
    // 中断・再開をまたいでも op_group_id は同じ (「戻る」1 回で戻せる)
    expect(new Set(calls.map((c) => c.op_group_id)).size).toBe(1);
    await vi.waitFor(() => expect(mockToast.success).toHaveBeenCalled());
  });

  it('(g8) スタッフ入れ替え: 2 連続 422 → 2 回確認 → 全件適用 (同一 op_group_id)', async () => {
    setupHooks({ visits: SWAP_VISITS });
    // ack が無い限り毎回 422 を返す (= 2 件とも確認が要る)。
    mockAssignStaffWeek.mockImplementation(async (payload: Record<string, unknown>) => {
      if (payload.acknowledge_constraint_warnings !== true) throw constraintError();
      return { changed: true };
    });
    renderStaffTab();
    await openSwapDialog();
    fireEvent.click(screen.getByTestId('cockpit-staff-swap-confirm'));

    // 1 件目の確認
    expect(await screen.findByTestId('constraint-override-confirm')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('constraint-override-ok'));

    // 2 件目の確認が**消えずに**出る (retry 中に積まれた pending を潰さない回帰)
    await vi.waitFor(() => expect(mockAssignStaffWeek).toHaveBeenCalledTimes(3));
    expect(await screen.findByTestId('constraint-override-confirm')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('constraint-override-ok'));

    await vi.waitFor(() => expect(mockAssignStaffWeek).toHaveBeenCalledTimes(4));
    const calls = mockAssignStaffWeek.mock.calls.map((c) => c[0] as Record<string, unknown>);
    expect(calls.map((c) => c.visit_id)).toEqual(['v1', 'v1', 'v2', 'v2']);
    expect(calls[1]).toMatchObject({ acknowledge_constraint_warnings: true, staff_id: STAFF_2 });
    expect(calls[3]).toMatchObject({ acknowledge_constraint_warnings: true, staff_id: STAFF_1 });
    // 中断・再開を 2 回はさんでも 1 操作グループ
    expect(new Set(calls.map((c) => c.op_group_id)).size).toBe(1);
    await vi.waitFor(() => expect(mockToast.success).toHaveBeenCalled());
  });

  it('(g9) スタッフ入れ替え: 確認を「やめる」と中止トースト + 残りは送らない', async () => {
    setupHooks({ visits: SWAP_VISITS });
    mockAssignStaffWeek.mockImplementation(async (payload: Record<string, unknown>) => {
      if (payload.acknowledge_constraint_warnings !== true) throw constraintError();
      return { changed: true };
    });
    renderStaffTab();
    await openSwapDialog();
    fireEvent.click(screen.getByTestId('cockpit-staff-swap-confirm'));

    expect(await screen.findByTestId('constraint-override-confirm')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('constraint-override-cancel'));

    await vi.waitFor(() => expect(mockToast.warning).toHaveBeenCalled());
    expect(String(mockToast.warning.mock.calls[0]?.[0])).toContain('入れ替えを中止しました');
    // 2 件目 (v2) は送らない
    expect(mockAssignStaffWeek).toHaveBeenCalledTimes(1);
    expect(mockToast.success).not.toHaveBeenCalled();
    // 実行が決着したので入れ替えダイアログも閉じる
    await vi.waitFor(() =>
      expect(screen.queryByTestId('cockpit-staff-swap-dialog')).not.toBeInTheDocument(),
    );
  });

  it('(g10) スタッフ入れ替え: 実行中の二度押しでは二重に流れない', async () => {
    setupHooks({ visits: SWAP_VISITS });
    mockAssignStaffWeek.mockImplementation(async () => {
      await new Promise((r) => setTimeout(r, 20));
      return { changed: true };
    });
    renderStaffTab();
    await openSwapDialog();

    const confirm = screen.getByTestId('cockpit-staff-swap-confirm');
    fireEvent.click(confirm);
    fireEvent.click(confirm); // 実行中の二度押し
    fireEvent.click(confirm);

    await vi.waitFor(() => expect(mockToast.success).toHaveBeenCalledTimes(1));
    // キューは 2 件。二重起動していれば 4 件・6 件になる。
    expect(mockAssignStaffWeek).toHaveBeenCalledTimes(2);
  });

  it('(g11) 休みが失敗したら toast.error のみ (成功文言は出さない)', async () => {
    setupHooks();
    mockStaffOffWeek.mockImplementation(async () => {
      callOrder.push('staff-off-week');
      throw new Error('boom');
    });
    renderStaffTab();

    fireEvent.click(screen.getByTestId(`staff-week-off-action-${STAFF_1}-0`));
    expect(await screen.findByTestId('substitute-panel')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId(`substitute-cand-${STAFF_2}`));

    await vi.waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(String(mockToast.error.mock.calls[0]?.[0])).toContain('休みにできませんでした');
    // BE が 1 トランザクションで弾いた = 何も書かれていない。成功文言は出さない。
    const successMsgs = mockToast.success.mock.calls.map((c) => String(c[0]));
    expect(successMsgs.some((m) => m.includes('休みに'))).toBe(false);
  });

  it('(h) タイムラインの行アクション「🛌 休みにする」で SubstitutePanel が開く', async () => {
    setupHooks();
    renderStaffTab();
    fireEvent.click(screen.getByTestId('staff-tab-mode-timeline'));

    expect(screen.queryByTestId('substitute-panel')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId(`tl-off-action-${STAFF_1}`));
    expect(await screen.findByTestId('substitute-panel')).toBeInTheDocument();
  });

  it('(h2) タイムラインの「＋訪問」で今週だけの訪問ダイアログが開く', async () => {
    setupHooks();
    renderStaffTab();
    fireEvent.click(screen.getByTestId('staff-tab-mode-timeline'));

    fireEvent.click(screen.getByTestId(`tl-add-visit-${STAFF_2}`));
    expect(await screen.findByTestId('add-visit-dialog')).toBeInTheDocument();
  });

  it('(f) 訪問メニューの同期表示は 未計測=「同期バーで確認」/ 報告後=「●未送信」', async () => {
    setupHooks();
    renderStaffTab();

    fireEvent.click(screen.getByTestId('staff-week-visit-v1'));
    expect(await screen.findByTestId('visit-action-footer')).toHaveTextContent('同期バーで確認');

    // メニューを閉じてから未送信を報告し、開き直す。
    fireEvent.keyDown(document.body, { key: 'Escape' });
    fireEvent.click(screen.getByTestId('stub-report-unsent'));
    fireEvent.click(screen.getByTestId('staff-week-visit-v1'));
    expect(await screen.findByTestId('visit-action-footer')).toHaveTextContent('●未送信');
  });

  // ─── 「担当なし」からの投入提案 (Phase 2-B) ────────────────────────────

  it('(i) 「◎ 提案を見る」→ その日ぶんを course_ids 1 回で調べてバッジにする', async () => {
    setupUnassignedHooks();
    renderStaffTab();

    // 押すまでは調べない (自動計算はしない = プールの「効果を表示」と同じ)。
    expect(mockFetchAssignCandidates).not.toHaveBeenCalled();
    expect(screen.getByTestId('staff-week-suggest-tpl-A-0')).toHaveTextContent('提案を見る');

    fireEvent.click(screen.getByTestId('staff-tab-see-suggestions'));

    await vi.waitFor(() =>
      expect(screen.getByTestId('staff-week-suggest-tpl-A-0')).toHaveTextContent('◎ 1名 引受可'),
    );
    // BE 契約 2026-08-23: コース数分のファンアウトはしない (曜日ごとに 1 回)。
    expect(mockFetchAssignCandidates).toHaveBeenCalledTimes(1);
    const req = mockFetchAssignCandidates.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(req).toMatchObject({ date: MONDAY_ISO, course_ids: ['course-A-mon'] });
    // 排他: course_id / visit_ids は載せない。
    expect(req.course_id).toBeUndefined();
    expect(req.visit_ids).toBeUndefined();
  });

  it('(i2) バッジ → 提案 → [このコースを割り当てる] = visit-assign N 回 + PATCH /courses 1 回 (同一 op_group)', async () => {
    setupUnassignedHooks();
    renderStaffTab();

    fireEvent.click(screen.getByTestId('staff-week-suggest-tpl-A-0'));
    expect(await screen.findByTestId('assign-suggestion-popover')).toBeInTheDocument();
    expect(screen.getByTestId('assign-suggestion-sub')).toHaveTextContent('2件・09:30〜12:05');

    fireEvent.click(screen.getByTestId(`assign-suggestion-apply-${STAFF_2}`));

    await vi.waitFor(() => expect(mockUpdateCourse).toHaveBeenCalledTimes(1));
    // 訪問ごとの付け替えが**先**・コース担当は後 (表示の正典を最後に合わせる)。
    expect(callOrder).toEqual(['assign', 'assign', 'update-course']);

    const assignCalls = mockAssignStaffWeek.mock.calls.map((c) => c[0] as Record<string, unknown>);
    expect(assignCalls.map((c) => c.visit_id)).toEqual(['v1', 'v2']);
    expect(assignCalls.every((c) => c.staff_id === STAFF_2)).toBe(true);
    const opGroupId = assignCalls[0]?.op_group_id;
    expect(typeof opGroupId).toBe('string');
    expect(assignCalls[1]?.op_group_id).toBe(opGroupId);
    // 「戻る」1 回で訪問もコースもまとめて戻る。
    expect(mockUpdateCourse.mock.calls[0]?.[0]).toMatchObject({
      id: 'course-A-mon',
      patch: { assigned_staff_id: STAFF_2, op_group_id: opGroupId },
    });

    await vi.waitFor(() => expect(mockToast.success).toHaveBeenCalled());
    const msg = String(mockToast.success.mock.calls[0]?.[0]);
    expect(msg).toContain('2件');
    expect(msg).toContain('鈴木さんへ');
    expect(msg).toContain('今週だけ・戻るで復元');
    // 他コースの候補が変わるのでポップオーバーは閉じ、バッジも捨てる。
    await vi.waitFor(() =>
      expect(screen.queryByTestId('assign-suggestion-popover')).not.toBeInTheDocument(),
    );
  });

  it('(i3) 「1件ずつ分けて入れる」= 提案を閉じて最初の訪問のメニューを開く', async () => {
    setupUnassignedHooks();
    renderStaffTab();

    fireEvent.click(screen.getByTestId('staff-week-suggest-tpl-A-0'));
    expect(await screen.findByTestId('assign-suggestion-popover')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('assign-suggestion-split'));

    expect(screen.queryByTestId('assign-suggestion-popover')).not.toBeInTheDocument();
    expect(await screen.findByTestId('staff-timeline-visit-menu')).toHaveTextContent('選択中');
    expect(mockAssignStaffWeek).not.toHaveBeenCalled();
  });

  it('(i4) 2 件目が失敗したらコース担当 (PATCH /courses) は当てない', async () => {
    setupUnassignedHooks();
    mockAssignStaffWeek.mockImplementation(async (payload: { visit_id: string }) => {
      callOrder.push('assign');
      if (payload.visit_id === 'v2') throw new Error('boom');
      return { changed: true };
    });
    renderStaffTab();

    fireEvent.click(screen.getByTestId('staff-week-suggest-tpl-A-0'));
    expect(await screen.findByTestId('assign-suggestion-popover')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId(`assign-suggestion-apply-${STAFF_2}`));

    await vi.waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    // 片側だけ動いた盤面 (訪問1件だけ移ってコース担当は全部移った) を作らない。
    expect(mockUpdateCourse).not.toHaveBeenCalled();
    expect(callOrder).toEqual(['assign', 'assign']);
    expect(String(mockToast.error.mock.calls[0]?.[0])).toContain('1件失敗');
    // 途中まで動いているので「元に戻す」を渡す。
    expect(mockToast.error.mock.calls[0]?.[1]).toMatchObject({ cancel: { label: '元に戻す' } });
  });

  it('(i5) 422 → 「やめる」= PATCH なし・中止トーストに「元に戻す」が付く', async () => {
    setupUnassignedHooks();
    mockAssignStaffWeek.mockImplementation(
      async (payload: { visit_id: string; acknowledge_constraint_warnings?: boolean }) => {
        callOrder.push('assign');
        if (payload.visit_id === 'v2' && payload.acknowledge_constraint_warnings !== true) {
          throw constraintError();
        }
        return { changed: true };
      },
    );
    renderStaffTab();

    fireEvent.click(screen.getByTestId('staff-week-suggest-tpl-A-0'));
    expect(await screen.findByTestId('assign-suggestion-popover')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId(`assign-suggestion-apply-${STAFF_2}`));

    expect(await screen.findByTestId('constraint-override-confirm')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('constraint-override-cancel'));

    await vi.waitFor(() => expect(mockToast.warning).toHaveBeenCalled());
    expect(mockUpdateCourse).not.toHaveBeenCalled();
    const [msg, opts] = mockToast.warning.mock.calls[0] ?? [];
    expect(String(msg)).toContain('中止');
    expect(String(msg)).toContain('1件は反映済み');
    expect(opts).toMatchObject({ cancel: { label: '元に戻す' } });
  });

  it('(i6) 422 → 「続ける」= 最後に PATCH /courses が 1 回だけ (ack 引き継ぎ)', async () => {
    setupUnassignedHooks();
    mockAssignStaffWeek.mockImplementation(
      async (payload: { visit_id: string; acknowledge_constraint_warnings?: boolean }) => {
        callOrder.push('assign');
        if (payload.visit_id === 'v2' && payload.acknowledge_constraint_warnings !== true) {
          throw constraintError();
        }
        return { changed: true };
      },
    );
    renderStaffTab();

    fireEvent.click(screen.getByTestId('staff-week-suggest-tpl-A-0'));
    expect(await screen.findByTestId('assign-suggestion-popover')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId(`assign-suggestion-apply-${STAFF_2}`));

    expect(await screen.findByTestId('constraint-override-confirm')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('constraint-override-ok'));

    await vi.waitFor(() => expect(mockUpdateCourse).toHaveBeenCalledTimes(1));
    // v1 → v2(422) → v2(ack) → コース の順。PATCH は最後の 1 回だけ。
    expect(callOrder).toEqual(['assign', 'assign', 'assign', 'update-course']);
    // M2: 訪問側で実際に確認を通したので、コース PATCH にも ack を引き継ぐ。
    expect(mockUpdateCourse.mock.calls[0]?.[0]).toMatchObject({
      patch: { assigned_staff_id: STAFF_2, acknowledge_constraint_warnings: true },
    });
  });

  it('(i6b) M2: 確認を通していないときはコース PATCH に ack を付けない', async () => {
    setupUnassignedHooks();
    renderStaffTab();

    fireEvent.click(screen.getByTestId('staff-week-suggest-tpl-A-0'));
    expect(await screen.findByTestId('assign-suggestion-popover')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId(`assign-suggestion-apply-${STAFF_2}`));

    await vi.waitFor(() => expect(mockUpdateCourse).toHaveBeenCalledTimes(1));
    const patch = (mockUpdateCourse.mock.calls[0]?.[0] as { patch: Record<string, unknown> }).patch;
    expect(patch.acknowledge_constraint_warnings).toBeUndefined();
  });

  it('(i7) H1: 週を切り替えるとバッジは「提案を見る」へ戻る', async () => {
    setupUnassignedHooks();
    const { setWeek } = renderStaffTab();

    fireEvent.click(screen.getByTestId('staff-tab-see-suggestions'));
    await vi.waitFor(() =>
      expect(screen.getByTestId('staff-week-suggest-tpl-A-0')).toHaveTextContent('◎ 1名 引受可'),
    );

    // 翌週へ (訪問も翌週の日付で返す = 帯は出たままにして「持ち越し」を見る)。
    const nextWeekStart = new Date(WEEK_START);
    nextWeekStart.setDate(WEEK_START.getDate() + 7);
    const nextMondayIso = `${nextWeekStart.getFullYear()}-${String(nextWeekStart.getMonth() + 1).padStart(2, '0')}-${String(nextWeekStart.getDate()).padStart(2, '0')}`;
    setupUnassignedHooks(nextMondayIso);
    setWeek(nextWeekStart);

    await vi.waitFor(() =>
      expect(screen.getByTestId('staff-week-suggest-tpl-A-0')).toHaveTextContent('提案を見る'),
    );
  });

  it('(i8) H2: 訪問 1 件の担当変更でも提案キャッシュとバッジが捨てられる', async () => {
    setupUnassignedHooks();
    renderStaffTab();

    fireEvent.click(screen.getByTestId('staff-tab-see-suggestions'));
    await vi.waitFor(() =>
      expect(screen.getByTestId('staff-week-suggest-tpl-A-0')).toHaveTextContent('◎ 1名 引受可'),
    );

    // 提案とは別経路 (訪問メニューの「担当変更」) で盤面を動かす。
    fireEvent.click(screen.getByTestId('staff-week-visit-v1'));
    expect(await screen.findByTestId('visit-action-menu')).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('visit-action-staff'), { target: { value: STAFF_2 } });

    await vi.waitFor(() => expect(mockAssignStaffWeek).toHaveBeenCalled());
    // 盤面が動けば候補は古い。バッジは「未計算」へ戻す。
    await vi.waitFor(() =>
      expect(screen.getByTestId('staff-week-suggest-tpl-A-0')).toHaveTextContent('提案を見る'),
    );
    expect(invalidatedKeys.some((k) => Array.isArray(k) && k[0] === 'assign-candidates')).toBe(
      true,
    );
  });
});
