/**
 * StaffTimelineView — 「今週の運転席」横タイムライン (Phase E / FE-B) の描画・操作契約。
 *
 * ① スタッフ行とバーが描かれる (時刻・患者名・担当が aria-label に載る)
 * ② 行帰属は StaffWeekBoard の cellMap と同じ (primary → コース担当 → 担当なし)
 * ③ 3px 以内の動き = クリック → onVisitClick
 * ④ 横 pointer ドラッグ → onVisitMove の toStart が 15分スナップされる
 * ⑤ 縦ドラッグ → toStaffId が付く / レーン外は担当変更なし / 変化なしは呼ばない
 * ⑥ 青ピン・取消・過去日・閲覧のみはドラッグ不可 (理由が title と aria-label に出る)
 * ⑦ ドラッグ状態が次の操作へ漏れない (HIGH-1/2 の回帰)
 * ⑧ 突合マーカー (ゴースト before/after) が描かれる
 * ⑨ 氏名 ⠿ の HTML5 DnD = その日の 2 人の予定を丸ごと入れ替え (PO 2026-08-22)
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import {
  snapXOffsetToMinutes,
  STAFF_SWAP_MIME,
  StaffTimelineView,
  UNASSIGNED_ROW_KEY,
  type StaffSwapPayload,
  type StaffTimelineRow,
  type TimelineMarker,
  type TimelineVisit,
  type VisitMovePayload,
} from '../StaffTimelineView';

const WEEK_START = new Date(2026, 7, 17); // 2026-08-17 (月)
const TPL_A = '00000000-0000-4000-8000-0000000000aa';
const STAFF_1 = '00000000-0000-4000-8000-000000000001';
const STAFF_2 = '00000000-0000-4000-8000-000000000002';
const STAFF_X = '00000000-0000-4000-8000-0000000000ff'; // staffRows に無い担当

/** 8:00〜19:00 = 660分。トラック幅 660px → 1px = 1分 でスナップを検算しやすくする。 */
const TRACK_WIDTH = 660;

const rows: StaffTimelineRow[] = [
  { staffId: STAFF_1, name: '宇田川　優莉', office: '稲毛' },
  { staffId: STAFF_2, name: '髙梨　桂子', office: '都賀' },
  { staffId: UNASSIGNED_ROW_KEY, name: '（担当なし）' },
];

function visit(partial: Partial<TimelineVisit> & { id: string }): TimelineVisit {
  return {
    patient_id: `p-${partial.id}`,
    patient_name: '山田　太郎',
    weekday: 0,
    course_template_id: TPL_A,
    start_time: '10:00',
    end_time: '10:30',
    patient_sex: 'male',
    primary_staff_id: STAFF_1,
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

/** レーン検出をテストから固定する (jsdom は elementFromPoint 未実装)。 */
function pointAt(el: Element | null) {
  document.elementFromPoint = (() => el) as typeof document.elementFromPoint;
}

let originalRect: typeof HTMLElement.prototype.getBoundingClientRect;
let originalElementFromPoint: typeof document.elementFromPoint;

beforeEach(() => {
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
  pointAt(null); // 既定は「レーンの外」= 担当変更なし
});

afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = originalRect;
  document.elementFromPoint = originalElementFromPoint;
});

