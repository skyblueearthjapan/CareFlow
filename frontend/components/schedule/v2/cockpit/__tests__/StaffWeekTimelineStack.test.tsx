/**
 * StaffWeekTimelineStack — 職員の「週・タイムライン」= 1 日の表 × 月〜土の 6 段
 * (設計 docs/plans/staff-week-timeline-stack-design-2026-11-01.md・PO 決定 2026-11-01 案 B)。
 *
 * - 6 段に、その曜日の訪問が 1 日の表と同じ帯 (帯の中に時刻・コース・患者名) で出る
 * - 職員の並びは 6 段で同じ (知らない担当・担当なしを含む)。休みはその曜日の段だけ
 * - ドラッグは段の中だけ (時刻・担当)。曜日は入れない。新人の行には落とせず案内を出す
 * - 全員の固定イベントは行に出さず「全員（固定）」帯だけ。一部の人だけの固定イベントは行に出す
 * - 別の曜日の段の行で離しても担当を変えない
 * - 件数の札と、数える帯 (取消・非稼働を除く) が合う
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';

const toastWarning = vi.fn();
vi.mock('sonner', () => ({ toast: { warning: (...a: unknown[]) => toastWarning(...a) } }));

import { countStaffDays } from '@/lib/schedule/staffDayCount';
import type { WeekOverrideRead } from '@/lib/queries/staff-overrides';
import {
  TRAINEE_DROP_MESSAGE,
  UNASSIGNED_ROW_KEY,
  type StaffTimelineRow,
  type TimelineEvent,
  type TimelineVisit,
  type VisitMovePayload,
} from '../StaffTimelineView';
import { StaffWeekTimelineStack } from '../StaffWeekTimelineStack';

const WEEK_START = new Date(2026, 10, 2); // 2026-11-02 (月)
const TODAY = '2026-11-01'; // 前の週の日曜 = 6 段とも明日以降
const TPL_A = '00000000-0000-4000-8000-0000000000aa';
const A = '00000000-0000-4000-8000-000000000001';
const B = '00000000-0000-4000-8000-000000000002';
const T = '00000000-0000-4000-8000-000000000003'; // 新人
const X = '00000000-0000-4000-8000-0000000000ff'; // staffRows に無い担当

/** 8:00〜19:00 = 660分。トラック幅 660px → 1px = 1分。 */
const TRACK_WIDTH = 660;

const rows: StaffTimelineRow[] = [
  { staffId: A, name: '職員A', office: '稲毛' },
  { staffId: B, name: '職員B', office: '都賀' },
  { staffId: T, name: '新人T', office: '稲毛', isTrainee: true },
];

function visit(partial: Partial<TimelineVisit> & { id: string }): TimelineVisit {
  return {
    patient_id: `p-${partial.id}`,
    patient_name: `患者${partial.id}`,
    weekday: 0,
    course_template_id: TPL_A,
    course_label: '稲毛A',
    start_time: '10:00',
    end_time: '10:30',
    patient_sex: 'male',
    primary_staff_id: A,
    status: 'planned',
    ...partial,
  } as TimelineVisit;
}

function firePointer(
  el: Element,
  type: 'pointerdown' | 'pointermove' | 'pointerup',
  clientX: number,
  clientY: number,
) {
  const ev = new Event(type, { bubbles: true, cancelable: true });
  Object.assign(ev, { clientX, clientY, pointerId: 1, button: 0, isPrimary: true });
  fireEvent(el, ev);
}

function pointAt(el: Element | null) {
  document.elementFromPoint = (() => el) as typeof document.elementFromPoint;
}

let originalRect: typeof HTMLElement.prototype.getBoundingClientRect;
let originalElementFromPoint: typeof document.elementFromPoint;

