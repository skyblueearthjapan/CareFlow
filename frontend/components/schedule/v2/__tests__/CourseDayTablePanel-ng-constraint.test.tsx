/**
 * CourseDayTablePanel — NG スタッフ / 性別制限の「確認して通す」配線テスト
 * (patient-ng-staff-design.md §7-2 / 2026-08-11).
 *
 * カバーするシナリオ:
 *   NC-1 訪問移動 (tl-visit → tl-col → この週だけ) が 422
 *        `constraint_confirmation_required` で返る → 確認ダイアログが移動用の文言で開く
 *   NC-2 「移動する」で acknowledge_constraint_warnings: true を足して再送する
 *   NC-3 422 でない失敗ではダイアログを出さず従来どおり toast.error
 *   NC-4 プール → 列 ドロップ (place-and-fix) も同じ確認 → ack 再送になる
 *
 * モック構成は CourseDayTablePanel-u3.test.tsx に準拠 (DndContext の onDragEnd を捕捉して
 * ドラッグを合成する)。
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ApiError } from '@/lib/api-client';
import { timeToY } from '@/lib/scheduling/timeline';

const { mockToast, dndState, mockMoveWeekOnly, mockInvalidate } = vi.hoisted(() => ({
  mockToast: { warning: vi.fn(), success: vi.fn(), error: vi.fn(), info: vi.fn() },
  dndState: {
    capturedHandlers: { onDragEnd: undefined as undefined | ((e: unknown) => Promise<void>) },
  },
  mockMoveWeekOnly: vi.fn(),
  mockInvalidate: vi.fn(),
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

vi.mock('@dnd-kit/utilities', () => ({ CSS: { Translate: { toString: () => '' } } }));
vi.mock('sonner', () => ({ toast: mockToast }));
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: null, status: 'unauthenticated' }),
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

vi.mock('@tanstack/react-query', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type TanstackQuery = typeof import('@tanstack/react-query');
  const actual = await importOriginal<TanstackQuery>();
  return { ...actual, useQueries: (...args: unknown[]) => mockUseQueries(...args) };
});

vi.mock('@/lib/api/fetcher', () => ({ fetcher: vi.fn() }));

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
    usePfvCoursePresenceLookup: () => ({ pfvCountFor: () => 0, isLoading: false }),
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
    useDeleteVisit: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});
vi.mock('@/lib/queries/courses', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/courses');
  const actual = await importOriginal<M>();
  return {
    ...actual,
    useCourses: (...args: unknown[]) => mockCourses(...args),
    useUpdateCourse: () => ({ mutateAsync: vi.fn(), isPending: false }),
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
    useGenerateWeek: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useGenerateWeekOnly: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});
vi.mock('@/lib/queries/assign_staff_only', async (importOriginal) => {
  // eslint-disable-next-line @typescript-eslint/consistent-type-imports
  type M = typeof import('@/lib/queries/assign_staff_only');
  const actual = await importOriginal<M>();
  return { ...actual, useAssignStaffOnly: () => ({ mutateAsync: vi.fn(), isPending: false }) };
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
vi.mock('@/lib/queries/opLog', () => ({
  useOpLogState: () => ({ data: undefined, isLoading: false }),
  useUndoOpLog: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRedoOpLog: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useInvalidateOpLog: () => mockInvalidate,
  OP_LOG_STATE_KEY: 'op-log-state',
}));
vi.mock('@/lib/queries/visitMoveWeekOnly', () => ({
  useVisitMoveWeekOnly: () => ({ mutateAsync: mockMoveWeekOnly, isPending: false }),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), back: vi.fn() }),
  usePathname: () => '/schedule',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/queries/schedulingSettings', () => ({
  useSchedulingSettings: () => ({ data: undefined, isLoading: false }),
}));

import { CourseDayTablePanel } from '../CourseDayTablePanel';

// ─── helpers ──────────────────────────────────────────────────────────────

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

const tlCourse = {
  id: 'course-1',
  course_template_id: 'tpl-A',
  iso_year: 2026,
  iso_week: 19,
  weekday: 0,
  code: 'A',
  office_id: 'office-honten',
  assigned_staff_id: null,
  course_status: 'course_fixed' as const,
  deleted_at: null,
};

const V1 = {
  id: 'v-1',
  patient_id: '11111111-1111-4111-8111-111111111111',
  patient_name: '田中 太郎',
  visit_date: '2026-05-04',
  start_time: '10:00:00',
  end_time: '10:30:00',
  primary_staff_id: null,
  course_id: 'course-1',
  required_staff_count: 1,
  type: 'regular',
  status: 'planned',
  source: 'allocate',
};

/** BE の 422 `constraint_confirmation_required`. */
function constraintError(): ApiError {
  return new ApiError('Unprocessable Entity', 422, {
    detail: {
      code: 'constraint_confirmation_required',
      warnings: [
        {
          kind: 'ng_staff',
          patient_id: V1.patient_id,
          patient_name: '田中 太郎',
          staff_id: '22222222-2222-4222-8222-222222222222',
          staff_name: '熊澤 妙子',
          note: 'ご家族からの申し出',
        },
      ],
    },
  });
}