function renderView(over: Partial<React.ComponentProps<typeof StaffTimelineView>> = {}) {
  const onVisitClick = vi.fn();
  const onVisitMove = vi.fn<[VisitMovePayload], void>();
  const onDayChange = vi.fn();
  const onEventClick = vi.fn();
  const onStaffSwap = vi.fn<[StaffSwapPayload], void>();
  render(
    <StaffTimelineView
      weekStart={WEEK_START}
      day={0}
      onDayChange={onDayChange}
      staffRows={rows}
      visits={[visit({ id: 'v1' })]}
      canEdit
      onVisitClick={onVisitClick}
      onVisitMove={onVisitMove}
      onEventClick={onEventClick}
      onStaffSwap={onStaffSwap}
      {...over}
    />,
  );
  return { onVisitClick, onVisitMove, onDayChange, onEventClick, onStaffSwap };
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

describe('StaffTimelineView — 描画', () => {
  it('スタッフ行とバーを描く (時刻・患者名・担当が aria-label に載る)', () => {
    renderView();

    // 氏名の全角スペースは jsdom 側で正規化されるため姓で照合する
    expect(screen.getByTestId(`tl-lane-${STAFF_1}`)).toHaveTextContent('宇田川');
    expect(screen.getByTestId(`tl-lane-${STAFF_2}`)).toHaveTextContent('髙梨');
    expect(screen.getByTestId(`tl-lane-${UNASSIGNED_ROW_KEY}`)).toHaveTextContent('（担当なし）');

    const bar = screen.getByTestId('tl-bar-v1');
    expect(bar.tagName).toBe('BUTTON');
    expect(bar.getAttribute('aria-label')).toBe('10:00〜10:30 山田　太郎 担当 宇田川　優莉');
    expect(bar).not.toHaveAttribute('aria-disabled');
    // 8:00 起点 660分 → 10:00 は 120/660
    expect(bar.style.left).toBe(`${(120 / 660) * 100}%`);
  });

  it('primary が無ければコース担当で行を決める (StaffWeekBoard の cellMap と同じ)', () => {
    renderView({
      visits: [visit({ id: 'vc', primary_staff_id: null })],
      assignedStaffByTemplateWeekday: new Map([[`${TPL_A}:0`, STAFF_2]]),
    });
    expect(screen.getByTestId(`tl-lane-${STAFF_2}`)).toContainElement(
      screen.getByTestId('tl-bar-vc'),
    );
  });

  it('担当スタッフの居ない訪問は「（担当なし）」行に出る', () => {
    renderView({ visits: [visit({ id: 'v9', primary_staff_id: null })] });
    expect(screen.getByTestId(`tl-lane-${UNASSIGNED_ROW_KEY}`)).toContainElement(
      screen.getByTestId('tl-bar-v9'),
    );
  });

  it('staffRows に無い担当は潰さず行を生やす (ズレは隠さない)', () => {
    renderView({
      staffRows: rows.slice(0, 2),
      visits: [visit({ id: 'vx', primary_staff_id: STAFF_X })],
    });
    const lane = screen.getByTestId(`tl-lane-${STAFF_X}`);
    expect(lane).toContainElement(screen.getByTestId('tl-bar-vx'));
    expect(lane).toHaveTextContent('（不明なスタッフ）');
    // 未割当が無ければ「（担当なし）」行は出さない
    expect(screen.queryByTestId(`tl-lane-${UNASSIGNED_ROW_KEY}`)).not.toBeInTheDocument();
  });

  it('alwaysShowUnassignedRow で「（担当なし）」行を常設する', () => {
    renderView({ staffRows: rows.slice(0, 2), alwaysShowUnassignedRow: true });
    expect(screen.getByTestId(`tl-lane-${UNASSIGNED_ROW_KEY}`)).toBeInTheDocument();
  });

  it('曜日ボタンで onDayChange が呼ばれる', () => {
    const { onDayChange } = renderView();
    fireEvent.click(screen.getByRole('button', { name: /水 8\/19/ }));
    expect(onDayChange).toHaveBeenCalledWith(2);
  });

  it('全員（固定）帯を先頭に描く', () => {
    renderView({ fixedAllDay: [{ start: '08:30', end: '08:45', title: '朝会' }] });
    const lane = screen.getByTestId('tl-lane-fixed');
    expect(lane).toHaveTextContent('全員（固定）');
    expect(lane).toHaveTextContent('🔒 08:30 朝会');
  });

  it('他の曜日のマーカー・訪問は描かない', () => {
    renderView({
      visits: [visit({ id: 'v1' }), visit({ id: 'v2', weekday: 3 })],
      markers: [
        {
          action: 'add',
          externalId: 'k9',
          title: '訪問',
          start: '11:00',
          end: '11:30',
          staffId: STAFF_1,
          weekday: 3,
        },
      ],
    });
    expect(screen.getByTestId('tl-bar-v1')).toBeInTheDocument();
    expect(screen.queryByTestId('tl-bar-v2')).not.toBeInTheDocument();
    expect(screen.queryByTestId('tl-marker-after')).not.toBeInTheDocument();
  });
});

describe('StaffTimelineView — ドラッグ', () => {
  it('3px 以内の動きはクリック扱い → onVisitClick', () => {
    const { onVisitClick, onVisitMove } = renderView();
    const bar = screen.getByTestId('tl-bar-v1');

    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 102, 11);
    firePointer(bar, 'pointerup', 102, 11);
    fireEvent.click(bar);

    expect(onVisitMove).not.toHaveBeenCalled();
    expect(onVisitClick).toHaveBeenCalledTimes(1);
    expect(onVisitClick.mock.calls[0][0].id).toBe('v1');
    expect(onVisitClick.mock.calls[0][1]).toBe(bar);
  });

  it('横ドラッグは 15分スナップで onVisitMove を呼ぶ (クリックは発火しない)', () => {
    const { onVisitClick, onVisitMove } = renderView();
    const bar = screen.getByTestId('tl-bar-v1');

    // +40px = +40分 → 10:40 → 15分スナップ → 10:45
    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 140, 12);
    firePointer(bar, 'pointerup', 140, 12);

    expect(onVisitMove).toHaveBeenCalledTimes(1);
    const payload = onVisitMove.mock.calls[0][0];
    expect(payload.visit.id).toBe('v1');
    expect(payload.fromStart).toBe('10:00');
    expect(payload.toStart).toBe('10:45');
    expect('toStaffId' in payload).toBe(false); // 担当は変えていない

    // ドラッグ後に続く click は握り潰す (メニューが開かない)
    fireEvent.click(bar);
    expect(onVisitClick).not.toHaveBeenCalled();
  });

  it('ドラッグ中はバーの時刻ラベルが追随する', () => {
    renderView();
    const bar = screen.getByTestId('tl-bar-v1');

    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 140, 12);

    expect(bar.getAttribute('aria-label')).toContain('10:45〜11:15');
    expect(bar.textContent).toContain('10:45〜11:15');
  });

  it('別のスタッフ行へドロップすると toStaffId が付く', () => {
    const { onVisitMove } = renderView();
    const bar = screen.getByTestId('tl-bar-v1');
    pointAt(screen.getByTestId(`tl-lane-${STAFF_2}`));

    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 130, 60);
    firePointer(bar, 'pointerup', 130, 60);

    expect(onVisitMove).toHaveBeenCalledTimes(1);
    expect(onVisitMove.mock.calls[0][0]).toMatchObject({
      fromStart: '10:00',
      toStart: '10:30',
      toStaffId: STAFF_2,
    });
  });

  it('担当だけ変えたドラッグは toStart を null にする', () => {
    const { onVisitMove } = renderView();
    const bar = screen.getByTestId('tl-bar-v1');
    pointAt(screen.getByTestId(`tl-lane-${UNASSIGNED_ROW_KEY}`));

    // 横は動かさず縦だけ → 時刻は変わらない
    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 100, 80);
    firePointer(bar, 'pointerup', 100, 80);

    expect(onVisitMove).toHaveBeenCalledTimes(1);
    const payload = onVisitMove.mock.calls[0][0];
    expect(payload.toStart).toBeNull();
    expect(payload.toStaffId).toBeNull(); // （担当なし）へ = 担当解除
  });

  it('レーンの外で離したら担当は変えない', () => {
    const { onVisitMove } = renderView();
    const bar = screen.getByTestId('tl-bar-v1');
    pointAt(screen.getByTestId(`tl-lane-${STAFF_2}`));

    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 140, 60);
    pointAt(document.body); // 盤面の外へ抜けた
    firePointer(bar, 'pointermove', 140, 400);
    firePointer(bar, 'pointerup', 140, 400);

    expect(onVisitMove).toHaveBeenCalledTimes(1);
    const payload = onVisitMove.mock.calls[0][0];
    expect(payload.toStart).toBe('10:45');
    expect('toStaffId' in payload).toBe(false);
  });

  it('元の位置に戻して離したら何も呼ばない', () => {
    const { onVisitMove } = renderView();
    const bar = screen.getByTestId('tl-bar-v1');

    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 140, 10);
    firePointer(bar, 'pointermove', 100, 10);
    firePointer(bar, 'pointerup', 100, 10);

    expect(onVisitMove).not.toHaveBeenCalled();
  });
});