beforeEach(() => {
  toastWarning.mockReset();
  originalRect = HTMLElement.prototype.getBoundingClientRect;
  originalElementFromPoint = document.elementFromPoint;
  HTMLElement.prototype.getBoundingClientRect = function rect() {
    return {
      width: TRACK_WIDTH,
      height: 30,
      top: 0,
      left: 0,
      right: TRACK_WIDTH,
      bottom: 30,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect;
  };
  pointAt(null);
});

afterEach(() => {
  cleanup();
  HTMLElement.prototype.getBoundingClientRect = originalRect;
  document.elementFromPoint = originalElementFromPoint;
});

function renderStack(over: Partial<React.ComponentProps<typeof StaffWeekTimelineStack>> = {}) {
  const onVisitClick = vi.fn();
  const onVisitMove = vi.fn<[VisitMovePayload], void>();
  render(
    <StaffWeekTimelineStack
      weekStart={WEEK_START}
      staffRows={rows}
      visits={[
        visit({ id: 'mon1', weekday: 0, patient_name: '月曜花子' }),
        visit({
          id: 'wed1',
          weekday: 2,
          start_time: '13:00',
          end_time: '13:45',
          primary_staff_id: B,
        }),
      ]}
      todayIso={TODAY}
      canEdit
      onVisitClick={onVisitClick}
      onVisitMove={onVisitMove}
      {...over}
    />,
  );
  return { onVisitClick, onVisitMove };
}

const day = (wd: number) => within(screen.getByTestId(`swt-day-${wd}`));

describe('StaffWeekTimelineStack — 描画', () => {
  it('月〜土の 6 段に日付と曜日の見出しが出て、横スクロールは 1 つだけ', () => {
    renderStack();
    for (let wd = 0; wd < 6; wd += 1) {
      expect(screen.getByTestId(`swt-day-${wd}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId('swt-day-head-0')).toHaveTextContent('11/2（月）');
    expect(screen.getByTestId('swt-day-head-5')).toHaveTextContent('11/7（土）');
    expect(screen.getAllByTestId('swt-hscroll')).toHaveLength(1);
    expect(screen.getAllByTestId('staff-timeline-view-embedded')).toHaveLength(6);
    // 1 日の表の外枠 (自前の横スクロールつき) は出さない。
    expect(screen.queryByTestId('staff-timeline-view')).toBeNull();
    expect(screen.getByTestId('swt-jump-2')).toHaveTextContent('水 11/4');
  });

  it('その曜日の段に、帯の中の文字 (時刻・コース・患者名) で訪問が出る', () => {
    renderStack();
    const mon = day(0).getByTestId('tl-bar-mon1');
    expect(mon).toHaveTextContent('10:00〜10:30 稲毛A');
    expect(mon).toHaveTextContent('月曜花子');
    const wed = day(2).getByTestId('tl-bar-wed1');
    expect(wed).toHaveTextContent('13:00〜13:45 稲毛A');
    expect(wed).toHaveTextContent('患者wed1');
    // 別の曜日の段には出さない。
    expect(day(0).queryByTestId('tl-bar-wed1')).toBeNull();
    expect(day(2).queryByTestId('tl-bar-mon1')).toBeNull();
    // 帯は担当の行に入る。
    expect(day(2).getByTestId(`tl-lane-${B}`)).toContainElement(wed);
  });

  it('職員の並びは 6 段で同じ (知らない担当・担当なしを含む)。休みはその曜日の段だけ', () => {
    const off: WeekOverrideRead = { type: '休み' } as WeekOverrideRead;
    renderStack({
      visits: [visit({ id: 'x1', weekday: 3, primary_staff_id: X })],
      offByStaffWeekday: new Map([[`${A}:1`, off]]),
    });
    const order = (wd: number) =>
      Array.from(
        screen.getByTestId(`swt-day-${wd}`).querySelectorAll<HTMLElement>('[data-lane-key]'),
      ).map((el) => el.dataset.laneKey);
    const expected = [A, B, T, X, UNASSIGNED_ROW_KEY];
    for (let wd = 0; wd < 6; wd += 1) expect(order(wd)).toEqual(expected);
    // 知らない担当の行は入れ替え・行の操作の相手にしない。
    expect(day(0).getByTestId(`tl-name-${X}`)).toHaveTextContent('（不明なスタッフ）');
    // 休みは火曜の段だけ。
    expect(day(1).getAllByTestId(`tl-off-shade-${A}`).length).toBeGreaterThan(0);
    expect(day(0).queryByTestId(`tl-off-shade-${A}`)).toBeNull();
    expect(day(2).queryByTestId(`tl-off-shade-${A}`)).toBeNull();
  });

  it('固定の全員イベントは行に出さず、その曜日の「全員（固定）」帯だけ。その人だけのイベントは行に出す', () => {
    const ev = (over: Partial<TimelineEvent> & { id: string }): TimelineEvent =>
      ({
        staff_id: A,
        date: '2026-11-02',
        title: '朝会',
        start_time: '08:30',
        end_time: '08:45',
        type: 'イベント',
        blocking: false,
        ...over,
      }) as TimelineEvent;
    renderStack({
      events: new Map([
        [
          A,
          [
            ev({ id: 'fx-a', source: 'fixed' }),
            ev({
              id: 'own',
              source: 'manual',
              title: '会議',
              start_time: '15:00',
              end_time: '16:00',
            }),
          ],
        ],
        [
          B,
          [
            ev({ id: 'fx-b', staff_id: B, source: 'fixed' }),
            // 一部の人だけの固定イベント (火曜のカンファのような 1 人だけのもの)。
            ev({
              id: 'conf-b',
              staff_id: B,
              source: 'fixed',
              title: 'カンファor研修',
              start_time: '09:00',
              end_time: '10:00',
            }),
          ],
        ],
        [T, [ev({ id: 'fx-t', staff_id: T, source: 'fixed' })]],
      ]),
      fixedAllDayByDay: [[{ start: '08:30', end: '08:45', title: '朝会' }], [], [], [], [], []],
    });
    expect(day(0).getByTestId('tl-lane-fixed')).toHaveTextContent('朝会');
    expect(day(1).queryByTestId('tl-lane-fixed')).toBeNull();
    expect(screen.queryByTestId('tl-event-fx-a')).toBeNull();
    expect(screen.queryByTestId('tl-event-fx-b')).toBeNull();
    expect(screen.queryByTestId('tl-event-fx-t')).toBeNull();
    expect(day(0).getByTestId('tl-event-own')).toHaveTextContent('会議');
    expect(day(0).getByTestId('tl-event-conf-b')).toHaveTextContent('カンファor研修');
  });

  it('その日に休みの人がいても、残りの全員が持つ固定イベントは行から外す', () => {
    const ev = (staff: string, id: string): TimelineEvent =>
      ({
        id,
        staff_id: staff,
        date: '2026-11-03',
        title: '朝会',
        start_time: '08:30',
        end_time: '08:45',
        type: 'イベント',
        blocking: false,
        source: 'fixed',
      }) as TimelineEvent;
    renderStack({
      events: new Map([
        [A, [ev(A, 'tue-a')]],
        [B, [ev(B, 'tue-b')]],
      ]),
      // 火曜は新人T が休み → A・B の 2 人で全員。月曜には朝会がない。
      offByStaffWeekday: new Map([[`${T}:1`, { type: '休み' } as WeekOverrideRead]]),
    });
    expect(screen.queryByTestId('tl-event-tue-a')).toBeNull();
    expect(screen.queryByTestId('tl-event-tue-b')).toBeNull();
  });

  it('曜日の分からない印 (weekday なし) はどの段にも出さない', () => {
    renderStack({
      markers: [
        {
          action: 'add',
          externalId: 'mk-null',
          title: '曜日なし',
          start: '15:00',
          end: '15:30',
          staffId: A,
          weekday: null,
        },
      ],
    });
    expect(screen.queryAllByTestId(/^tl-marker-/)).toHaveLength(0);
  });

  it('件数の札は、描いた帯のうち数える帯 (取消・非稼働を除く) と合う', () => {
    const visits = [
      visit({ id: 'c1', weekday: 1, start_time: '09:00', end_time: '09:30' }),
      visit({ id: 'c2', weekday: 1, start_time: '11:00', end_time: '11:30' }),
      visit({ id: 'cx', weekday: 1, start_time: '13:00', end_time: '13:30', status: 'cancelled' }),
      visit({
        id: 'ci',
        weekday: 1,
        start_time: '15:00',
        end_time: '15:30',
        patient_status: 'admitted',
      }),
    ];
    renderStack({ visits, dayCounts: countStaffDays(visits, { today: TODAY }) });
    const lane = day(1).getByTestId(`tl-lane-${A}`);
    const bars = within(lane).getAllByTestId(/^tl-bar-/);
    expect(bars).toHaveLength(4);
    // 数えない帯は薄さで見分ける (取消 0.55・非稼働 0.6)。
    const counted = bars.filter((b) => b.style.opacity === '');
    expect(counted.map((b) => b.dataset.visitId).sort()).toEqual(['c1', 'c2']);
    expect(day(1).getByTestId(`staff-day-count-${A}-1`)).toHaveTextContent(`${counted.length}件`);
    expect(day(1).getByTestId('tl-inactive-badge-ci')).toBeInTheDocument();
    // ほかの曜日の札は 0 件。
    expect(day(0).getByTestId(`staff-day-count-${A}-0`)).toHaveTextContent('0件');
  });

  it('行の操作 (休み・＋訪問・＋イベント) を各段の行に出し、押すとその曜日で呼ぶ', () => {
    const onMarkOff = vi.fn();
    const onAddVisit = vi.fn();
    const onAddEvent = vi.fn();
    renderStack({ onMarkOff, onAddVisit, onAddEvent });
    fireEvent.click(day(3).getByTestId(`tl-off-action-${A}`));
    expect(onMarkOff.mock.calls[0]?.slice(0, 2)).toEqual([A, 3]);
    fireEvent.click(day(4).getByTestId(`tl-add-visit-${UNASSIGNED_ROW_KEY}`));
    expect(onAddVisit.mock.calls[0]?.slice(0, 2)).toEqual([null, 4]);
    fireEvent.click(day(5).getByTestId(`tl-add-event-${B}`));
    expect(onAddEvent).toHaveBeenCalledWith(B, 5);
  });

  it('今日を含む過去の段の帯はつかめない', () => {
    renderStack({ todayIso: '2026-11-02' });
    expect(day(0).getByTestId('tl-bar-mon1')).toHaveAttribute('aria-disabled', 'true');
    expect(day(2).getByTestId('tl-bar-wed1')).not.toHaveAttribute('aria-disabled');
  });
});

describe('StaffWeekTimelineStack — 段の中のドラッグ', () => {
  it('横に動かすと toStart だけ (15 分刻み・曜日は入れない)', () => {
    const { onVisitMove } = renderStack();
    const bar = day(2).getByTestId('tl-bar-wed1');
    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 140, 12);
    firePointer(bar, 'pointerup', 140, 12);
    expect(onVisitMove).toHaveBeenCalledTimes(1);
    const payload = onVisitMove.mock.calls[0]![0];
    expect(payload).toMatchObject({ fromStart: '13:00', toStart: '13:45' });
    expect(payload.visit.id).toBe('wed1');
    expect('toStaffId' in payload).toBe(false);
    expect(payload.toWeekday).toBeUndefined();
  });

  it('同じ段の別の行へ → toStaffId (担当なしの行へは null)', () => {
    const { onVisitMove } = renderStack();
    const bar = day(2).getByTestId('tl-bar-wed1');
    pointAt(day(2).getByTestId(`tl-lane-${A}`));
    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 100, 80);
    firePointer(bar, 'pointerup', 100, 80);
    expect(onVisitMove.mock.calls[0]![0]).toMatchObject({ toStart: null, toStaffId: A });
    expect(onVisitMove.mock.calls[0]![0].toWeekday).toBeUndefined();

    const bar2 = day(0).getByTestId('tl-bar-mon1');
    pointAt(day(0).getByTestId(`tl-lane-${UNASSIGNED_ROW_KEY}`));
    firePointer(bar2, 'pointerdown', 100, 10);
    firePointer(bar2, 'pointermove', 100, 200);
    firePointer(bar2, 'pointerup', 100, 200);
    expect(onVisitMove.mock.calls[1]![0]).toMatchObject({ toStaffId: null });
  });

  it('別の曜日の段の行の上で離しても担当を変えない (move を呼ばない)', () => {
    const { onVisitMove } = renderStack();
    const bar = day(2).getByTestId('tl-bar-wed1');
    // 水曜の帯を、月曜の段の職員A の行の上で離す。
    pointAt(day(0).getByTestId(`tl-lane-${A}`));
    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 100, 300);
    firePointer(bar, 'pointerup', 100, 300);
    expect(onVisitMove).not.toHaveBeenCalled();
  });

  it('別の曜日の段の行の上で、時刻だけ動かしたときは担当を入れない', () => {
    const { onVisitMove } = renderStack();
    const bar = day(2).getByTestId('tl-bar-wed1');
    pointAt(day(0).getByTestId(`tl-lane-${A}`));
    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 130, 300);
    firePointer(bar, 'pointerup', 130, 300);
    expect(onVisitMove).toHaveBeenCalledTimes(1);
    const payload = onVisitMove.mock.calls[0]![0];
    expect(payload.toStart).toBe('13:30');
    expect('toStaffId' in payload).toBe(false);
  });

  it('別の曜日の段の新人の行の上では、案内を出さず何も変えない', () => {
    const { onVisitMove } = renderStack();
    const bar = day(2).getByTestId('tl-bar-wed1');
    pointAt(day(0).getByTestId(`tl-lane-${T}`));
    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 100, 300);
    expect(screen.queryByTestId(`tl-trainee-block-${T}`)).toBeNull();
    firePointer(bar, 'pointerup', 100, 300);
    expect(onVisitMove).not.toHaveBeenCalled();
    expect(toastWarning).not.toHaveBeenCalled();
  });

  it('動かさずに離すとメニュー (onVisitClick)・move は呼ばない', () => {
    const { onVisitClick, onVisitMove } = renderStack();
    const bar = day(2).getByTestId('tl-bar-wed1');
    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointerup', 100, 10);
    fireEvent.click(bar);
    expect(onVisitMove).not.toHaveBeenCalled();
    expect(onVisitClick).toHaveBeenCalledTimes(1);
    expect(onVisitClick.mock.calls[0]![0].id).toBe('wed1');
  });

  it('新人の行には落とせず、案内を出す (move は呼ばない)', () => {
    const { onVisitMove } = renderStack();
    const bar = day(2).getByTestId('tl-bar-wed1');
    pointAt(day(2).getByTestId(`tl-lane-${T}`));
    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 130, 120);
    expect(day(2).getByTestId(`tl-trainee-block-${T}`)).toHaveTextContent(TRAINEE_DROP_MESSAGE);
    firePointer(bar, 'pointerup', 130, 120);
    expect(onVisitMove).not.toHaveBeenCalled();
    expect(toastWarning).toHaveBeenCalledWith(TRAINEE_DROP_MESSAGE);
    expect(screen.queryByTestId(`tl-trainee-block-${T}`)).toBeNull();
  });

  it('あとから渡し直した手 (onVisitMove) で呼ぶ (段の memo で古い手を握らない)', () => {
    const first = vi.fn();
    const second = vi.fn();
    const props = {
      weekStart: WEEK_START,
      staffRows: rows,
      visits: [visit({ id: 'wed1', weekday: 2, start_time: '13:00', end_time: '13:45' })],
      todayIso: TODAY,
      canEdit: true,
      onVisitClick: vi.fn(),
    };
    const { rerender } = render(<StaffWeekTimelineStack {...props} onVisitMove={first} />);
    rerender(<StaffWeekTimelineStack {...props} onVisitMove={second} />);
    const bar = day(2).getByTestId('tl-bar-wed1');
    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 160, 10);
    firePointer(bar, 'pointerup', 160, 10);
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