function setupHooks(opts: { patients?: Array<Record<string, unknown>> } = {}) {
  mockOffices.mockReturnValue({
    allOffices: [{ id: 'office-honten', name: '本店' }],
    isLoading: false,
  });
  mockPatients.mockReturnValue({
    data: {
      items: opts.patients ?? [
        {
          id: V1.patient_id,
          name: '田中 太郎',
          kana: null,
          status: 'active',
          address: '',
        },
      ],
    },
    isLoading: false,
  });
  mockStaffList.mockReturnValue({ data: [], isLoading: false });
  mockUseQueries.mockReturnValue([
    {
      data: [{ id: 'tpl-A', office_id: 'office-honten', label: 'A', ...baseTpl }],
      isLoading: false,
    },
  ]);
  mockVisits.mockReturnValue({ data: { items: [V1], truncated: false }, isLoading: false });
  mockCourses.mockReturnValue({ data: [tlCourse], isLoading: false });
}

function renderPanel() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <CourseDayTablePanel weekStart={monday(2026, 5, 4)} officeId="office-honten" canEdit={true} />
    </QueryClientProvider>,
  );
}

/** tl-visit を同一列の 11:00 へドラッグ → TimelineMoveDialog を「この週だけ」で確定。 */
async function dragVisitAndConfirmMove() {
  fireEvent.click(screen.getByTestId('course-day-tab-0'));
  await act(async () => {
    await dndState.capturedHandlers.onDragEnd!({
      active: {
        id: 'tl-visit:v-1',
        rect: { current: { translated: { top: 10 + (timeToY('11:00') ?? 0) } } },
      },
      over: { id: 'tl-col:tpl-A:0', rect: { top: 10 } },
    });
  });
  await waitFor(() => expect(screen.getByTestId('timeline-move-dialog')).toBeInTheDocument());
  await act(async () => {
    fireEvent.click(screen.getByTestId('timeline-move-confirm'));
  });
}

// ─── Tests ─────────────────────────────────────────────────────────────────