describe('StaffTimelineView — ドラッグ不可', () => {
  it('青ピン (week_pinned) は動かない・理由が title と aria-label に出る', () => {
    const { onVisitMove } = renderView({ visits: [visit({ id: 'vp', week_pinned: true })] });
    const bar = screen.getByTestId('tl-bar-vp');

    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 160, 10);
    firePointer(bar, 'pointerup', 160, 10);

    expect(onVisitMove).not.toHaveBeenCalled();
    expect(bar.getAttribute('title')).toContain('青ピン');
    expect(bar.getAttribute('aria-label')).toContain('青ピン（今週だけ固定）のため動かせません。');
    expect(bar).toHaveAttribute('aria-disabled', 'true');
    expect(bar.className).toContain('cursor-not-allowed');
  });

  it('今週だけ取消は動かない・打消線が付く', () => {
    const { onVisitMove } = renderView({ visits: [visit({ id: 'vc', status: 'cancelled' })] });
    const bar = screen.getByTestId('tl-bar-vc');
    expect(bar.style.textDecoration).toBe('line-through');

    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 160, 10);
    firePointer(bar, 'pointerup', 160, 10);

    expect(onVisitMove).not.toHaveBeenCalled();
    expect(bar.getAttribute('title')).toContain('取消済み');
  });

  it('過去日は動かない', () => {
    const { onVisitMove } = renderView({ isPast: true });
    const bar = screen.getByTestId('tl-bar-v1');

    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 160, 10);
    firePointer(bar, 'pointerup', 160, 10);

    expect(onVisitMove).not.toHaveBeenCalled();
    expect(bar.getAttribute('title')).toContain('過去日');
    expect(bar).toHaveAttribute('aria-disabled', 'true');
  });

  it('canEdit=false は動かない (クリックでの参照は残す)', () => {
    const { onVisitMove, onVisitClick } = renderView({ canEdit: false });
    const bar = screen.getByTestId('tl-bar-v1');

    firePointer(bar, 'pointerdown', 100, 10);
    firePointer(bar, 'pointermove', 160, 10);
    firePointer(bar, 'pointerup', 160, 10);
    fireEvent.click(bar);

    expect(onVisitMove).not.toHaveBeenCalled();
    expect(bar.getAttribute('title')).toContain('閲覧のみ');
    expect(onVisitClick).toHaveBeenCalledTimes(1);
  });
});

