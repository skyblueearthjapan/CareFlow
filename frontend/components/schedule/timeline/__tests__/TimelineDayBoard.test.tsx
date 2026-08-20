import { DndContext } from '@dnd-kit/core';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { CourseGridVisit } from '@/components/schedule/v2/courseGrid';
import {
  TimelineDayBoard,
  TlPairDragGhost,
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
    staffOptions: [],
    ...over,
  };
}

/**
 * ドラッグ終了。dnd-kit の PointerSensor は drag 開始時に document へ capture 段の
 * click 抑止リスナー (stopPropagation) を張り、drag 終了後 `setTimeout(..., 50)` で
 * 外す (core.cjs AbstractPointerSensor.detach)。同期で次のテストへ進むと後続の
 * `.click()` が全部飲まれるため、ここで確実にフラッシュしてから抜ける。
 */
async function endDragAndFlush() {
  fireEvent.pointerUp(document);
  await new Promise((resolve) => setTimeout(resolve, 60));
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
    // PO指摘 2026-07-08: 帯の主表示はタイトルのみ (「研修: 」前置は狭い列で
    // 種別だけ見えて切れるため廃止。種別はツールチップ/2行目)。
    expect(screen.getByText('接遇研修')).toBeInTheDocument();
    // 旧「カイポケ反映外」バッジは Phase 3 (カイポケ送信機能) で撤去済み。
    expect(screen.queryByText('カイポケ反映外')).toBeNull();
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
    // ピン表示はカード右上に打ち込む CornerPushPin (PO要望 2026-07-08)。
    expect(ghost.querySelector('[data-icon="corner-push-pin"]')).not.toBeNull();
  });

  it('住所は30分カードから📍行で出て、極小カードは title で読める (A-1)', () => {
    render(
      <TimelineDayBoard
        columns={[
          column({
            key: 'c1',
            visits: [
              visit({
                id: 'std30',
                start_time: '09:30:00',
                end_time: '10:00:00',
                patient_address: '稲毛区小仲台2-5',
              }),
              visit({
                id: 'short',
                start_time: '11:00:00',
                end_time: '11:15:00',
                patient_address: '稲毛区園生町100',
              }),
            ],
          }),
        ]}
        weekdayLabel="月"
      />,
    );
    // 30分カード (高さ49px) でも 📍住所行が見える (重複なし = 1回だけ・PO要望)。
    expect(screen.getAllByText('稲毛区小仲台2-5')).toHaveLength(1);
    // 15分の極小カード (高さ30px) → 行は出ないが title で読める。
    expect(screen.queryByText('稲毛区園生町100')).toBeNull();
    expect(screen.getByTestId('tl-visit-short').getAttribute('title')).toBe('稲毛区園生町100');
  });

  it('同住所ペアボックスは dndEnabled で2名セットのまま掴める (青ピン含みは不可)', () => {
    render(
      <DndContext>
        <TimelineDayBoard
          columns={[
            column({
              key: 'c1',
              visits: [
                visit({
                  id: 'pa',
                  start_time: '09:30:00',
                  end_time: '10:05:00',
                  same_address_group_id: 'g1',
                  patient_id: 'p-pa',
                }),
                visit({
                  id: 'pb',
                  start_time: '09:30:00',
                  end_time: '10:05:00',
                  same_address_group_id: 'g1',
                  patient_id: 'p-pb',
                }),
                visit({
                  id: 'qa',
                  start_time: '13:00:00',
                  end_time: '13:35:00',
                  same_address_group_id: 'g2',
                  patient_id: 'p-qa',
                  week_pinned: true,
                }),
                visit({
                  id: 'qb',
                  start_time: '13:00:00',
                  end_time: '13:35:00',
                  same_address_group_id: 'g2',
                  patient_id: 'p-qb',
                }),
              ],
            }),
          ]}
          weekdayLabel="月"
          dndEnabled
        />
      </DndContext>,
    );
    const okPair = screen.getByTestId('tl-pair-pair:pa:pb');
    expect(okPair.getAttribute('data-tl-drag')).toBe('enabled');
    // 青ピン (今週固定) を含むペアは掴めない + shake で拒否 (統合 2026-08-09:
    // 赤=完全固定はドラッグ可・移動確認ダイアログで注意書き)。
    const pinnedPair = screen.getByTestId('tl-pair-pair:qa:qb');
    expect(pinnedPair.getAttribute('data-tl-drag')).toBe('disabled');
    fireEvent.pointerDown(pinnedPair);
    expect(pinnedPair.className).toContain('tl-shake');
  });

  it('TlPairDragGhost は2名セットのまま描く', () => {
    render(
      <TlPairDragGhost
        visits={[
          visit({ id: 'g1', patient_name: '安永 一', patient_sex: 'female' }),
          visit({ id: 'g2', patient_name: '菅原 二', patient_sex: 'male' }),
        ]}
      />,
    );
    expect(screen.getByTestId('tl-pair-drag-ghost')).toBeInTheDocument();
    expect(screen.getByText('安永 一')).toBeInTheDocument();
    expect(screen.getByText('菅原 二')).toBeInTheDocument();
    expect(screen.getByText(/同住所 2名セット/)).toBeInTheDocument();
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

  it('dndEnabled のとき単独カードは draggable・青ピン/ペアは掴めない (赤=完全固定は掴める)', () => {
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
                  week_pinned: true,
                }),
                visit({
                  id: 'red',
                  start_time: '14:30:00',
                  end_time: '15:15:00',
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
    // 青 (今週固定) は蓋 = 掴めない。
    expect(screen.getByTestId('tl-visit-pin').getAttribute('data-tl-drag')).toBe('disabled');
    // 赤 (完全固定) は掴める — 移動確認ダイアログで「これは完全固定です」を出す
    // (統合 / PO 決定 2026-08-09)。
    expect(screen.getByTestId('tl-visit-red').getAttribute('data-tl-drag')).toBe('enabled');
    expect(screen.getByTestId('tl-visit-pair').getAttribute('data-tl-drag')).toBe('disabled');
    // 列 droppable レイヤが張られる。
    expect(screen.getByTestId('tl-col-drop-c1')).toBeInTheDocument();
  });

  it('同住所ペアの各行に ⠿ ハンドルが出て個別 draggable・青ピン行はハンドル無し (T-6 パリティ③)', () => {
    render(
      <DndContext>
        <TimelineDayBoard
          columns={[
            column({
              key: 'c1',
              visits: [
                visit({
                  id: 'sa1',
                  patient_id: 'pa',
                  start_time: '16:00:00',
                  end_time: '16:35:00',
                  same_address_group_id: 'g1',
                }),
                visit({
                  id: 'sa2',
                  patient_id: 'pb',
                  start_time: '16:00:00',
                  end_time: '16:35:00',
                  same_address_group_id: 'g1',
                  week_pinned: true,
                }),
              ],
            }),
          ]}
          weekdayLabel="月"
          dndEnabled
        />
      </DndContext>,
    );
    // 非ピンの行 = ハンドルあり・個別 draggable。
    expect(screen.getByTestId('tl-pair-member-handle-sa1')).toBeInTheDocument();
    expect(screen.getByTestId('tl-visit-sa1').getAttribute('data-tl-member-drag')).toBe('enabled');
    // 青ピン (今週固定) の行 = ハンドル無し (disabled)。
    expect(screen.queryByTestId('tl-pair-member-handle-sa2')).toBeNull();
    expect(screen.getByTestId('tl-visit-sa2').getAttribute('data-tl-member-drag')).toBe('disabled');
    // 箱自体は青ピンを含むため 2名セット移動が disabled のまま (既存契約)。
    expect(screen.getByTestId('tl-pair-pair:sa1:sa2').getAttribute('data-tl-drag')).toBe(
      'disabled',
    );
  });

  it('⠿ ハンドルからのドラッグは tl-visit として発火し、箱の2名セットドラッグにならない', async () => {
    const onDragStart = vi.fn();
    render(
      <DndContext onDragStart={onDragStart}>
        <TimelineDayBoard
          columns={[
            column({
              key: 'c1',
              visits: [
                visit({
                  id: 'sa1',
                  patient_id: 'pa',
                  start_time: '16:00:00',
                  end_time: '16:35:00',
                  same_address_group_id: 'g1',
                }),
                visit({
                  id: 'sa2',
                  patient_id: 'pb',
                  start_time: '16:00:00',
                  end_time: '16:35:00',
                  same_address_group_id: 'g1',
                }),
              ],
            }),
          ]}
          weekdayLabel="月"
          dndEnabled
        />
      </DndContext>,
    );
    const handle = screen.getByTestId('tl-pair-member-handle-sa1');
    const down = new MouseEvent('pointerdown', { bubbles: true, cancelable: true, button: 0 });
    Object.defineProperty(down, 'isPrimary', { value: true });
    handle.dispatchEvent(down);
    await vi.waitFor(() => {
      expect(onDragStart).toHaveBeenCalledTimes(1);
      // 個別移動 (tl-visit:) であって 2名セット (tl-pair:) ではない。
      expect(String(onDragStart.mock.calls[0]![0].active.id)).toBe('tl-visit:sa1');
    });
    await endDragAndFlush();
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
    await endDragAndFlush();
  });

  it('青ピン (今週固定) カードを掴もうとすると shake で拒否を伝える (T-2 ②-c)', () => {
    render(
      <DndContext>
        <TimelineDayBoard
          columns={[
            column({
              key: 'c1',
              visits: [
                visit({
                  id: 'pin',
                  start_time: '09:30:00',
                  end_time: '10:15:00',
                  week_pinned: true,
                }),
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
    // 青ピン表示はカード右上に打ち込む CornerWeekPushPin。
    expect(pinned.querySelector('[data-icon="corner-week-push-pin"]')).not.toBeNull();
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

  // ─── G1: 列ヘッダの担当スタッフ変更 dropdown (テーブルからの移設) ──────────
  describe('G1. 担当スタッフ変更 dropdown', () => {
    const staffOptions = [
      { id: 's1', name: '田中 一郎', sex: 'male' },
      { id: 's2', name: '佐藤 花子', sex: 'female' },
    ] as TimelineCourseColumn['staffOptions'];

    it('onChangeAssignedStaff 指定 (canEdit) のとき列ヘッダに select が出て、変更でハンドラが呼ばれる', () => {
      const onChange = vi.fn();
      render(
        <TimelineDayBoard
          columns={[column({ key: 'c1', staffOptions })]}
          weekdayLabel="月"
          onChangeAssignedStaff={onChange}
        />,
      );
      const select = screen.getByTestId('tl-staff-select-c1') as HTMLSelectElement;
      // course.assigned_staff_id='s1' が初期選択。
      expect(select.value).toBe('s1');
      fireEvent.change(select, { target: { value: 's2' } });
      expect(onChange).toHaveBeenCalledTimes(1);
      expect(onChange.mock.calls[0]![0].key).toBe('c1');
      expect(onChange.mock.calls[0]![1]).toBe('s2');
      // 「（未割当）」= 空 option → null で通知する。
      fireEvent.change(select, { target: { value: '' } });
      expect(onChange.mock.calls[1]![1]).toBeNull();
    });

    it('onChangeAssignedStaff 未指定 (read-only) のとき select は出ず担当名のテキスト表示のまま', () => {
      render(
        <TimelineDayBoard columns={[column({ key: 'c1', staffOptions })]} weekdayLabel="月" />,
      );
      expect(screen.queryByTestId('tl-staff-select-c1')).toBeNull();
      expect(screen.getByText('田中 一郎')).toBeInTheDocument();
    });

    it('isStaffMutating のとき select は disabled', () => {
      render(
        <TimelineDayBoard
          columns={[column({ key: 'c1', staffOptions })]}
          weekdayLabel="月"
          onChangeAssignedStaff={vi.fn()}
          isStaffMutating
        />,
      );
      expect((screen.getByTestId('tl-staff-select-c1') as HTMLSelectElement).disabled).toBe(true);
    });
  });

  // ─── G2: 訪問削除 (カード右下の ×) ───────────────────────────────────────
  describe('G2. 訪問削除 (×)', () => {
    it('× クリックで onDeleteVisit が呼ばれ、カードの onClick (患者詳細) は呼ばれない', () => {
      const onDelete = vi.fn();
      const onPatientClick = vi.fn();
      render(
        <TimelineDayBoard
          columns={[
            column({
              key: 'c1',
              visits: [visit({ id: 'v1', start_time: '09:30:00', end_time: '10:15:00' })],
            }),
          ]}
          weekdayLabel="月"
          onPatientClick={onPatientClick}
          onDeleteVisit={onDelete}
        />,
      );
      screen.getByTestId('tl-delete-visit-v1').click();
      expect(onDelete).toHaveBeenCalledWith('v1', '患者v1');
      expect(onPatientClick).not.toHaveBeenCalled();
    });

    it('onDeleteVisit 未指定 (read-only) では × を描画しない', () => {
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
      expect(screen.queryByTestId('tl-delete-visit-v1')).toBeNull();
    });

    it('極小カード (15分) には × を出さない / キャンセル済みには出す', () => {
      render(
        <TimelineDayBoard
          columns={[
            column({
              key: 'c1',
              visits: [
                visit({ id: 'tiny', start_time: '09:30:00', end_time: '09:45:00' }),
                visit({
                  id: 'cxl',
                  start_time: '11:00:00',
                  end_time: '11:45:00',
                  status: 'cancelled',
                }),
              ],
            }),
          ]}
          weekdayLabel="月"
          onDeleteVisit={vi.fn()}
        />,
      );
      expect(screen.queryByTestId('tl-delete-visit-tiny')).toBeNull();
      expect(screen.getByTestId('tl-delete-visit-cxl')).toBeInTheDocument();
    });

    it('× 追加後もカードのドラッグは生きている (div 化の回帰防止)', async () => {
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
            onDeleteVisit={vi.fn()}
          />
        </DndContext>,
      );
      const card = screen.getByTestId('tl-visit-norm');
      expect(card.tagName).toBe('DIV');
      expect(card.getAttribute('role')).toBe('button');
      const down = new MouseEvent('pointerdown', { bubbles: true, cancelable: true, button: 0 });
      Object.defineProperty(down, 'isPrimary', { value: true });
      card.dispatchEvent(down);
      await vi.waitFor(() => {
        expect(onDragStart).toHaveBeenCalledTimes(1);
        expect(String(onDragStart.mock.calls[0]![0].active.id)).toBe('tl-visit:norm');
      });
      await endDragAndFlush();
    });

    it('× の pointerdown はカードのドラッグを開始させない', async () => {
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
            onDeleteVisit={vi.fn()}
          />
        </DndContext>,
      );
      const del = screen.getByTestId('tl-delete-visit-norm');
      const down = new MouseEvent('pointerdown', { bubbles: true, cancelable: true, button: 0 });
      Object.defineProperty(down, 'isPrimary', { value: true });
      del.dispatchEvent(down);
      expect(onDragStart).not.toHaveBeenCalled();
      await endDragAndFlush();
    });

    it('カードは Enter / Space で onClick (患者詳細) 相当が発火する (button → div のキーボード維持)', () => {
      const onPatientClick = vi.fn();
      render(
        <TimelineDayBoard
          columns={[
            column({
              key: 'c1',
              visits: [visit({ id: 'v1', start_time: '09:30:00', end_time: '10:15:00' })],
            }),
          ]}
          weekdayLabel="月"
          onPatientClick={onPatientClick}
        />,
      );
      const card = screen.getByTestId('tl-visit-v1');
      expect(card.getAttribute('tabindex')).toBe('0');
      fireEvent.keyDown(card, { key: 'Enter' });
      fireEvent.keyDown(card, { key: ' ' });
      fireEvent.keyDown(card, { key: 'Escape' });
      expect(onPatientClick).toHaveBeenCalledTimes(2);
    });
  });

  // ─── G3: 「今週のみ」チップ → 固定昇格 ──────────────────────────────────
  describe('G3. 「今週のみ」チップ', () => {
    const weekOnly = visit({
      id: 'wk',
      start_time: '09:30:00',
      end_time: '11:00:00', // pills 行が出る高さ (>= TL_SHOW_PILLS_PX)
      source: 'manual_week',
    });

    it('source=manual_week のときチップが出て、クリック (confirm OK) で onPromoteWeekOnly が呼ばれる', () => {
      const onPromote = vi.fn();
      const onPatientClick = vi.fn();
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
      render(
        <TimelineDayBoard
          columns={[column({ key: 'c1', visits: [weekOnly] })]}
          weekdayLabel="月"
          onPatientClick={onPatientClick}
          onPromoteWeekOnly={onPromote}
        />,
      );
      screen.getByTestId('tl-week-only-chip-wk').click();
      expect(onPromote).toHaveBeenCalledWith('p-wk', '患者wk');
      // カード本体の onClick (患者詳細) は発火しない。
      expect(onPatientClick).not.toHaveBeenCalled();
      confirmSpy.mockRestore();
    });

    it('confirm キャンセルでは昇格しない', () => {
      const onPromote = vi.fn();
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
      render(
        <TimelineDayBoard
          columns={[column({ key: 'c1', visits: [weekOnly] })]}
          weekdayLabel="月"
          onPromoteWeekOnly={onPromote}
        />,
      );
      screen.getByTestId('tl-week-only-chip-wk').click();
      expect(onPromote).not.toHaveBeenCalled();
      confirmSpy.mockRestore();
    });

    it('onPromoteWeekOnly 未指定 (read-only) では非クリックの表示専用チップ', () => {
      render(
        <TimelineDayBoard
          columns={[column({ key: 'c1', visits: [weekOnly] })]}
          weekdayLabel="月"
        />,
      );
      const chip = screen.getByTestId('tl-week-only-chip-wk');
      expect(chip.tagName).toBe('SPAN');
    });

    it('source が manual_week でないカードにはチップを出さない', () => {
      render(
        <TimelineDayBoard
          columns={[
            column({
              key: 'c1',
              visits: [
                visit({
                  id: 'v1',
                  start_time: '09:30:00',
                  end_time: '11:00:00',
                  source: 'allocate',
                }),
              ],
            }),
          ]}
          weekdayLabel="月"
          onPromoteWeekOnly={vi.fn()}
        />,
      );
      expect(screen.queryByTestId('tl-week-only-chip-v1')).toBeNull();
    });
  });

  /**
   * T-6撤去: 旧 CourseDayTable にしか無かった 2 名体制の運用情報
   * (①/② スロット・相方の現在地・「相方未配置」警告) がカードへ移設されたことの担保。
   * これが無いとテーブル撤去で「片側しか置けていない」事故に気付けなくなる。
   */
  describe('2名体制の相方情報 (T-6撤去に伴う移設)', () => {
    const tallVisit = (over: Partial<CourseGridVisit>) =>
      visit({ id: 'v1', start_time: '09:30:00', end_time: '11:00:00', ...over });

    it('相方が別セルに配置済みなら「相方: 本店-A 15:00」を通常色で出す', () => {
      render(
        <TimelineDayBoard
          columns={[
            column({
              key: 'c1',
              visits: [
                tallVisit({
                  patient_requires_multiple_staff: true,
                  group_slot_label: 1,
                  partner_location: { kind: 'cell', cellLabel: '本店-A', time: '15:00' },
                }),
              ],
            }),
          ]}
          weekdayLabel="月"
        />,
      );
      expect(screen.getByTestId('tl-slot-mark-v1')).toHaveTextContent('①');
      const note = screen.getByTestId('tl-partner-note-v1');
      expect(note).toHaveTextContent('相方: 本店-A 15:00');
      expect(note.className).not.toContain('text-error');
    });

    it('相方がプール残存なら「複数 ① のみ (相方未配置)」を警告色で出す', () => {
      render(
        <TimelineDayBoard
          columns={[
            column({
              key: 'c1',
              visits: [
                tallVisit({
                  patient_requires_multiple_staff: true,
                  group_slot_label: 1,
                  partner_location: { kind: 'pool' },
                }),
              ],
            }),
          ]}
          weekdayLabel="月"
        />,
      );
      const note = screen.getByTestId('tl-partner-note-v1');
      expect(note).toHaveTextContent('複数 ① のみ (相方未配置)');
      expect(note.className).toContain('text-error');
      expect(screen.getByTestId('tl-slot-mark-v1').className).toContain('text-error');
    });

    it('小さいカードでも相方情報は title ツールチップで補完される', () => {
      render(
        <TimelineDayBoard
          columns={[
            column({
              key: 'c1',
              // 15分カード = 注記行を出す高さが無い
              visits: [
                visit({
                  id: 'v1',
                  start_time: '09:30:00',
                  end_time: '09:45:00',
                  patient_requires_multiple_staff: true,
                  group_slot_label: 2,
                  partner_label: '山田 花子',
                  partner_location: { kind: 'pool' },
                }),
              ],
            }),
          ]}
          weekdayLabel="月"
        />,
      );
      expect(screen.queryByTestId('tl-partner-note-v1')).toBeNull();
      const title = screen.getByTestId('tl-visit-v1').getAttribute('title') ?? '';
      expect(title).toContain('相方: プール');
      expect(title).toContain('ペア: 山田 花子');
    });

    it('通常患者 (相方なし) には ①/② も注記も出ない', () => {
      render(
        <TimelineDayBoard
          columns={[column({ key: 'c1', visits: [tallVisit({})] })]}
          weekdayLabel="月"
        />,
      );
      expect(screen.queryByTestId('tl-slot-mark-v1')).toBeNull();
      expect(screen.queryByTestId('tl-partner-note-v1')).toBeNull();
    });
  });
});
