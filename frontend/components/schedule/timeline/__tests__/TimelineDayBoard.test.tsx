import { DndContext } from '@dnd-kit/core';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { CourseGridVisit } from '@/components/schedule/v2/CourseDayTable';
import {
  TimelineDayBoard,
  TlVisitDragGhost,
  type TimelineCourseColumn,
} from '@/components/schedule/timeline/TimelineDayBoard';
import { durationToHeight, minutesToY } from '@/lib/scheduling/timeline';

function visit(over: Partial<CourseGridVisit> & { id: string }): CourseGridVisit {
  return {
    patient_id: `p-${over.id}`,
    patient_name: `患者${over.id}`,
    patient_address: null,
    patient_requires_multiple_staff: false,
    patient_sex_restriction_label: null,
    required_staff_count: 1,
    start_slot: '09:30',
    ...over,
  } as CourseGridVisit;
}

function column(over: Partial<TimelineCourseColumn> & { key: string }): TimelineCourseColumn {
  return {
    template: { id: 't1', office_id: 'o1', label: 'A' } as TimelineCourseColumn['template'],
    course: { id: 'c1', assigned_staff_id: 's1' } as TimelineCourseColumn['course'],
    officeName: '稲毛',
    visits: [],
    assignedStaff: {
      id: 's1',
      name: '田中 一郎',
      sex: 'male',
    } as TimelineCourseColumn['assignedStaff'],
    freeGaps: [],
    capacity: { filled: 0, max: 6 },
    staffEvents: [],
    ...over,
  };
}