describe('StaffTimelineView — ドラッグ状態の後始末 (HIGH-1/2 回帰)', () => {
  it('ドラッグ後のクリック抑止が別のバーへ漏れない', () => {
    const { onVisitClick, onVisitMove } = renderView({
      visits: [visit({ id: 'v1' }), visit({ id: 'v2', start_time: '13:00', end_time: '13:30' })],
    });
    const bar1 = screen.getByTestId('tl-bar-v1');
    const bar2 = screen.getByTestId('tl-bar-v2');

    firePointer(bar1, 'pointerdown', 100, 10);
    firePointer(bar1, 'pointermove', 140, 10);
    firePointer(bar1, 'pointerup', 140, 10);
    expect(onVisitMove).toHaveBeenCalledTimes(1);

    // v1 の click は来ないまま v2 をクリック → 抑止を持ち越さない
    fireEvent.click(bar2);
    expect(onVisitClick).toHaveBeenCalledTimes(1);
    expect(onVisitClick.mock.calls[0][0].id).toBe('v2');
  });

  it('青ピンを押した後は直前の押下が残らず、別の訪問が動かない', () => {
    const { onVisitMove } = renderView({
      visits: [visit({ id: 'v1' }), visit({ id: 'vp', start_time: '13:00', week_pinned: true })],
    });
    const bar1 = screen.getByTestId('tl-bar-v1');
    const pinned = screen.getByTestId('tl-bar-vp');

    firePointer(bar1, 'pointerdown', 100, 10); // 押しただけ (まだ動かしていない)
    firePointer(pinned, 'pointerdown', 300, 60); // 動かせないバーを押す
    firePointer(pinned, 'pointermove', 360, 60);
    firePointer(pinned, 'pointerup', 360, 60);

    expect(onVisitMove).not.toHaveBeenCalled();
  });

  it('別のバーへ飛んだ pointermove / pointerup は無視する', () => {
    const { onVisitMove } = renderView({
      visits: [visit({ id: 'v1' }), visit({ id: 'v2', start_time: '13:00', end_time: '13:30' })],
    });
    const bar1 = screen.getByTestId('tl-bar-v1');
    const bar2 = screen.getByTestId('tl-bar-v2');

    firePointer(bar1, 'pointerdown', 100, 10);
    firePointer(bar2, 'pointermove', 200, 10);
    firePointer(bar2, 'pointerup', 200, 10);

    expect(onVisitMove).not.toHaveBeenCalled();
  });
});

