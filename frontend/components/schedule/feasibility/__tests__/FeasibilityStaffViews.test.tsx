/**
 * 実現性の印を、職員の表示とコースの 1 日の表示にも出す
 * (設計 docs/plans/schedule-feasibility-alerts-design-2026-11-01.md・PO 2026-11-01: 普段は職員の表示)。
 *
 * - 職員・その日のタイムライン (StaffTimelineView) のバー
 * - 職員・週のリスト (StaffWeekBoard) の訪問の行
 * - 職員・週のタイムライン (StaffWeekTimeline) の細い帯 (枠の色と title)
 * - コースの 1 日の時間軸 (TimelineDayBoard) のカード
 * - 帯の行は、盤面の持ち主が引き受けたら自分では探さない
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { FeasibilityMarksResponse } from '@/lib/queries/feasibility';
import type { WeekOverviewVisit } from '@/components/schedule/v2/CourseWeekOverview';
import type { CourseGridVisit } from '@/components/schedule/v2/courseGrid';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { StaffRead } from '@/lib/schemas/staff';

const marksState: { data: FeasibilityMarksResponse | undefined } = { data: undefined };

vi.mock('@/lib/queries/feasibility', () => ({
  useFeasibilityMarks: (p: { enabled?: boolean }) => ({
    data: p.enabled === false ? undefined : marksState.data,
  }),
}));

const toastInfo = vi.fn();
vi.mock('sonner', () => ({ toast: { info: (...a: unknown[]) => toastInfo(...a) } }));

import {
  FEASIBILITY_FOCUS_EVENT,
  FeasibilityBand,
} from '@/components/schedule/feasibility/FeasibilityBand';
import { FeasibilityMarksProvider } from '@/components/schedule/feasibility/FeasibilityMark';
import {
  StaffTimelineView,
  type TimelineVisit,
} from '@/components/schedule/v2/cockpit/StaffTimelineView';
import { StaffWeekTimeline } from '@/components/schedule/v2/cockpit/StaffWeekTimeline';
import { StaffWeekBoard } from '@/components/schedule/v2/StaffWeekBoard';
import {
  TimelineDayBoard,
  type TimelineCourseColumn,
} from '@/components/schedule/timeline/TimelineDayBoard';

const WEEK_START = new Date(2026, 10, 2); // 2026-11-02 (月)
const OFFICE_ID = '00000000-0000-4000-8000-00000000000f';
const TPL_A = '00000000-0000-4000-8000-0000000000aa';
const STAFF_1 = '00000000-0000-4000-8000-000000000001';

const REASON_HARD = '前の予定から 5 分しかありません。移動に約 17 分かかります。';
const REASON_SOFT = '前の予定から 12 分です。';

function response(): FeasibilityMarksResponse {
  return {
    dateFrom: '2026-11-02',
    dateTo: '2026-11-08',
    hardCount: 1,
    softCount: 1,
    unassignedCount: 0,
    items: [
      {
        tier: 'hard',
        kind: '移動不可',
        day: '2026-11-02',
        at: '10:40',
        staff: '看護X',
        patientName: '乙川 二郎',
        reason: REASON_HARD,
        visitIds: ['a'],
      },
    ],
    marks: {
      a: [{ tier: 'hard', kind: '移動不可', reason: REASON_HARD }],
      s: [{ tier: 'soft', kind: 'バッファ不足', reason: REASON_SOFT }],
    },
  };
}

function withProvider(children: React.ReactNode, enabled = true) {
  return (
    <FeasibilityMarksProvider dateFrom="2026-11-02" dateTo="2026-11-08" enabled={enabled}>
      {children}
    </FeasibilityMarksProvider>
  );
}

function tv(over: Partial<TimelineVisit> & { id: string }): TimelineVisit {
  return {
    patient_id: `p-${over.id}`,
    patient_name: `患者${over.id}`,
    weekday: 0,
    course_template_id: TPL_A,
    start_time: '10:00',
    end_time: '10:30',
    patient_sex: 'male',
    primary_staff_id: STAFF_1,
    ...over,
  } as TimelineVisit;
}

const TL_VISITS: TimelineVisit[] = [
  tv({ id: 'a', start_time: '10:40', end_time: '11:15' }),
  tv({ id: 's', start_time: '13:00', end_time: '13:30' }),
  tv({ id: 'n', start_time: '15:00', end_time: '15:30' }),
];

beforeEach(() => {
  marksState.data = response();
  toastInfo.mockReset();
});

describe('職員・その日のタイムライン (StaffTimelineView)', () => {
  function renderView(enabled = true) {
    const onVisitClick = vi.fn();
    const onVisitMove = vi.fn();
    render(
      withProvider(
        <StaffTimelineView
          weekStart={WEEK_START}
          day={0}
          staffRows={[{ staffId: STAFF_1, name: '職員D　秋子', office: '稲毛' }]}
          visits={TL_VISITS}
          canEdit
          onVisitClick={onVisitClick}
          onVisitMove={onVisitMove}
        />,
        enabled,
      ),
    );
    return { onVisitClick, onVisitMove };
  }

  it('印の付いた訪問のバーにだけ赤・黄の印が出て、押すと理由が出る', () => {
    renderView();
    expect(screen.getByTestId('tl-feas-mark-a')).toHaveAttribute('data-tier', 'hard');
    expect(screen.getByTestId('tl-feas-mark-a')).toHaveTextContent('！');
    expect(screen.getByTestId('tl-feas-mark-s')).toHaveAttribute('data-tier', 'soft');
    expect(screen.getByTestId('tl-feas-mark-s')).toHaveTextContent('△');
    expect(screen.queryByTestId('tl-feas-mark-n')).toBeNull();
    // 印はバー (<button>) の中に入れない (ボタンの入れ子にしない)
    expect(within(screen.getByTestId('tl-bar-a')).queryByTestId('tl-feas-mark-a')).toBeNull();
    fireEvent.click(screen.getByTestId('tl-feas-mark-a'));
    expect(
      within(screen.getByTestId('tl-feas-mark-a-reasons')).getByText(REASON_HARD),
    ).toBeVisible();
  });

  it('印を押してもバーのメニュー (クリック) は開かない', () => {
    const { onVisitClick, onVisitMove } = renderView();
    const mark = screen.getByTestId('tl-feas-mark-a');
    fireEvent.pointerDown(mark);
    fireEvent.click(mark);
    expect(onVisitClick).not.toHaveBeenCalled();
    expect(onVisitMove).not.toHaveBeenCalled();
  });

  it('管理者以外 (enabled=false) では印が出ない', () => {
    renderView(false);
    expect(screen.queryByTestId('tl-feas-mark-a')).toBeNull();
  });
});

describe('職員・週のリスト (StaffWeekBoard)', () => {
  function wv(over: Partial<WeekOverviewVisit> & { id: string }): WeekOverviewVisit {
    return {
      patient_id: `p-${over.id}`,
      patient_name: `患者${over.id}`,
      weekday: 0,
      course_template_id: TPL_A,
      start_time: '10:00',
      end_time: '10:35',
      ...over,
    } as WeekOverviewVisit;
  }

  it('訪問の行に赤・黄の印が出る (印の無い訪問には出ない)', () => {
    render(
      withProvider(
        <StaffWeekBoard
          templates={
            [{ id: TPL_A, office_id: OFFICE_ID, label: 'A' }] as unknown as CourseTemplateRead[]
          }
          officeNameById={new Map([[OFFICE_ID, '稲毛']])}
          visits={[
            wv({ id: 'a', start_time: '10:40' }),
            wv({ id: 's', start_time: '13:00' }),
            wv({ id: 'n', start_time: '15:00' }),
          ]}
          assignedStaffByTemplateWeekday={new Map([[`${TPL_A}:0`, STAFF_1]])}
          staffMap={
            new Map([
              [
                STAFF_1,
                {
                  id: STAFF_1,
                  name: '職員D　秋子',
                  primary_office_id: OFFICE_ID,
                  is_trainee: false,
                  status: 'active',
                } as unknown as StaffRead,
              ],
            ])
          }
          staffEventsByStaff={new Map()}
          weekStart={WEEK_START}
        />,
      ),
    );
    const rowA = screen.getByTestId('staff-week-visit-a');
    expect(within(rowA).getByTestId('staff-week-feas-mark-a')).toHaveAttribute('data-tier', 'hard');
    expect(screen.getByTestId('staff-week-feas-mark-s')).toHaveAttribute('data-tier', 'soft');
    expect(screen.queryByTestId('staff-week-feas-mark-n')).toBeNull();
    fireEvent.click(screen.getByTestId('staff-week-feas-mark-s'));
    expect(screen.getByTestId('staff-week-feas-mark-s-reasons')).toHaveTextContent(REASON_SOFT);
  });
});

describe('職員・週のタイムライン (StaffWeekTimeline)', () => {
  it('細い帯は枠の色で分け、title に理由を添える', () => {
    render(
      withProvider(
        <StaffWeekTimeline
          weekStart={WEEK_START}
          staffRows={[{ staffId: STAFF_1, name: '職員D　秋子', office: '稲毛' }]}
          visits={TL_VISITS}
          todayIso="2026-11-01"
          canEdit
          onVisitMove={vi.fn()}
          onVisitClick={vi.fn()}
        />,
      ),
    );
    const a = screen.getByTestId('swt-visit-a');
    expect(a).toHaveAttribute('data-feas-tier', 'hard');
    expect(a).toHaveClass('ring-error');
    expect(a.getAttribute('title')).toContain(REASON_HARD);
    expect(screen.getByTestId('swt-visit-s')).toHaveClass('ring-warning');
    expect(screen.getByTestId('swt-visit-n')).not.toHaveAttribute('data-feas-tier');
  });
});

describe('コースの 1 日の時間軸 (TimelineDayBoard)', () => {
  function cv(over: Partial<CourseGridVisit> & { id: string }): CourseGridVisit {
    return {
      patient_id: `p-${over.id}`,
      patient_name: `患者${over.id}`,
      patient_address: null,
      patient_requires_multiple_staff: false,
      patient_sex_restriction_label: null,
      required_staff_count: 1,
      start_slot: '09:30',
      start_time: '10:25',
      end_time: '11:00',
      ...over,
    } as CourseGridVisit;
  }
  function column(visits: CourseGridVisit[]): TimelineCourseColumn {
    return {
      template: { id: 't1', office_id: 'o1', label: 'A' } as TimelineCourseColumn['template'],
      course: { id: 'c1', assigned_staff_id: 's1' } as TimelineCourseColumn['course'],
      officeName: '稲毛',
      visits,
      assignedStaff: {
        id: 's1',
        name: '田中 一郎',
        sex: 'male',
      } as TimelineCourseColumn['assignedStaff'],
      freeGaps: [],
      capacity: { filled: visits.length, max: 6 },
      staffEvents: [],
      staffOptions: [],
      key: 'col-1',
    } as TimelineCourseColumn;
  }

  it('カードに印が出て、画鋲のカードでは画鋲の左へ置く・押しても詳細を開かない', () => {
    const onPatientClick = vi.fn();
    render(
      withProvider(
        <TimelineDayBoard
          columns={[
            column([
              cv({ id: 'a', start_time: '10:25', end_time: '11:00' }),
              cv({ id: 's', start_time: '13:00', end_time: '13:35', movability: 'locked' }),
              cv({ id: 'n', start_time: '15:00', end_time: '15:35' }),
            ]),
          ]}
          onPatientClick={onPatientClick}
        />,
      ),
    );
    expect(screen.getByTestId('tl-feas-mark-a')).toHaveAttribute('data-tier', 'hard');
    expect(screen.getByTestId('tl-feas-mark-a')).toHaveClass('-right-1');
    expect(screen.getByTestId('tl-feas-mark-s')).toHaveClass('right-[16px]');
    expect(screen.queryByTestId('tl-feas-mark-n')).toBeNull();
    fireEvent.click(screen.getByTestId('tl-feas-mark-a'));
    expect(onPatientClick).not.toHaveBeenCalled();
  });
});

describe('帯の行と盤面の持ち主', () => {
  let handler: ((ev: Event) => void) | null = null;
  afterEach(() => {
    if (handler) window.removeEventListener(FEASIBILITY_FOCUS_EVENT, handler);
    handler = null;
  });

  it('持ち主が引き受けたら (preventDefault)、帯は自分で探さず案内も出さない', () => {
    const seen: unknown[] = [];
    handler = (ev: Event) => {
      seen.push((ev as CustomEvent).detail);
      ev.preventDefault();
    };
    window.addEventListener(FEASIBILITY_FOCUS_EVENT, handler);
    render(withProvider(<FeasibilityBand scopeLabel="この週" cardTestIdPrefixes={['tl-bar-']} />));
    fireEvent.click(screen.getByTestId('feasibility-band-toggle'));
    fireEvent.click(screen.getByTestId('feasibility-band-item-0'));
    expect(seen).toEqual([{ visitIds: ['a'], day: '2026-11-02' }]);
    expect(toastInfo).not.toHaveBeenCalled();
  });

  it('誰も引き受けず、カードも無ければ案内を出す', () => {
    render(
      withProvider(
        <FeasibilityBand
          scopeLabel="この週"
          cardTestIdPrefixes={['tl-bar-']}
          notFoundMessage="職員・その日のタイムラインで見られます"
        />,
      ),
    );
    fireEvent.click(screen.getByTestId('feasibility-band-toggle'));
    fireEvent.click(screen.getByTestId('feasibility-band-item-0'));
    expect(toastInfo).toHaveBeenCalledWith('職員・その日のタイムラインで見られます');
  });
});
