/**
 * StaffWeekTimeline — 職員の「週・タイムライン」(PO 決定 2026-10-21) の描画とドラッグの契約。
 * 職員の名前は 職員A〜 (実名を書かない)。
 */
import * as React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createEvent, fireEvent, render, screen } from '@testing-library/react';

import { SWT_VISIT_DND_MIME, UNASSIGNED_ROW_KEY } from '../../courseDnd';
import { dropXToStartMinutes, StaffWeekTimeline } from '../StaffWeekTimeline';
import type { StaffTimelineRow, TimelineVisit, VisitMovePayload } from '../StaffTimelineView';
import type { WeekOverrideRead } from '@/lib/queries/staff-overrides';

const WEEK_START = new Date(2026, 7, 17); // 2026-08-17 (月)
const TODAY = '2026-08-17'; // 月曜 = 今日 (今日を含む過去は動かせない)
const TPL = '00000000-0000-4000-8000-0000000000aa';
const A = '00000000-0000-4000-8000-00000000000a';
const B = '00000000-0000-4000-8000-00000000000b';
const T = '00000000-0000-4000-8000-00000000000c'; // 新人

/** 8:00〜19:00 = 660 分。マスの幅 660px → 1px = 1 分。 */
const TRACK = 660;

const rows: StaffTimelineRow[] = [
  { staffId: A, name: '職員A', office: '本店' },
  { staffId: B, name: '職員B', office: '本店' },
  { staffId: T, name: '職員C', office: '本店', isTrainee: true },
];

function visit(over: Partial<TimelineVisit> & { id: string }): TimelineVisit {
  return {
    patient_id: `p-${over.id}`,
    patient_name: '利用者A',
    weekday: 1,
    course_template_id: TPL,
    start_time: '10:00',
    end_time: '10:30',
    patient_sex: 'female',
    primary_staff_id: A,
    course_label: '本店A',
    ...over,
  } as TimelineVisit;
}

let originalRect: typeof HTMLElement.prototype.getBoundingClientRect;
beforeEach(() => {
  originalRect = HTMLElement.prototype.getBoundingClientRect;
  HTMLElement.prototype.getBoundingClientRect = function rect() {
    return {
      width: TRACK,
      height: 20,
      top: 0,
      left: 0,
      right: TRACK,
      bottom: 20,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect;
  };
});
afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = originalRect;
});

function makeDataTransfer() {
  const store = new Map<string, string>();
  return {
    effectAllowed: 'none',
    dropEffect: 'none',
    get types() {
      return [...store.keys()];
    },
    setData: (type: string, value: string) => void store.set(type, value),
    getData: (type: string) => store.get(type) ?? '',
  };
}

function renderView(over: Partial<React.ComponentProps<typeof StaffWeekTimeline>> = {}) {
  const onVisitMove = vi.fn<[VisitMovePayload], void>();
  const onVisitClick = vi.fn();
  render(
    <StaffWeekTimeline
      weekStart={WEEK_START}
      staffRows={rows}
      visits={[visit({ id: 'v1' }), visit({ id: 'v0', weekday: 0, start_time: '09:00' })]}
      todayIso={TODAY}
      canEdit
      onVisitMove={onVisitMove}
      onVisitClick={onVisitClick}
      {...over}
    />,
  );
  return { onVisitMove, onVisitClick };
}

/** 帯 (つかむ位置 = 帯の左端) を、マスの左端から x px の所へ落とす。 */
function dragTo(visitId: string, rowKey: string, wd: number, x: number) {
  const dt = makeDataTransfer();
  // jsdom の DragEvent は clientX を持たないので、イベントに直接載せる。
  const fire = (el: Element, kind: 'dragStart' | 'dragOver' | 'drop', clientX: number) => {
    const ev = createEvent[kind](el, { dataTransfer: dt });
    Object.defineProperty(ev, 'clientX', { value: clientX });
    fireEvent(el, ev);
  };
  fire(screen.getByTestId(`swt-visit-${visitId}`), 'dragStart', 0);
  const cell = screen.getByTestId(`swt-cell-${rowKey}-${wd}`);
  fire(cell, 'dragOver', x);
  fire(cell, 'drop', x);
}