describe('StaffTimelineView — イベント / マーカー', () => {
  it('イベントは緑帯で出て、取消済みは打消線・クリックで onEventClick', () => {
    const { onEventClick } = renderView({
      visits: [],
      events: new Map([
        [
          STAFF_1,
          [
            {
              id: 'e1',
              staff_id: STAFF_1,
              date: '2026-08-17',
              title: '朝会',
              start_time: '08:30',
              end_time: '09:00',
              type: 'イベント',
              blocking: false,
              cancelled_at: '2026-08-17T00:00:00Z',
            },
          ],
        ],
      ]) as React.ComponentProps<typeof StaffTimelineView>['events'],
    });
    const bar = screen.getByTestId('tl-event-e1');
    expect(bar.style.textDecoration).toBe('line-through');
    // 種別 (イベント) より状態 (取消済み) の理由を先に出す
    expect(bar.getAttribute('title')).toContain('取消済み');
    fireEvent.click(bar);
    expect(onEventClick).toHaveBeenCalledTimes(1);
    expect(onEventClick.mock.calls[0][1]).toBe(STAFF_1);
  });

  it('突合マーカーのゴースト (before=点線 / after=実線) を描く', () => {
    const markers: TimelineMarker[] = [
      {
        action: 'update',
        externalId: 'k1',
        title: '訪問',
        patientName: '山田　太郎',
        start: '11:00',
        end: '11:30',
        beforeStart: '10:00',
        beforeEnd: '10:30',
        staffId: STAFF_2,
        beforeStaffId: STAFF_1,
        weekday: 0,
        kind: 'visit',
      },
      {
        action: 'delete',
        externalId: 'k2',
        title: '訪問',
        patientName: '佐藤　花子',
        start: '13:00',
        end: '13:30',
        staffId: STAFF_1,
        weekday: 0,
      },
    ];
    renderView({ markers });

    const before = screen.getByTestId('tl-marker-before');
    const after = screen.getByTestId('tl-marker-after');
    expect(before).toHaveTextContent('今ここ');
    expect(after).toHaveTextContent('こう変わる');
    expect(before.style.borderStyle).toBe('dashed');
    expect(after.style.borderStyle).toBe('solid');
    // before は移動前の担当行 / after は移動後の担当行
    expect(screen.getByTestId(`tl-lane-${STAFF_1}`)).toContainElement(before);
    expect(screen.getByTestId(`tl-lane-${STAFF_2}`)).toContainElement(after);

    const del = screen.getByTestId('tl-marker-delete');
    expect(del).toHaveTextContent('消えている');
    expect(del.style.textDecoration).toBe('line-through');
  });
});

