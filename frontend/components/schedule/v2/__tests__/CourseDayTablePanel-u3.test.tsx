/**
 * CourseDayTablePanel — Wave U-3 テスト。
 *
 * カバーするシナリオ:
 *  U3-1  opLogState=undefined → 戻る/進む両ボタン disabled
 *  U3-2  can_undo=true → 戻るボタンのみ有効
 *  U3-3  can_redo=true → 進むボタンのみ有効
 *  U3-4  isPending=true → 両ボタン disabled
 *  U3-5  戻るボタンクリック → mutateAsync が { iso_year, iso_week } で呼ばれる
 *  U3-6  進むボタンクリック → mutateAsync が呼ばれる
 *  U3-7  mutateAsync が 409 ApiError を投げても toast.error は呼ばれない
 *  U3-8  mutateAsync が汎用エラーを投げると toast.error が呼ばれる
 *  U3-9  Ctrl+Z → can_undo=true で mutateAsync が呼ばれる
 *  U3-10 Ctrl+Z → input にフォーカス時は発火しない
 *  U3-11 Ctrl+Y → can_redo=true で mutateAsync が呼ばれる
 *  U3-12 Ctrl+Shift+Z → can_redo=true で mutateAsync が呼ばれる
 *  U3-13 can_undo=false のとき Ctrl+Z は mutateAsync を呼ばない
 *  U3-14 プール→タイムライン列 DnD: placeAndFix に有効 UUID が渡される
 *  (U3-15 は Phase 2 の日テーブル撤去で削除。末尾コメント参照)
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ApiError } from '@/lib/api-client';
import { timeToY } from '@/lib/scheduling/timeline';

// ─── hoisted 変数 ──────────────────────────────────────────────────────────
// vi.mock の factory は hoisting されるため、factory 内で参照する変数も hoisted が必要。

const {
  mockOpLogStore,
  mockUndoMutateAsync,
  mockRedoMutateAsync,
  mockInvalidate,
  mockToast,
  dndState,
} = vi.hoisted(() => ({
  mockOpLogStore: {
    state: undefined as
      | {
          can_undo: boolean;
          can_redo: boolean;
          undo_label: string | null;
          redo_label: string | null;
        }
      | undefined,
    undoIsPending: false,
    redoIsPending: false,
  },
  mockUndoMutateAsync: vi
    .fn<[{ iso_year: number; iso_week: number }], Promise<unknown>>()
    .mockResolvedValue(undefined),
  mockRedoMutateAsync: vi
    .fn<[{ iso_year: number; iso_week: number }], Promise<unknown>>()
    .mockResolvedValue(undefined),
  mockInvalidate: vi.fn(),
  mockToast: {
    warning: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
  dndState: {
    capturedHandlers: { onDragEnd: undefined as undefined | ((e: unknown) => Promise<void>) },
  },
}));

// ─── モック ───────────────────────────────────────────────────────────────

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

// lucide-react: 実アイコンをベースに、テストで data-testid を使うアイコンだけ上書き。
// importOriginal でツリーを丸ごと取り込むことで子コンポーネントが使う未列挙アイコンも解決する。
vi.mock('lucide-react', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type LucideReact = typeof import('lucide-react');
  const actual = await importOriginal<LucideReact>();
  return {
    ...actual,
    Loader2: () => <span data-testid="loader" />,
    Undo2: () => <span data-testid="undo-icon" />,
    Redo2: () => <span data-testid="redo-icon" />,
  };
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
    'data-testid': testId,
    title,
    ...rest
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    'data-testid'?: string;
    title?: string;
    [k: string]: unknown;
  }) => (
    <button onClick={onClick} disabled={disabled} data-testid={testId} title={title} {...rest}>
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

// ──────────────────────────────────────────────────────────────────────────
// 各クエリモジュール: importOriginal でモジュール全エクスポートを保持しつつ、
// テストで制御が必要な hook だけを差し替える。
// これにより子コンポーネントが未列挙エクスポートを使っても
// "No X export is defined on the mock" エラーが出ない。
// QueryClientProvider があるため useMutation/useQuery も動作する。
// ──────────────────────────────────────────────────────────────────────────

vi.mock('@/lib/queries/weekday_staff_capacity', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/weekday_staff_capacity');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    useWeekdayStaffCapacityLookup: () => ({
      staffCountFor: () => 5,
      managerCountFor: () => 0,
      courseCodesMax: 5,
      isLoading: false,
    }),
  };
});
vi.mock('@/lib/queries/pfv_course_presence', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/pfv_course_presence');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    usePfvCoursePresenceLookup: () => ({
      pfvCountFor: () => 0,
      isLoading: false,
    }),
  };
});
vi.mock('@/lib/queries/offices', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/offices');
  const actual = await importOriginal<M>();
  return { ...actual, useOffices: (...args: unknown[]) => mockOffices(...args) };
});
vi.mock('@/lib/queries/patients', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/patients');
  const actual = await importOriginal<M>();
  return { ...actual, usePatients: (...args: unknown[]) => mockPatients(...args) };
});
vi.mock('@/lib/queries/staff', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/staff');
  const actual = await importOriginal<M>();
  return { ...actual, useStaffList: (...args: unknown[]) => mockStaffList(...args) };
});
vi.mock('@/lib/queries/visits', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/visits');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    useVisits: (...args: unknown[]) => mockVisits(...args),
    useDeleteVisit: () => ({ mutateAsync: mockDeleteVisit, isPending: false }),
  };
});
vi.mock('@/lib/queries/courses', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/courses');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    useCourses: (...args: unknown[]) => mockCourses(...args),
    useUpdateCourse: () => ({ mutateAsync: mockUpdateCourse, isPending: false }),
  };
});
vi.mock('@/lib/queries/place_and_fix', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/place_and_fix');
  const actual = await importOriginal<M>();
  return { ...actual, usePlaceAndFix: () => ({ mutateAsync: mockPlaceAndFix, isPending: false }) };
});
vi.mock('@/lib/queries/generate_week', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/generate_week');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    useGenerateWeek: () => ({ mutateAsync: mockGenerateWeek, isPending: false }),
    useGenerateWeekOnly: () => ({ mutateAsync: mockGenerateWeek, isPending: false }),
  };
});
vi.mock('@/lib/queries/assign_staff_only', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/assign_staff_only');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    useAssignStaffOnly: () => ({ mutateAsync: mockAssignStaffOnly, isPending: false }),
  };
});
vi.mock('@/lib/queries/g21', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/g21');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    useTogglePfvPin: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
    useBulkPinPfvs: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  };
});
vi.mock('@/lib/queries/autoScheduleV2', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/autoScheduleV2');
  const actual = await importOriginal<M>();
  return { ...actual };
});
vi.mock('@/lib/queries/staff-events', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/staff-events');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    useWeekStaffEvents: () => ({ data: [], isLoading: false }),
    buildStaffEventsMap: () => new Map(),
    useUpdateEventForDrag: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});
vi.mock('@/lib/queries/patient_fixed_visits', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/patient_fixed_visits');
  const actual = await importOriginal<M>();
  return { ...actual };
});
vi.mock('@/lib/api/patientSync', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/api/patientSync');
  const actual = await importOriginal<M>();
  return { ...actual };
});
vi.mock('@/lib/queries/opLog', () => ({
  useOpLogState: () => ({ data: mockOpLogStore.state, isLoading: false }),
  useUndoOpLog: () => ({
    mutateAsync: mockUndoMutateAsync,
    isPending: mockOpLogStore.undoIsPending,
  }),
  useRedoOpLog: () => ({
    mutateAsync: mockRedoMutateAsync,
    isPending: mockOpLogStore.redoIsPending,
  }),
  useInvalidateOpLog: () => mockInvalidate,
  OP_LOG_STATE_KEY: 'op-log-state',
}));

vi.mock('@/lib/queries/visitMoveWeekOnly', () => ({
  useVisitMoveWeekOnly: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), back: vi.fn() }),
  usePathname: () => '/schedule',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/queries/schedulingSettings', () => ({
  useSchedulingSettings: () => ({ data: undefined, isLoading: false }),
}));

// ─── Subject under test ────────────────────────────────────────────────────

import { CourseDayTablePanel } from '../CourseDayTablePanel';

// ─── helpers ──────────────────────────────────────────────────────────────

/**
 * BulkFixToPatternButton / ScheduleHealthDialog など CourseDayTablePanel の
 * 子コンポーネントが QueryClient を要求するため、QueryClientProvider でラップする。
 * 各テストで新規クライアントを作ることでキャッシュ汚染を防ぐ。
 */