describe('StaffWeekTimeline — 描画', () => {
  it('行は職員＋担当なし・列は月〜土・目盛り (9・12・15・18) は見出しに 1 回だけ', () => {
    renderView();
    expect(screen.getByTestId(`swt-name-${A}`)).toHaveTextContent('職員A');
    expect(screen.getByTestId(`swt-name-${UNASSIGNED_ROW_KEY}`)).toHaveTextContent('（担当なし）');
    for (let wd = 0; wd < 6; wd += 1) {
      expect(screen.getByTestId(`swt-cell-${A}-${wd}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId('swt-head-0')).toHaveTextContent('9121518');
    expect(screen.getByTestId('swt-grid').style.gridTemplateColumns).toBe(
      '200px repeat(6, minmax(120px, 1fr))',
    );
    // 今日のマスと見出しは薄く色を付ける。
    expect(screen.getByTestId('swt-head-0').className).toContain('bg-brand-primary-50');
  });

  it('帯の位置と幅は所要時間どおり・title に「時刻・患者名・コース」', () => {
    renderView();
    const bar = screen.getByTestId('swt-visit-v1');
    // 10:00 = 8:00 から 120 分 / 660 分。30 分の長さ。
    expect(parseFloat(bar.style.left)).toBeCloseTo((120 / 660) * 100, 3);
    expect(parseFloat(bar.style.width)).toBeCloseTo((30 / 660) * 100, 3);
    expect(bar.title).toBe('10:00〜10:30 利用者A 本店A');
  });

  it('休みは働かない時間だけ斜線・終日休みはマス全体', () => {
    const off = (wd: number, type: WeekOverrideRead['type']): WeekOverrideRead => ({
      id: `o${wd}`,
      staff_id: A,
      date: `2026-08-${17 + wd}`,
      weekday: wd,
      type,
      start_time: null,
      end_time: null,
      note: null,
    });
    renderView({
      offByStaffWeekday: new Map([
        [`${A}:1`, off(1, '午前休')],
        [`${A}:2`, off(2, '休み')],
      ]),
    });
    const am = screen.getByTestId(`swt-off-shade-${A}-1`);
    expect(am.style.left).toBe('0%');
    expect(parseFloat(am.style.width)).toBeCloseTo((300 / 660) * 100, 2);
    const full = screen.getByTestId(`swt-off-shade-${A}-2`);
    expect(full.style.width).toBe('100%');
    expect(screen.getByTestId(`swt-off-${A}-1`)).toHaveTextContent('火 午前休');
  });

  it('マスの角に件数・氏名の下に「週 N件」・同行は薄い帯 (動かせない)', () => {
    const v = visit({ id: 'v1' });
    renderView({
      visits: [v],
      dayCounts: new Map([[`${A}:1`, { visits: 6, patients: 6, companions: 0 }]]),
      countLimit: 6,
      companionVisitsByCell: new Map([[`${B}:1`, [v]]]),
    });
    expect(screen.getByTestId(`staff-day-count-${A}-1`)).toHaveTextContent('6件 満杯');
    expect(screen.getByTestId(`swt-week-total-${A}`)).toHaveTextContent('週 6件');
    const acc = screen.getByTestId(`swt-companion-${B}-v1`);
    expect(acc.className).toContain('pointer-events-none');
    expect(acc.getAttribute('draggable')).toBeNull();
  });
});

describe('StaffWeekTimeline — ドラッグ', () => {
  it('同じ職員の別の時刻: toStart だけ', () => {
    const { onVisitMove } = renderView();
    dragTo('v1', A, 1, 180); // 8:00 + 180 分 = 11:00
    expect(onVisitMove).toHaveBeenCalledOnce();
    const p = onVisitMove.mock.calls[0]![0];
    expect(p.toStart).toBe('11:00');
    expect(p.fromStart).toBe('10:00');
    expect(p.toWeekday).toBeUndefined();
    expect('toStaffId' in p).toBe(false);
  });

  it('15 分刻みに丸める', () => {
    expect(dropXToStartMinutes(187, TRACK, 0, 30)).toBe(8 * 60 + 180);
    expect(dropXToStartMinutes(194, TRACK, 0, 30)).toBe(8 * 60 + 195);
    // 時間軸の外へは出さない。
    expect(dropXToStartMinutes(9999, TRACK, 0, 30)).toBe(19 * 60 - 30);
  });

  it('別の職員 (同じ時刻): toStaffId だけ', () => {
    const { onVisitMove } = renderView();
    dragTo('v1', B, 1, 120);
    const p = onVisitMove.mock.calls[0]![0];
    expect(p.toStart).toBeNull();
    expect(p.toStaffId).toBe(B);
    expect(p.toWeekday).toBeUndefined();
  });

  it('別の曜日・別の時刻: toWeekday と toStart', () => {
    const { onVisitMove } = renderView();
    dragTo('v1', A, 3, 60);
    const p = onVisitMove.mock.calls[0]![0];
    expect(p.toWeekday).toBe(3);
    expect(p.toStart).toBe('09:00');
    expect('toStaffId' in p).toBe(false);
  });

  it('担当なしの行へ: toStaffId = null', () => {
    const { onVisitMove } = renderView();
    dragTo('v1', UNASSIGNED_ROW_KEY, 1, 120);
    expect(onVisitMove.mock.calls[0]![0].toStaffId).toBeNull();
  });

  it('今日を含む過去の日には落とせない・過去の日の帯はつかめない', () => {
    const { onVisitMove } = renderView();
    dragTo('v1', A, 0, 120);
    expect(onVisitMove).not.toHaveBeenCalled();
    expect(screen.getByTestId(`swt-cell-${A}-0`).dataset.droppable).toBe('false');
    const pastBar = screen.getByTestId('swt-visit-v0');
    expect(pastBar.getAttribute('draggable')).toBe('false');
    expect(pastBar).toHaveAttribute('aria-disabled', 'true');
  });

  it('新人の行・閲覧のみには落とせない', () => {
    const { onVisitMove } = renderView();
    dragTo('v1', T, 1, 120);
    expect(onVisitMove).not.toHaveBeenCalled();
  });

  it('動かさずに離したら呼ばない・クリックはメニュー', () => {
    const { onVisitMove, onVisitClick } = renderView();
    dragTo('v1', A, 1, 120);
    expect(onVisitMove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('swt-visit-v1'));
    expect(onVisitClick).toHaveBeenCalledOnce();
  });

  it('SWT_VISIT_DND_MIME で運ぶ', () => {
    renderView();
    const dt = makeDataTransfer();
    fireEvent.dragStart(screen.getByTestId('swt-visit-v1'), { dataTransfer: dt, clientX: 0 });
    expect(JSON.parse(dt.getData(SWT_VISIT_DND_MIME))).toMatchObject({ visitId: 'v1' });
  });
});