describe('StaffTimelineView — スタッフ入れ替え (氏名 ⠿ DnD)', () => {
  it('(a) ⠿ を別のスタッフ行の氏名セルへ落とすと onStaffSwap({from,to,day})', () => {
    const { onStaffSwap } = renderView();
    const dt = makeDataTransfer();

    fireEvent.dragStart(screen.getByTestId(`tl-swap-grip-${STAFF_1}`), { dataTransfer: dt });
    expect(dt.getData(STAFF_SWAP_MIME)).toBe(JSON.stringify({ staffId: STAFF_1, day: 0 }));

    const target = screen.getByTestId(`tl-name-${STAFF_2}`);
    fireEvent.dragOver(target, { dataTransfer: dt });
    // ドロップ先の行がハイライトされる
    expect(target).toHaveAttribute('data-swap-over', 'true');

    fireEvent.drop(target, { dataTransfer: dt });
    expect(onStaffSwap).toHaveBeenCalledTimes(1);
    expect(onStaffSwap.mock.calls[0][0]).toEqual({
      fromStaffId: STAFF_1,
      toStaffId: STAFF_2,
      day: 0,
    });
  });

  it('(a2) 表示中の曜日がそのまま day として渡る', () => {
    const { onStaffSwap } = renderView({ day: 2 });
    const dt = makeDataTransfer();

    fireEvent.dragStart(screen.getByTestId(`tl-swap-grip-${STAFF_2}`), { dataTransfer: dt });
    fireEvent.drop(screen.getByTestId(`tl-name-${STAFF_1}`), { dataTransfer: dt });

    expect(onStaffSwap.mock.calls[0][0]).toEqual({
      fromStaffId: STAFF_2,
      toStaffId: STAFF_1,
      day: 2,
    });
  });

  it('(b) 自分自身へのドロップは呼ばれない', () => {
    const { onStaffSwap } = renderView();
    const dt = makeDataTransfer();

    fireEvent.dragStart(screen.getByTestId(`tl-swap-grip-${STAFF_1}`), { dataTransfer: dt });
    const self = screen.getByTestId(`tl-name-${STAFF_1}`);
    fireEvent.dragOver(self, { dataTransfer: dt });
    fireEvent.drop(self, { dataTransfer: dt });

    expect(onStaffSwap).not.toHaveBeenCalled();
    expect(self).not.toHaveAttribute('data-swap-over');
  });

  it('(b2) 「（担当なし）」行と不明スタッフ行は掴めず・落とせない', () => {
    const { onStaffSwap } = renderView({
      visits: [visit({ id: 'vx', primary_staff_id: STAFF_X })],
    });
    expect(screen.queryByTestId(`tl-swap-grip-${UNASSIGNED_ROW_KEY}`)).not.toBeInTheDocument();
    expect(screen.queryByTestId(`tl-swap-grip-${STAFF_X}`)).not.toBeInTheDocument();

    const dt = makeDataTransfer();
    fireEvent.dragStart(screen.getByTestId(`tl-swap-grip-${STAFF_1}`), { dataTransfer: dt });
    fireEvent.drop(screen.getByTestId(`tl-name-${UNASSIGNED_ROW_KEY}`), { dataTransfer: dt });
    fireEvent.drop(screen.getByTestId(`tl-name-${STAFF_X}`), { dataTransfer: dt });

    expect(onStaffSwap).not.toHaveBeenCalled();
  });

  it('(b3) 過去日は掴めない (理由が title に出る)', () => {
    const { onStaffSwap } = renderView({ isPast: true });
    const grip = screen.getByTestId(`tl-swap-grip-${STAFF_1}`);
    expect(grip).toHaveAttribute('draggable', 'false');
    expect(grip.getAttribute('title')).toContain('過去日');
    expect(grip).toHaveAttribute('aria-disabled', 'true');

    const dt = makeDataTransfer();
    fireEvent.dragStart(grip, { dataTransfer: dt });
    fireEvent.drop(screen.getByTestId(`tl-name-${STAFF_2}`), { dataTransfer: dt });
    expect(onStaffSwap).not.toHaveBeenCalled();

    // クリックしても相手選択メニューは開かない
    fireEvent.click(grip);
    expect(screen.queryByTestId(`tl-swap-menu-${STAFF_1}`)).not.toBeInTheDocument();
  });

  it('(b4) canEdit=false は掴めない (理由が title に出る)', () => {
    const { onStaffSwap } = renderView({ canEdit: false });
    const grip = screen.getByTestId(`tl-swap-grip-${STAFF_1}`);
    expect(grip.getAttribute('title')).toContain('閲覧のみ');

    const dt = makeDataTransfer();
    fireEvent.dragStart(grip, { dataTransfer: dt });
    fireEvent.drop(screen.getByTestId(`tl-name-${STAFF_2}`), { dataTransfer: dt });
    expect(onStaffSwap).not.toHaveBeenCalled();
  });

  it('(c) キーボード代替: ⠿ の Enter で開いた相手選択が同じ onStaffSwap を呼ぶ', () => {
    const { onStaffSwap } = renderView();
    const grip = screen.getByTestId(`tl-swap-grip-${STAFF_1}`);
    expect(grip).toHaveAttribute('role', 'button');
    expect(grip).toHaveAttribute('tabindex', '0');
    expect(grip.getAttribute('aria-label')).toBe(
      '宇田川　優莉さんの予定を入れ替える(ドラッグ、または Enter で相手を選ぶ)',
    );

    // span role="button" はブラウザが Enter を click に変換しない → keyDown で開く
    fireEvent.keyDown(grip, { key: 'Enter' });
    const menu = screen.getByTestId(`tl-swap-menu-${STAFF_1}`);
    expect(menu).toBeInTheDocument();
    // 自分自身と「（担当なし）」は候補に出さない
    expect(screen.getByTestId(`tl-swap-option-${STAFF_2}`)).toBeInTheDocument();
    expect(screen.queryByTestId(`tl-swap-option-${STAFF_1}`)).not.toBeInTheDocument();
    expect(screen.queryByTestId(`tl-swap-option-${UNASSIGNED_ROW_KEY}`)).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId(`tl-swap-option-${STAFF_2}`));
    expect(onStaffSwap).toHaveBeenCalledTimes(1);
    expect(onStaffSwap.mock.calls[0][0]).toEqual({
      fromStaffId: STAFF_1,
      toStaffId: STAFF_2,
      day: 0,
    });
    expect(screen.queryByTestId(`tl-swap-menu-${STAFF_1}`)).not.toBeInTheDocument();
  });

  it('(c1) 新人 (isTrainee) は掴めず・落とせない', () => {
    const { onStaffSwap } = renderView({
      staffRows: [rows[0]!, { ...rows[1]!, isTrainee: true }, rows[2]!],
    });
    expect(screen.queryByTestId(`tl-swap-grip-${STAFF_2}`)).not.toBeInTheDocument();

    const dt = makeDataTransfer();
    fireEvent.dragStart(screen.getByTestId(`tl-swap-grip-${STAFF_1}`), { dataTransfer: dt });
    fireEvent.drop(screen.getByTestId(`tl-name-${STAFF_2}`), { dataTransfer: dt });
    expect(onStaffSwap).not.toHaveBeenCalled();

    // 相手選択メニューの候補にも出さない
    fireEvent.click(screen.getByTestId(`tl-swap-grip-${STAFF_1}`));
    expect(screen.queryByTestId(`tl-swap-option-${STAFF_2}`)).not.toBeInTheDocument();
  });

  it('(c2) メニューは Escape で閉じる (Radix のポータル/dismiss)', async () => {
    const { onStaffSwap } = renderView();
    fireEvent.click(screen.getByTestId(`tl-swap-grip-${STAFF_1}`));
    expect(screen.getByTestId(`tl-swap-menu-${STAFF_1}`)).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });
    await vi.waitFor(() =>
      expect(screen.queryByTestId(`tl-swap-menu-${STAFF_1}`)).not.toBeInTheDocument(),
    );
    expect(onStaffSwap).not.toHaveBeenCalled();
  });

  it('(c3) メニューの「やめる」は何も呼ばずに閉じる', () => {
    const { onStaffSwap } = renderView();
    fireEvent.click(screen.getByTestId(`tl-swap-grip-${STAFF_1}`));
    fireEvent.click(screen.getByTestId(`tl-swap-cancel-${STAFF_1}`));

    expect(onStaffSwap).not.toHaveBeenCalled();
    expect(screen.queryByTestId(`tl-swap-menu-${STAFF_1}`)).not.toBeInTheDocument();
  });

  it('onStaffSwap 未指定なら ⠿ を出さない', () => {
    renderView({ onStaffSwap: undefined });
    expect(screen.queryByTestId(`tl-swap-grip-${STAFF_1}`)).not.toBeInTheDocument();
  });
});

