/**
 * CourseDayTablePanel — 職員スケジュールタブ「今週の運転席」の結線 (Phase E / FE-C)。
 *
 * カバーするシナリオ:
 *   (a) 訪問行クリックでメニューが開き、「今週だけ取消」が visit-cancel-week を呼ぶ
 *   (b) セルの「🛌 休みにする」→ 代替候補で付け替え = override → assign の順で呼ぶ
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
/**
 * 休み登録は「遅い API」として振る舞わせる。付け替えを await せずに走らせると
 * 順序が逆転するため、遅延を入れて**本当に待っているか**を検証する (M-12)。
 */
const mockCreateOverride = vi.fn().mockImplementation(async (payload: unknown) => {
  callOrder.push('override:start');
  await new Promise((r) => setTimeout(r, 20));
  callOrder.push('override:done');
  return payload;
});
const mockAssignStaffWeek = vi.fn().mockImplementation(async () => {
  callOrder.push('assign');
  return { changed: true };
});
/** PATCH /courses/{id} (コース丸ごとの担当変更)。入れ替えの経路判定に使う。 */
const mockUpdateCourse = vi.fn().mockResolvedValue({ id: 'course-A-mon' });

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
  useCreateOverride: () => ({ mutateAsync: mockCreateOverride, isPending: false }),
}));
// 運転席の新 API (契約書 §2)。代替候補は 1 グループ (コース無し = 訪問1件) を返す。
vi.mock('@/lib/queries/cockpit', () => ({
  useVisitCancelWeek: () => ({ mutateAsync: mockVisitCancelWeek, isPending: false }),
  useEventCancelWeek: () => ({ mutateAsync: vi.fn(), isPending: false }),
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
  return view;
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
    mockCreateOverride.mockImplementation(async (payload: unknown) => {
      callOrder.push('override:start');
      await new Promise((r) => setTimeout(r, 20));
      callOrder.push('override:done');
      return payload;
    });
    mockUpdateCourse.mockResolvedValue({ id: 'course-A-mon' });
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

  it('(b) 「🛌 休みにする」→ 代替候補の適用は 休み登録 → 担当付け替え の順', async () => {
    setupHooks();
    renderStaffTab();

    fireEvent.click(screen.getByTestId(`staff-week-off-action-${STAFF_1}-0`));
    expect(await screen.findByTestId('substitute-panel')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId(`substitute-cand-__none__0-${STAFF_2}`));

    // 休みの登録が**完了してから**付け替える (遅延 mock で順序を実証)。
    await vi.waitFor(() =>
      expect(callOrder).toEqual(['override:start', 'override:done', 'assign']),
    );
    // 休みは 1 回だけ (複数コースを渡しても override は 1 件)。
    expect(mockCreateOverride).toHaveBeenCalledTimes(1);
    expect(mockCreateOverride.mock.calls[0]?.[0]).toMatchObject({
      type: '休み',
      date: MONDAY_ISO,
    });
    expect(mockAssignStaffWeek.mock.calls[0]?.[0]).toMatchObject({
      visit_id: 'v1',
      staff_id: STAFF_2,
    });
  });

  it('(b2) 休み登録の成功で週の休み一覧 (staff-overrides-week) が失効する', async () => {
    setupHooks();
    renderStaffTab();

    fireEvent.click(screen.getByTestId(`staff-week-off-action-${STAFF_1}-0`));
    expect(await screen.findByTestId('substitute-panel')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId(`substitute-cand-__none__0-${STAFF_2}`));

    await vi.waitFor(() => expect(mockCreateOverride).toHaveBeenCalled());
    // useCreateOverride は ['staff-overrides', staffId] しか失効させないため、
    // 盤面の網掛け (週一括キー) が古いまま残る回帰を防ぐ。
    await vi.waitFor(() =>
      expect(invalidatedKeys.some((k) => Array.isArray(k) && k[0] === 'staff-overrides-week')).toBe(
        true,
      ),
    );
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

  it('(g11) 急な休みの付替: 失敗したら入れ替えと同じ形で toast.error になる', async () => {
    setupHooks();
    mockAssignStaffWeek.mockImplementation(async () => {
      callOrder.push('assign');
      throw new Error('boom');
    });
    renderStaffTab();

    fireEvent.click(screen.getByTestId(`staff-week-off-action-${STAFF_1}-0`));
    expect(await screen.findByTestId('substitute-panel')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId(`substitute-cand-__none__0-${STAFF_2}`));

    await vi.waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(String(mockToast.error.mock.calls[0]?.[0])).toContain('1 件の付け替えに失敗しました');
    // 休み登録そのものの成功トーストは出るが、**付け替えの成功**は出さない
    // (従来は失敗しても成功文言が出ていた = 非対称の解消)。
    const successMsgs = mockToast.success.mock.calls.map((c) => String(c[0]));
    expect(successMsgs.some((m) => m.includes('曜を休みに。'))).toBe(false);
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
});