function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

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

// weekStart=monday(2026,5,4) → isoYear=2026, isoWeek=19
const WEEK_START = monday(2026, 5, 4);
const ISO_YEAR = 2026;
const ISO_WEEK = 19;

/** UUID v4 の正規表現 */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

// ─── Tests ─────────────────────────────────────────────────────────────────

describe('CourseDayTablePanel — Wave U-3 戻る/進みボタン・キーボード・DnD', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // mockUndoMutateAsync / mockRedoMutateAsync は vi.fn() の再利用
    mockUndoMutateAsync.mockResolvedValue(undefined);
    mockRedoMutateAsync.mockResolvedValue(undefined);
    // opLogStore をデフォルトにリセット
    mockOpLogStore.state = undefined;
    mockOpLogStore.undoIsPending = false;
    mockOpLogStore.redoIsPending = false;
    // DnD ハンドラをリセット
    dndState.capturedHandlers.onDragEnd = undefined;
  });

  // ────────────────────────────────────────────────────────────
  // 1. ボタン disabled 状態
  // ────────────────────────────────────────────────────────────

  describe('ボタン disabled 状態', () => {
    it('U3-1. opLogState=undefined → 両ボタンが disabled', () => {
      mockOpLogStore.state = undefined;
      setupHooks({
        templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      });
      renderWithClient(
        <CourseDayTablePanel weekStart={WEEK_START} officeId={null} canEdit={true} />,
      );
      expect(screen.getByTestId('schedule-undo-button')).toBeDisabled();
      expect(screen.getByTestId('schedule-redo-button')).toBeDisabled();
    });

    it('U3-2. can_undo=true, can_redo=false → 戻るのみ有効、進むは disabled', () => {
      mockOpLogStore.state = {
        can_undo: true,
        can_redo: false,
        undo_label: null,
        redo_label: null,
      };
      setupHooks({
        templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      });
      renderWithClient(
        <CourseDayTablePanel weekStart={WEEK_START} officeId={null} canEdit={true} />,
      );
      expect(screen.getByTestId('schedule-undo-button')).not.toBeDisabled();
      expect(screen.getByTestId('schedule-redo-button')).toBeDisabled();
    });

    it('U3-3. can_undo=false, can_redo=true → 進むのみ有効、戻るは disabled', () => {
      mockOpLogStore.state = {
        can_undo: false,
        can_redo: true,
        undo_label: null,
        redo_label: null,
      };
      setupHooks({
        templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      });
      renderWithClient(
        <CourseDayTablePanel weekStart={WEEK_START} officeId={null} canEdit={true} />,
      );
      expect(screen.getByTestId('schedule-undo-button')).toBeDisabled();
      expect(screen.getByTestId('schedule-redo-button')).not.toBeDisabled();
    });

    it('U3-4. isPending=true → 両ボタンが disabled (実行中)', () => {
      mockOpLogStore.state = { can_undo: true, can_redo: true, undo_label: null, redo_label: null };
      mockOpLogStore.undoIsPending = true;
      mockOpLogStore.redoIsPending = false; // undo pending だけで両方 disabled になる
      setupHooks({
        templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      });
      renderWithClient(
        <CourseDayTablePanel weekStart={WEEK_START} officeId={null} canEdit={true} />,
      );
      // undoRedoPending = undoMut.isPending || redoMut.isPending → true
      expect(screen.getByTestId('schedule-undo-button')).toBeDisabled();
      expect(screen.getByTestId('schedule-redo-button')).toBeDisabled();
    });

    it('U3-4b. undo_label が設定されると title 属性に反映される', () => {
      mockOpLogStore.state = {
        can_undo: true,
        can_redo: false,
        undo_label: '田中様を移動',
        redo_label: null,
      };
      setupHooks({
        templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      });
      renderWithClient(
        <CourseDayTablePanel weekStart={WEEK_START} officeId={null} canEdit={true} />,
      );
      expect(screen.getByTestId('schedule-undo-button')).toHaveAttribute(
        'title',
        '戻す: 田中様を移動',
      );
    });
  });

  // ────────────────────────────────────────────────────────────
  // 2. ボタンクリック → mutation
  // ────────────────────────────────────────────────────────────

  describe('ボタンクリック → mutation', () => {
    it('U3-5. 戻るボタンクリック → useUndoOpLog.mutateAsync が iso_year/iso_week 付きで呼ばれる', async () => {
      mockOpLogStore.state = {
        can_undo: true,
        can_redo: false,
        undo_label: null,
        redo_label: null,
      };
      setupHooks({
        templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      });
      renderWithClient(
        <CourseDayTablePanel weekStart={WEEK_START} officeId={null} canEdit={true} />,
      );
      await act(async () => {
        fireEvent.click(screen.getByTestId('schedule-undo-button'));
      });
      expect(mockUndoMutateAsync).toHaveBeenCalledOnce();
      expect(mockUndoMutateAsync).toHaveBeenCalledWith({ iso_year: ISO_YEAR, iso_week: ISO_WEEK });
    });

    it('U3-6. 進むボタンクリック → useRedoOpLog.mutateAsync が iso_year/iso_week 付きで呼ばれる', async () => {
      mockOpLogStore.state = {
        can_undo: false,
        can_redo: true,
        undo_label: null,
        redo_label: null,
      };
      setupHooks({
        templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      });
      renderWithClient(
        <CourseDayTablePanel weekStart={WEEK_START} officeId={null} canEdit={true} />,
      );
      await act(async () => {
        fireEvent.click(screen.getByTestId('schedule-redo-button'));
      });
      expect(mockRedoMutateAsync).toHaveBeenCalledOnce();
      expect(mockRedoMutateAsync).toHaveBeenCalledWith({ iso_year: ISO_YEAR, iso_week: ISO_WEEK });
    });
  });

  // ────────────────────────────────────────────────────────────
  // 3. エラーハンドリング
  // ────────────────────────────────────────────────────────────

  describe('エラーハンドリング', () => {
    it('U3-7. mutateAsync が 409 ApiError を投げても toast.error は呼ばれない', async () => {
      mockOpLogStore.state = {
        can_undo: true,
        can_redo: false,
        undo_label: null,
        redo_label: null,
      };
      mockUndoMutateAsync.mockRejectedValue(new ApiError('Conflict', 409, null));
      setupHooks({
        templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      });
      renderWithClient(
        <CourseDayTablePanel weekStart={WEEK_START} officeId={null} canEdit={true} />,
      );
      await act(async () => {
        fireEvent.click(screen.getByTestId('schedule-undo-button'));
      });
      // 409 は handleUndo 内の catch で吸収 → toast.error を呼ばない
      expect(mockToast.error).not.toHaveBeenCalled();
    });

    it('U3-8. mutateAsync が汎用エラーを投げると toast.error が呼ばれる', async () => {
      mockOpLogStore.state = {
        can_undo: true,
        can_redo: false,
        undo_label: null,
        redo_label: null,
      };
      mockUndoMutateAsync.mockRejectedValue(new Error('Network error'));
      setupHooks({
        templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      });
      renderWithClient(
        <CourseDayTablePanel weekStart={WEEK_START} officeId={null} canEdit={true} />,
      );
      await act(async () => {
        fireEvent.click(screen.getByTestId('schedule-undo-button'));
      });
      expect(mockToast.error).toHaveBeenCalledOnce();
      expect(mockToast.error).toHaveBeenCalledWith(
        expect.stringContaining('操作の取り消しに失敗しました'),
      );
    });
  });

  // ────────────────────────────────────────────────────────────
  // 4. Ctrl+Z / Ctrl+Y キーボードショートカット
  // ────────────────────────────────────────────────────────────

  describe('Ctrl+Z/Y キーボードショートカット', () => {
    it('U3-9. Ctrl+Z → can_undo=true で mutateAsync が呼ばれる', async () => {
      mockOpLogStore.state = {
        can_undo: true,
        can_redo: false,
        undo_label: null,
        redo_label: null,
      };
      setupHooks({
        templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      });
      renderWithClient(
        <CourseDayTablePanel weekStart={WEEK_START} officeId={null} canEdit={true} />,
      );
      await act(async () => {
        window.dispatchEvent(
          new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true }),
        );
      });
      expect(mockUndoMutateAsync).toHaveBeenCalledOnce();
      expect(mockUndoMutateAsync).toHaveBeenCalledWith({ iso_year: ISO_YEAR, iso_week: ISO_WEEK });
    });

    it('U3-10. Ctrl+Z → input にフォーカス時は発火しない', async () => {
      mockOpLogStore.state = {
        can_undo: true,
        can_redo: false,
        undo_label: null,
        redo_label: null,
      };
      setupHooks({
        templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      });
      renderWithClient(
        <CourseDayTablePanel weekStart={WEEK_START} officeId={null} canEdit={true} />,
      );
      // input 要素を追加してそこからイベントを発火 → e.target が HTMLInputElement
      const input = document.body.appendChild(document.createElement('input'));
      try {
        await act(async () => {
          input.dispatchEvent(
            new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true }),
          );
        });
        expect(mockUndoMutateAsync).not.toHaveBeenCalled();
      } finally {
        document.body.removeChild(input);
      }
    });

    it('U3-11. Ctrl+Y → can_redo=true で mutateAsync が呼ばれる', async () => {
      mockOpLogStore.state = {
        can_undo: false,
        can_redo: true,
        undo_label: null,
        redo_label: null,
      };
      setupHooks({
        templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      });
      renderWithClient(
        <CourseDayTablePanel weekStart={WEEK_START} officeId={null} canEdit={true} />,
      );
      await act(async () => {
        window.dispatchEvent(
          new KeyboardEvent('keydown', { key: 'y', ctrlKey: true, bubbles: true }),
        );
      });
      expect(mockRedoMutateAsync).toHaveBeenCalledOnce();
      expect(mockRedoMutateAsync).toHaveBeenCalledWith({ iso_year: ISO_YEAR, iso_week: ISO_WEEK });
    });

    it('U3-12. Ctrl+Shift+Z → can_redo=true で mutateAsync が呼ばれる', async () => {
      mockOpLogStore.state = {
        can_undo: false,
        can_redo: true,
        undo_label: null,
        redo_label: null,
      };
      setupHooks({
        templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      });
      renderWithClient(
        <CourseDayTablePanel weekStart={WEEK_START} officeId={null} canEdit={true} />,
      );
      await act(async () => {
        window.dispatchEvent(
          new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, shiftKey: true, bubbles: true }),
        );
      });
      expect(mockRedoMutateAsync).toHaveBeenCalledOnce();
    });

    it('U3-13. can_undo=false のとき Ctrl+Z は mutateAsync を呼ばない', async () => {
      mockOpLogStore.state = {
        can_undo: false,
        can_redo: false,
        undo_label: null,
        redo_label: null,
      };
      setupHooks({
        templates: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      });
      renderWithClient(
        <CourseDayTablePanel weekStart={WEEK_START} officeId={null} canEdit={true} />,
      );
      await act(async () => {
        window.dispatchEvent(
          new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true }),
        );
      });
      expect(mockUndoMutateAsync).not.toHaveBeenCalled();
    });
  });

  // ────────────────────────────────────────────────────────────
  // 5. DnD op_group_id ペイロード
  // ────────────────────────────────────────────────────────────

  describe('DnD op_group_id ペイロード', () => {
    it('U3-14. プール→タイムライン列 DnD: placeAndFix に有効 UUID が渡される', async () => {
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
      renderWithClient(
        <CourseDayTablePanel weekStart={WEEK_START} officeId="office-honten" canEdit={true} />,
      );
      // 既定タブは「週」。日タイムライン列 (tl-col) を出すため月曜タブへ切替える。
      fireEvent.click(screen.getByTestId('course-day-tab-0'));
      expect(dndState.capturedHandlers.onDragEnd).toBeDefined();
      await act(async () => {
        await dndState.capturedHandlers.onDragEnd!({
          active: {
            id: 'pool-patient:99999999-9999-9999-9999-999999999999',
            rect: { current: { translated: { top: 10 + (timeToY('09:30') ?? 0) } } },
          },
          over: { id: 'tl-col:tpl-A:0', rect: { top: 10 } },
        });
      });
      expect(mockPlaceAndFix).toHaveBeenCalledOnce();
      const arg = mockPlaceAndFix.mock.calls[0][0] as Record<string, unknown>;
      // op_group_id は UUID 形式
      expect(typeof arg.op_group_id).toBe('string');
      expect(arg.op_group_id).toMatch(UUID_RE);
    });

    // U3-15 (テーブル→テーブル DnD: delete + placeAndFix が同一 UUID) は削除した.
    //   Phase 2 で日テーブルを撤去し、`visit:` → `course-day-cell:` の
    //   「delete + place-and-fix」経路そのものが production から消えたため
    //   (タイムラインの移動は useVisitMoveWeekOnly の単一 BE 呼出で 2 段階 mutation ではない)。
    //   「1 ユーザー操作 = 1 op_group_id」の担保は CourseDayTablePanel.test.tsx の G4-2
    //   (tl-pair をプールへドロップ → 2 件の delete が同一 op_group_id を共有) が継承する。
  });
});