describe('StaffTimelineView — 行アクション (🛌休みにする / ＋訪問 / ＋イベント)', () => {
  function renderWithActions(over: Partial<React.ComponentProps<typeof StaffTimelineView>> = {}) {
    const onMarkOff = vi.fn();
    const onAddVisit = vi.fn();
    const onAddEvent = vi.fn();
    renderView({ onMarkOff, onAddVisit, onAddEvent, ...over });
    return { onMarkOff, onAddVisit, onAddEvent };
  }

  it('各アクションが (staffId, day) で呼ばれる', () => {
    const { onMarkOff, onAddVisit, onAddEvent } = renderWithActions({ day: 3 });

    fireEvent.click(screen.getByTestId(`tl-off-action-${STAFF_2}`));
    expect(onMarkOff.mock.calls[0]?.slice(0, 2)).toEqual([STAFF_2, 3]);
    // アンカー要素 (第3引数) はボタン自身
    expect(onMarkOff.mock.calls[0]?.[2]).toBe(screen.getByTestId(`tl-off-action-${STAFF_2}`));

    fireEvent.click(screen.getByTestId(`tl-add-visit-${STAFF_1}`));
    expect(onAddVisit.mock.calls[0]?.slice(0, 2)).toEqual([STAFF_1, 3]);

    fireEvent.click(screen.getByTestId(`tl-add-event-${STAFF_1}`));
    expect(onAddEvent).toHaveBeenCalledWith(STAFF_1, 3);
  });

  it('「（担当なし）」行と不明スタッフ行には出さない', () => {
    renderWithActions({ visits: [visit({ id: 'vx', primary_staff_id: STAFF_X })] });
    expect(screen.queryByTestId(`tl-row-actions-${UNASSIGNED_ROW_KEY}`)).not.toBeInTheDocument();
    expect(screen.queryByTestId(`tl-row-actions-${STAFF_X}`)).not.toBeInTheDocument();
  });

  it('過去日 / 閲覧のみでは出さない', () => {
    renderWithActions({ isPast: true });
    expect(screen.queryByTestId(`tl-row-actions-${STAFF_1}`)).not.toBeInTheDocument();
  });

  it('canEdit=false では出さない', () => {
    renderWithActions({ canEdit: false });
    expect(screen.queryByTestId(`tl-row-actions-${STAFF_1}`)).not.toBeInTheDocument();
  });

  it('ハンドラを渡さなければ何も出さない', () => {
    renderView();
    expect(screen.queryByTestId(`tl-row-actions-${STAFF_1}`)).not.toBeInTheDocument();
  });
});

describe('snapXOffsetToMinutes', () => {
  it('15分に丸める', () => {
    // 幅 660px = 660分 → 1px = 1分
    expect(snapXOffsetToMinutes(40, 660, 600, 30)).toBe(645); // 10:00 +40分 → 10:45
    expect(snapXOffsetToMinutes(7, 660, 600, 30)).toBe(600); // 端数は元へ戻る
    expect(snapXOffsetToMinutes(-40, 660, 600, 30)).toBe(555); // 9:15
  });

  it('時間軸の外へは出さない (両端クランプ)', () => {
    expect(snapXOffsetToMinutes(-9999, 660, 600, 30)).toBe(8 * 60); // 8:00
    expect(snapXOffsetToMinutes(9999, 660, 600, 30)).toBe(19 * 60 - 30); // 18:30 (終端 19:00)
    expect(snapXOffsetToMinutes(9999, 660, 600, 60)).toBe(19 * 60 - 60); // 長い訪問ほど手前で止まる
  });

  it('幅が測れないときは動かさない', () => {
    expect(snapXOffsetToMinutes(120, 0, 600, 30)).toBe(600);
  });
});