describe('TimelineDayBoard', () => {
  it('コース列ヘッダに担当スタッフ名・コース記号・件数を出す', () => {
    render(
      <TimelineDayBoard
        columns={[column({ key: 'c1', capacity: { filled: 2, max: 6 } })]}
        weekdayLabel="月"
      />,
    );
    expect(screen.getByText('田中 一郎')).toBeInTheDocument();
    expect(screen.getByText('稲毛A')).toBeInTheDocument();
    expect(screen.getByText('2/6件')).toBeInTheDocument();
  });

  it('訪問カードを時間比例の位置・高さで描く (9:30・35分)', () => {
    render(
      <TimelineDayBoard
        columns={[
          column({
            key: 'c1',
            visits: [visit({ id: 'v1', start_time: '09:30:00', end_time: '10:05:00' })],
          }),
        ]}
        weekdayLabel="月"
      />,
    );
    const card = screen.getByTestId('tl-visit-v1');
    // 9:30 は 9:00 起点で +30分 → minutesToY(570)。高さは 35分ぶん。
    expect(card.style.top).toBe(`${minutesToY(9 * 60 + 30) + 1}px`);
    expect(card.style.height).toBe(`${durationToHeight(35) - 3}px`);
    expect(screen.getByText('患者v1')).toBeInTheDocument();
  });

  it('性別でカード地色が変わる (patient_sex)', () => {
    render(
      <TimelineDayBoard
        columns={[
          column({
            key: 'c1',
            visits: [
              visit({ id: 'm', patient_sex: 'male', start_time: '09:30:00', end_time: '10:00:00' }),
              visit({
                id: 'f',
                patient_sex: 'female',
                start_time: '10:30:00',
                end_time: '11:00:00',
              }),
              visit({ id: 'n', patient_sex: null, start_time: '11:30:00', end_time: '12:00:00' }),
            ],
          }),
        ]}
        weekdayLabel="月"
      />,
    );
    expect(screen.getByTestId('tl-visit-m').style.background).toContain('male');
    expect(screen.getByTestId('tl-visit-f').style.background).toContain('female');
    expect(screen.getByTestId('tl-visit-n').style.background).toContain('neutral');
  });

  it('カードクリックで onPatientClick(patientId) を呼ぶ', () => {
    const onClick = vi.fn();
    render(
      <TimelineDayBoard
        columns={[
          column({
            key: 'c1',
            visits: [visit({ id: 'v1', start_time: '09:30:00', end_time: '10:05:00' })],
          }),
        ]}
        weekdayLabel="月"
        onPatientClick={onClick}
      />,
    );
    screen.getByTestId('tl-visit-v1').click();
    expect(onClick).toHaveBeenCalledWith('p-v1');
  });

  it('会議・イベントは担当スタッフの列内にだけ帯を描く (全幅ではない)', () => {
    const ev = {
      id: 'e1',
      date: '2026-07-07',
      title: '接遇研修',
      start_time: '13:00',
      end_time: '13:45',
      type: '研修',
    } as TimelineCourseColumn['staffEvents'][number];
    render(
      <TimelineDayBoard
        columns={[
          column({ key: 'c1', staffEvents: [ev] }),
          // イベントを持たない別コース列 — こちらには帯が出ないこと。
          column({ key: 'c2', staffEvents: [] }),
        ]}
        weekdayLabel="月"
      />,
    );
    expect(screen.getByText('研修: 接遇研修')).toBeInTheDocument();
    expect(screen.getByText('カイポケ反映外')).toBeInTheDocument();
    // 帯はイベント所有者の列 (c1) にだけ描かれる。
    expect(screen.getByTestId('tl-event-c1-e1')).toBeInTheDocument();
    expect(screen.queryByTestId('tl-event-c2-e1')).toBeNull();
    // 所有列内の帯 (per-column) — 描画は 1 箇所のみ。
    expect(screen.getAllByText(/接遇研修/)).toHaveLength(1);
  });

  it('onEventClick ありのときイベント帯はボタンになり (ev, col) で発火する', () => {
    const onEventClick = vi.fn();
    const ev = {
      id: 'e1',
      date: '2026-07-07',
      title: '全体会議',
      start_time: '13:00',
      end_time: '14:00',
      type: 'イベント',
    } as TimelineCourseColumn['staffEvents'][number];
    render(
      <TimelineDayBoard
        columns={[column({ key: 'c1', staffEvents: [ev] })]}
        weekdayLabel="月"
        onEventClick={onEventClick}
      />,
    );
    const band = screen.getByTestId('tl-event-c1-e1');
    expect(band.tagName).toBe('BUTTON');
    band.click();
    expect(onEventClick).toHaveBeenCalledTimes(1);
    expect(onEventClick.mock.calls[0]![0].id).toBe('e1');
    expect(onEventClick.mock.calls[0]![1].key).toBe('c1');
  });

  it('onEventClick なし (read-only) のときイベント帯は pointer-events-none の div', () => {
    const ev = {
      id: 'e1',
      date: '2026-07-07',
      title: '全体会議',
      start_time: '13:00',
      end_time: '14:00',
      type: 'イベント',
    } as TimelineCourseColumn['staffEvents'][number];
    render(
      <TimelineDayBoard columns={[column({ key: 'c1', staffEvents: [ev] })]} weekdayLabel="月" />,
    );
    const band = screen.getByTestId('tl-event-c1-e1');
    expect(band.tagName).toBe('DIV');
    expect(band.className).toContain('pointer-events-none');
  });

  it('現在時刻ラインは nowMinutes 指定時のみ出る', () => {
    const { rerender } = render(
      <TimelineDayBoard columns={[column({ key: 'c1' })]} weekdayLabel="月" nowMinutes={null} />,
    );
    expect(screen.queryByTestId('timeline-now-line')).toBeNull();
    rerender(
      <TimelineDayBoard
        columns={[column({ key: 'c1' })]}
        weekdayLabel="月"
        nowMinutes={14 * 60 + 5}
      />,
    );
    expect(screen.getByTestId('timeline-now-line')).toBeInTheDocument();
  });

  it('時間帯が重なる訪問は左右レーンに分かれて相互に隠さない (MED-1)', () => {
    render(
      <TimelineDayBoard
        columns={[
          column({
            key: 'c1',
            visits: [
              visit({ id: 'a', start_time: '09:30:00', end_time: '10:30:00' }),
              visit({ id: 'b', start_time: '10:00:00', end_time: '11:00:00' }), // a と重なる
            ],
          }),
        ]}
        weekdayLabel="月"
      />,
    );
    const a = screen.getByTestId('tl-visit-a');
    const b = screen.getByTestId('tl-visit-b');
    // 重なるので幅が calc(50% ...) に分割され、left が別 (= 相互に全幅で被らない)。
    expect(a.style.width).toContain('50%');
    expect(b.style.width).toContain('50%');
    expect(a.style.left).not.toBe(b.style.left);
  });

  it('範囲外 (18:00超) の訪問も軸内にクランプして描く (LOW-4)', () => {
    render(
      <TimelineDayBoard
        columns={[
          column({
            key: 'c1',
            visits: [visit({ id: 'late', start_time: '17:30:00', end_time: '18:30:00' })],
          }),
        ]}
        weekdayLabel="月"
      />,
    );
    const card = screen.getByTestId('tl-visit-late');
    // 17:30 開始 (軸内)・終端は 18:00 にクランプ → top は負にならない。
    expect(card.style.top).toBe(`${minutesToY(17 * 60 + 30) + 1}px`);
    expect(Number.parseFloat(card.style.top)).toBeGreaterThan(0);
  });

  it('同住所・同時刻の2名は90分占有のペアボックスにまとまり上下2段で並ぶ', () => {
    render(
      <TimelineDayBoard
        columns={[
          column({
            key: 'c1',
            visits: [
              visit({
                id: 'sa1',
                patient_id: 'pa',
                patient_name: '安永 一',
                start_time: '16:00:00',
                end_time: '16:35:00',
                same_address_group_id: 'g1',
              }),
              visit({
                id: 'sa2',
                patient_id: 'pb',
                patient_name: '菅原 二',
                start_time: '16:00:00',
                end_time: '16:35:00',
                same_address_group_id: 'g1',
              }),
            ],
          }),
        ]}
        weekdayLabel="月"
      />,
    );
    // ペアボックスが出て、2名とも内部に描かれる。
    const box = screen.getByTestId('tl-pair-pair:sa1:sa2');
    expect(box).toBeInTheDocument();
    // 90分占有 (16:00→17:30) の高さ = durationToHeight(90) 相当。
    expect(box.style.height).toBe(`${durationToHeight(90) - 3}px`);
    expect(screen.getByText('安永 一')).toBeInTheDocument();
    expect(screen.getByText('菅原 二')).toBeInTheDocument();
    expect(screen.getByText(/同住所 90分占有/)).toBeInTheDocument();
  });

  it('同住所ペアと重ならない単体カードは全幅のまま', () => {
    render(
      <TimelineDayBoard
        columns={[
          column({
            key: 'c1',
            visits: [
              visit({
                id: 'sa1',
                patient_id: 'pa',
                start_time: '09:00:00',
                end_time: '09:35:00',
                same_address_group_id: 'g1',
              }),
              visit({
                id: 'sa2',
                patient_id: 'pb',
                start_time: '09:00:00',
                end_time: '09:35:00',
                same_address_group_id: 'g1',
              }),
              // ペア(9:00-10:30占有)と重ならない単体。
              visit({ id: 'solo', start_time: '11:00:00', end_time: '11:35:00' }),
            ],
          }),
        ]}
        weekdayLabel="月"
      />,
    );
    const solo = screen.getByTestId('tl-visit-solo');
    // laneCount=1 なので left:3px / right:3px (全幅)。
    expect(solo.style.left).toBe('3px');
    expect(solo.style.right).toBe('3px');
  });

  it('TlVisitDragGhost はカードと同じ視覚言語 (性別地色・名前・時刻/分) で描く', () => {
    render(
      <TlVisitDragGhost
        visit={visit({
          id: 'g1',
          patient_name: '青柳 あい',
          patient_sex: 'female',
          start_time: '09:30:00',
          end_time: '10:15:00',
          is_pinned: true,
        })}
      />,
    );
    const ghost = screen.getByTestId('tl-drag-ghost');
    expect(ghost.style.background).toContain('female');
    expect(screen.getByText('青柳 あい')).toBeInTheDocument();
    expect(screen.getByText(/09:30・45分/)).toBeInTheDocument();
    expect(ghost.querySelector('[data-icon="push-pin"]')).not.toBeNull();
  });

  it('コース0件は案内を出す', () => {
    render(<TimelineDayBoard columns={[]} weekdayLabel="日" />);
    expect(screen.getByText(/表示対象コースがありません/)).toBeInTheDocument();
  });

  it('onFreeSlotClick ありのとき空き枠がボタンになり (col, gap) で発火する (T-2 ②-a)', () => {
    const onClick = vi.fn();
    const col = column({
      key: 'c1',
      freeGaps: [{ startMin: 13 * 60, endMin: 14 * 60, label: '13:00〜14:00' }],
    });
    render(<TimelineDayBoard columns={[col]} weekdayLabel="月" onFreeSlotClick={onClick} />);
    const gapEl = screen.getByTestId('tl-gap-c1-780');
    expect(gapEl.tagName).toBe('BUTTON');
    expect(screen.getByText('ここに追加')).toBeInTheDocument();
    gapEl.click();
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(onClick.mock.calls[0]![0].key).toBe('c1');
    expect(onClick.mock.calls[0]![1].startMin).toBe(780);
  });

  it('dndEnabled のとき単独カードは draggable・ピン留め/ペアは掴めない (T-2 ②-b)', () => {
    render(
      <DndContext>
        <TimelineDayBoard
          columns={[
            column({
              key: 'c1',
              visits: [
                visit({ id: 'norm', start_time: '09:30:00', end_time: '10:15:00' }),
                visit({
                  id: 'pin',
                  start_time: '11:00:00',
                  end_time: '11:45:00',
                  is_pinned: true,
                }),
                visit({
                  id: 'pair',
                  start_time: '13:00:00',
                  end_time: '13:45:00',
                  visit_group_id: 'g1',
                }),
              ],
            }),
          ]}
          weekdayLabel="月"
          dndEnabled
        />
      </DndContext>,
    );
    expect(screen.getByTestId('tl-visit-norm').getAttribute('data-tl-drag')).toBe('enabled');
    expect(screen.getByTestId('tl-visit-pin').getAttribute('data-tl-drag')).toBe('disabled');
    expect(screen.getByTestId('tl-visit-pair').getAttribute('data-tl-drag')).toBe('disabled');
    // 列 droppable レイヤが張られる。
    expect(screen.getByTestId('tl-col-drop-c1')).toBeInTheDocument();
  });

  it('通常カードは pointerdown+move で実際にドラッグが始まる (onPointerDown 上書き回帰防止)', async () => {
    // ②-c で onPointerDown={undefined} が listeners spread を後勝ちで上書きし
    // ドラッグが死んだ実バグの回帰テスト。DndContext の onDragStart 発火まで確認する。
    const onDragStart = vi.fn();
    render(
      <DndContext onDragStart={onDragStart}>
        <TimelineDayBoard
          columns={[
            column({
              key: 'c1',
              visits: [visit({ id: 'norm', start_time: '09:30:00', end_time: '10:15:00' })],
            }),
          ]}
          weekdayLabel="月"
          dndEnabled
        />
      </DndContext>,
    );
    const card = screen.getByTestId('tl-visit-norm');
    // jsdom には PointerEvent が無いため MouseEvent を pointerdown として偽装し、
    // PointerSensor の activator 条件 (isPrimary && button===0) を満たす。
    const down = new MouseEvent('pointerdown', { bubbles: true, cancelable: true, button: 0 });
    Object.defineProperty(down, 'isPrimary', { value: true });
    card.dispatchEvent(down);
    await vi.waitFor(() => {
      expect(onDragStart).toHaveBeenCalledTimes(1);
      expect(String(onDragStart.mock.calls[0]![0].active.id)).toBe('tl-visit:norm');
    });
    fireEvent.pointerUp(document);
  });

  it('ピン留めカードを掴もうとすると shake で拒否を伝える (T-2 ②-c)', () => {
    render(
      <DndContext>
        <TimelineDayBoard
          columns={[
            column({
              key: 'c1',
              visits: [
                visit({ id: 'pin', start_time: '09:30:00', end_time: '10:15:00', is_pinned: true }),
                visit({ id: 'norm', start_time: '11:00:00', end_time: '11:45:00' }),
              ],
            }),
          ]}
          weekdayLabel="月"
          dndEnabled
        />
      </DndContext>,
    );
    const pinned = screen.getByTestId('tl-visit-pin');
    // ピン表示は 🔒 ではなく赤丸頭の PushPin アイコン (PO要望で全UI統一)。
    expect(pinned.querySelector('[data-icon="push-pin"]')).not.toBeNull();
    fireEvent.pointerDown(pinned);
    expect(pinned.className).toContain('tl-shake');
    // アニメーション終了で解除 (再度掴んだらまた shake できる)。
    fireEvent.animationEnd(pinned);
    expect(pinned.className).not.toContain('tl-shake');
    // 通常カードは pointerdown しても shake しない。
    const norm = screen.getByTestId('tl-visit-norm');
    fireEvent.pointerDown(norm);
    expect(norm.className).not.toContain('tl-shake');
  });

  it('dndEnabled なし (read-only) のときカードは従来どおり非ドラッグ・droppable レイヤなし', () => {
    render(
      <TimelineDayBoard
        columns={[
          column({
            key: 'c1',
            visits: [visit({ id: 'v1', start_time: '09:30:00', end_time: '10:15:00' })],
          }),
        ]}
        weekdayLabel="月"
      />,
    );
    expect(screen.getByTestId('tl-visit-v1').getAttribute('data-tl-drag')).toBeNull();
    expect(screen.queryByTestId('tl-col-drop-c1')).toBeNull();
  });

  it('onFreeSlotClick なし (read-only) のとき空き枠は従来の表示専用 div のまま', () => {
    render(
      <TimelineDayBoard
        columns={[
          column({
            key: 'c1',
            freeGaps: [{ startMin: 13 * 60, endMin: 14 * 60, label: '13:00〜14:00' }],
          }),
        ]}
        weekdayLabel="月"
      />,
    );
    const gapEl = screen.getByTestId('tl-gap-c1-780');
    expect(gapEl.tagName).toBe('DIV');
    expect(screen.queryByText('ここに追加')).toBeNull();
  });
});