describe('CourseDayTablePanel — NG スタッフ / 性別制限の確認フロー (§7-2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    dndState.capturedHandlers.onDragEnd = undefined;
    setupHooks();
  });

  it('NC-1. 移動が 422 constraint_confirmation_required なら移動用の文言で確認ダイアログが出る', async () => {
    mockMoveWeekOnly.mockRejectedValueOnce(constraintError());
    renderPanel();
    await dragVisitAndConfirmMove();

    const dialog = await screen.findByTestId('constraint-override-confirm');
    expect(dialog).toHaveTextContent('それでも移動しますか？');
    expect(dialog).toHaveTextContent('この移動先の担当者は、次の制約に抵触します');
    expect(screen.getByTestId('constraint-override-ok')).toHaveTextContent('移動する');
    // 警告内容 (NG スタッフ + メモ) がそのまま出る。
    const row = screen.getByTestId('constraint-override-warning-row');
    expect(row).toHaveAttribute('data-kind', 'ng_staff');
    expect(row).toHaveTextContent('熊澤 妙子さんは田中 太郎様のNGスタッフです');
    // 確認前は成功トーストを出さない。
    expect(mockToast.success).not.toHaveBeenCalled();
  });

  it('NC-2. 「移動する」で acknowledge_constraint_warnings:true を足して再送する', async () => {
    mockMoveWeekOnly.mockRejectedValueOnce(constraintError()).mockResolvedValueOnce({
      visits_moved: 1,
    });
    renderPanel();
    await dragVisitAndConfirmMove();
    await screen.findByTestId('constraint-override-confirm');

    await act(async () => {
      fireEvent.click(screen.getByTestId('constraint-override-ok'));
    });

    await waitFor(() => expect(mockMoveWeekOnly).toHaveBeenCalledTimes(2));
    const first = mockMoveWeekOnly.mock.calls[0][0] as Record<string, unknown>;
    const second = mockMoveWeekOnly.mock.calls[1][0] as Record<string, unknown>;
    // 1 回目は ack なし、2 回目だけ true。
    expect(first.acknowledge_constraint_warnings).toBeUndefined();
    expect(second.acknowledge_constraint_warnings).toBe(true);
    // 移動先の内容 (時刻) は同一。op_group_id も引き継ぐ。
    expect(second.new_start_time).toBe(first.new_start_time);
    expect(second.op_group_id).toBe(first.op_group_id);
    // 再送成功でダイアログは閉じ、成功トーストが出る。
    await waitFor(() =>
      expect(screen.queryByTestId('constraint-override-confirm')).not.toBeInTheDocument(),
    );
    expect(mockToast.success).toHaveBeenCalled();
  });

  it('NC-3. 422 でない失敗では確認ダイアログを出さず toast.error のまま', async () => {
    mockMoveWeekOnly.mockRejectedValueOnce(new ApiError('Conflict', 409, { detail: 'busy' }));
    renderPanel();
    await dragVisitAndConfirmMove();

    await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
    expect(screen.queryByTestId('constraint-override-confirm')).not.toBeInTheDocument();
  });

  it('NC-4. プール→列ドロップ (place-and-fix) も確認 → ack 再送になる', async () => {
    // プール患者 = 訪問を持たない患者を 1 名足す。
    setupHooks({
      patients: [
        { id: V1.patient_id, name: '田中 太郎', kana: null, status: 'active', address: '' },
        {
          id: '33333333-3333-4333-8333-333333333333',
          name: '鈴木 花子',
          kana: null,
          status: 'active',
          address: '',
          weekly_pattern: { service_minutes: 45 },
        },
      ],
    });
    mockPlaceAndFix
      .mockRejectedValueOnce(constraintError())
      .mockResolvedValueOnce({ visit: {}, fixed_visit: null });
    renderPanel();
    fireEvent.click(screen.getByTestId('course-day-tab-0'));

    await act(async () => {
      await dndState.capturedHandlers.onDragEnd!({
        active: {
          id: 'pool-patient:33333333-3333-4333-8333-333333333333',
          rect: { current: { translated: { top: 10 + (timeToY('09:30') ?? 0) } } },
        },
        over: { id: 'tl-col:tpl-A:0', rect: { top: 10 } },
      });
    });

    const dialog = await screen.findByTestId('constraint-override-confirm');
    expect(dialog).toHaveTextContent('それでも配置しますか？');
    expect(screen.getByTestId('constraint-override-ok')).toHaveTextContent('配置する');

    await act(async () => {
      fireEvent.click(screen.getByTestId('constraint-override-ok'));
    });
    await waitFor(() => expect(mockPlaceAndFix).toHaveBeenCalledTimes(2));
    expect(
      (mockPlaceAndFix.mock.calls[0][0] as Record<string, unknown>).acknowledge_constraint_warnings,
    ).toBeUndefined();
    expect(
      (mockPlaceAndFix.mock.calls[1][0] as Record<string, unknown>).acknowledge_constraint_warnings,
    ).toBe(true);
  });
});
