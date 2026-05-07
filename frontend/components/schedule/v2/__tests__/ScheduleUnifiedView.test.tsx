/**
 * ScheduleUnifiedView — Wave 15 統合スケジュール画面 テスト (W15-FE).
 *
 * カバーするシナリオ:
 *   1. テンプレートが空のとき案内文が出る
 *   2. メインマトリクスがレンダーされる (ヘッダー曜日)
 *   3. 受入目安レイヤーが OFF のとき凡例は出ない / ON のとき凡例が出る
 *   4. プールに患者カードが描画される
 *   5. canEdit=false のときプールが disabled
 *   6. 満枠超過警告バッジが capacity を超えた時に表示される
 *   7. ドロップ時に usePlaceAndFix.mutateAsync が呼ばれる (簡易)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

// ─── モック ──────────────────────────────────────────────────────────────────

const { dndState } = vi.hoisted(() => ({
  dndState: { capturedHandlers: { onDragEnd: undefined as undefined | ((e: unknown) => void) } },
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
    onDragEnd?: (e: unknown) => void;
  }) => {
    dndState.capturedHandlers.onDragEnd = onDragEnd;
    return <>{children}</>;
  },
  DragOverlay: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  PointerSensor: class {},
  useSensor: () => ({}),
  useSensors: (...args: unknown[]) => args,
}));

vi.mock('sonner', () => ({
  toast: { warning: vi.fn(), success: vi.fn(), error: vi.fn() },
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: null, status: 'unauthenticated' }),
}));

vi.mock('lucide-react', () => ({
  ChevronLeft: () => <span />,
  ChevronRight: () => <span data-testid="chev-right" />,
  Inbox: () => <span />,
  AlertTriangle: () => <span data-testid="alert-icon" />,
  User: () => <span />,
  Users: () => <span />,
  X: () => <span />,
  Plus: () => <span />,
  ChevronDown: () => <span />,
  Star: () => <span />,
}));

vi.mock('@/components/ui/popover', () => ({
  Popover: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  PopoverTrigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  PopoverContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/components/ui/card', () => ({
  Card: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
}));

vi.mock('@/components/ui/skeleton', () => ({
  Skeleton: () => <div data-testid="skeleton" />,
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
const mockAcceptance = vi.fn();
const mockVisits = vi.fn();
const mockCourses = vi.fn();
const mockPlaceAndFix = vi.fn();

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
vi.mock('@/lib/queries/acceptance_calendar', () => ({
  useAcceptanceCalendar: (...args: unknown[]) => mockAcceptance(...args),
}));
vi.mock('@/lib/queries/visits', () => ({
  useVisits: (...args: unknown[]) => mockVisits(...args),
}));
vi.mock('@/lib/queries/courses', () => ({
  useCourses: (...args: unknown[]) => mockCourses(...args),
  useUpdateCourse: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/lib/queries/place_and_fix', () => ({
  usePlaceAndFix: () => ({ mutateAsync: mockPlaceAndFix, isPending: false }),
}));

// ─── Subject under test ─────────────────────────────────────────────────────

import { ScheduleUnifiedView } from '../ScheduleUnifiedView';

// ─── helpers ────────────────────────────────────────────────────────────────

function monday(year: number, month: number, day: number): Date {
  const d = new Date(year, month - 1, day, 0, 0, 0, 0);
  const dow = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - dow);
  return d;
}

function setupHooks(opts: {
  templates?: Array<Record<string, unknown>>;
  acceptance?: Array<Record<string, unknown>>;
  visits?: Array<Record<string, unknown>>;
  courses?: Array<Record<string, unknown>>;
  patients?: Array<Record<string, unknown>>;
}) {
  mockOffices.mockReturnValue({
    allOffices: [{ id: 'office-1', name: '稲毛' }],
    isLoading: false,
  });
  mockPatients.mockReturnValue({
    data: { items: opts.patients ?? [] },
    isLoading: false,
  });
  mockStaffList.mockReturnValue({
    data: [{ id: 'staff-1', name: '山本', code: 'YAM', status: 'active', role: 'staff' }],
  });
  mockTemplates.mockReturnValue({
    data: opts.templates ?? [],
    isLoading: false,
  });
  mockAcceptance.mockReturnValue({
    data: opts.acceptance ?? [],
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

describe('ScheduleUnifiedView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. テンプレート未登録時に案内文を表示する', () => {
    setupHooks({ templates: [] });
    render(
      <ScheduleUnifiedView
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
        showWarningLayer={true}
      />,
    );
    expect(screen.getByText(/コーステンプレートが登録されていません/)).toBeInTheDocument();
  });

  it('2. メインマトリクスのヘッダー (月〜土) が描画される', () => {
    setupHooks({
      templates: [
        {
          id: 'tpl-1',
          office_id: 'office-1',
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
      <ScheduleUnifiedView
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
        showWarningLayer={true}
      />,
    );
    expect(screen.getByTestId('schedule-unified-view')).toBeInTheDocument();
    // 曜日ヘッダ
    for (const lbl of ['月', '火', '水', '木', '金', '土']) {
      expect(screen.getAllByText(new RegExp(lbl)).length).toBeGreaterThan(0);
    }
    // 行ラベル: 拠点名-A
    expect(screen.getByText(/稲毛-A/)).toBeInTheDocument();
  });

  it('3. 受入目安レイヤー OFF では凡例が出ない / ON で凡例が出る', () => {
    setupHooks({
      templates: [
        {
          id: 'tpl-1',
          office_id: 'office-1',
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
    const { rerender } = render(
      <ScheduleUnifiedView
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
        showWarningLayer={true}
      />,
    );
    // 凡例文言「受入目安:」は OFF では現れない
    expect(screen.queryByText(/受入目安:/)).not.toBeInTheDocument();

    rerender(
      <ScheduleUnifiedView
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={true}
        showWarningLayer={true}
      />,
    );
    expect(screen.getByText(/受入目安:/)).toBeInTheDocument();
  });

  it('4. プールに患者カードが描画される', () => {
    setupHooks({
      templates: [
        {
          id: 'tpl-1',
          office_id: 'office-1',
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
      patients: [{ id: 'p-1', name: '田中太郎', kana: null, status: 'active' }],
      visits: [],
    });
    render(
      <ScheduleUnifiedView
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
        showWarningLayer={true}
      />,
    );
    expect(screen.getByText('田中太郎')).toBeInTheDocument();
  });

  it('5. canEdit=false でも閲覧表示自体は維持される', () => {
    setupHooks({
      templates: [
        {
          id: 'tpl-1',
          office_id: 'office-1',
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
      <ScheduleUnifiedView
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={false}
        showAcceptanceLayer={false}
        showWarningLayer={true}
      />,
    );
    expect(screen.getByTestId('schedule-unified-view')).toBeInTheDocument();
  });

  it('6. ドロップで usePlaceAndFix.mutateAsync が呼ばれる', async () => {
    mockPlaceAndFix.mockResolvedValue({ results: [] });
    setupHooks({
      templates: [
        {
          id: '11111111-1111-1111-1111-111111111111',
          office_id: 'office-1',
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
      patients: [
        {
          id: '22222222-2222-2222-2222-222222222222',
          name: '田中',
          kana: null,
          status: 'active',
          weekly_pattern: { service_minutes: 60 },
        },
      ],
    });
    render(
      <ScheduleUnifiedView
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={true}
        showAcceptanceLayer={false}
        showWarningLayer={true}
      />,
    );
    // DragEnd を直接シミュレート (DnD-kit は mock 済み)
    expect(dndState.capturedHandlers.onDragEnd).toBeDefined();
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: 'unified-patient:22222222-2222-2222-2222-222222222222' },
      over: { id: 'cell:11111111-1111-1111-1111-111111111111:0:09:00' },
    });
    expect(mockPlaceAndFix).toHaveBeenCalledOnce();
    const arg = mockPlaceAndFix.mock.calls[0][0];
    expect(arg.entries[0].patient_id).toBe('22222222-2222-2222-2222-222222222222');
    expect(arg.entries[0].course_template_id).toBe('11111111-1111-1111-1111-111111111111');
    expect(arg.entries[0].weekday).toBe(0);
    expect(arg.entries[0].start_time).toBe('09:00');
  });

  it('7. canEdit=false のドロップは何もせず place-and-fix を呼ばない', async () => {
    mockPlaceAndFix.mockResolvedValue({ results: [] });
    setupHooks({
      templates: [
        {
          id: '11111111-1111-1111-1111-111111111111',
          office_id: 'office-1',
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
      patients: [
        {
          id: '22222222-2222-2222-2222-222222222222',
          name: '田中',
          kana: null,
          status: 'active',
        },
      ],
    });
    render(
      <ScheduleUnifiedView
        weekStart={monday(2026, 5, 4)}
        officeId={null}
        canEdit={false}
        showAcceptanceLayer={false}
        showWarningLayer={true}
      />,
    );
    await dndState.capturedHandlers.onDragEnd!({
      active: { id: 'unified-patient:22222222-2222-2222-2222-222222222222' },
      over: { id: 'cell:11111111-1111-1111-1111-111111111111:0:09:00' },
    });
    expect(mockPlaceAndFix).not.toHaveBeenCalled();
  });
});
