/**
 * StaffWeekBoard — 週ビュー「スタッフ別」(案B・PO要望 2026-07-26) の描画契約。
 *
 * ① 行 = スタッフ (拠点→名前順)・セルにコース見出し + 訪問明細 (時刻+患者名) 常時表示
 * ② イベント (緑) と 📝ゼロ長メモがセルに出る
 * ③ 担当スタッフの居ないコースの訪問は「（担当なし）」行に出る (隠さない)
 * ④ 新人スタッフには ⚠新人 表示
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { StaffWeekBoard } from '../StaffWeekBoard';
import { COURSE_DND_MIME } from '../WeekCoursePalette';
import type { WeekOverviewVisit } from '../CourseWeekOverview';
import type { WeekOverrideRead } from '@/lib/queries/staff-overrides';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { StaffRead } from '@/lib/schemas/staff';
import type { EventRead } from '@/lib/schemas/staff-events';

const WEEK_START = new Date(2026, 6, 20); // 2026-07-20 (月)

const OFFICE_ID = '00000000-0000-4000-8000-00000000000f';
const TPL_A = '00000000-0000-4000-8000-0000000000aa';
const TPL_B = '00000000-0000-4000-8000-0000000000bb';
const STAFF_1 = '00000000-0000-4000-8000-000000000001';
const STAFF_2 = '00000000-0000-4000-8000-000000000002';

const templates = [
  { id: TPL_A, office_id: OFFICE_ID, label: 'A' },
  { id: TPL_B, office_id: OFFICE_ID, label: 'B' },
] as unknown as CourseTemplateRead[];

const staffMap = new Map<string, StaffRead>([
  [
    STAFF_1,
    {
      id: STAFF_1,
      name: '宇田川　優莉',
      primary_office_id: OFFICE_ID,
      is_trainee: false,
      status: 'active',
    } as unknown as StaffRead,
  ],
  [
    STAFF_2,
    {
      id: STAFF_2,
      name: '髙梨　桂子',
      primary_office_id: OFFICE_ID,
      is_trainee: true,
      status: 'active',
    } as unknown as StaffRead,
  ],
]);

function visit(partial: Partial<WeekOverviewVisit> & { id: string }): WeekOverviewVisit {
  return {
    patient_id: 'p-' + partial.id,
    patient_name: '患者',
    weekday: 0,
    course_template_id: TPL_A,
    start_time: '10:00',
    end_time: '10:35',
    ...partial,
  } as WeekOverviewVisit;
}

const visits: WeekOverviewVisit[] = [
  visit({ id: 'v1', patient_name: '朝倉　美夢', weekday: 0, start_time: '09:00' }),
  visit({ id: 'v2', patient_name: '川田　春菜', weekday: 0, start_time: '10:30' }),
  // 火曜はコースBを髙梨(新人)が担当
  visit({ id: 'v3', patient_name: '小川　文代', weekday: 1, course_template_id: TPL_B }),
  // 水曜のコースBは担当なし
  visit({ id: 'v4', patient_name: '担当無　太郎', weekday: 2, course_template_id: TPL_B }),
];

const assigned = new Map<string, string>([
  [`${TPL_A}:0`, STAFF_1],
  [`${TPL_B}:1`, STAFF_2],
  // `${TPL_B}:2` は未割当
]);

const events = new Map<string, EventRead[]>([
  [
    STAFF_1,
    [
      {
        id: '00000000-0000-4000-8000-00000000e001',
        date: '2026-07-21',
        title: '休み',
        start_time: '09:00',
        end_time: '18:00',
        type: 'イベント',
        note: null,
      } as EventRead,
      {
        id: '00000000-0000-4000-8000-00000000e002',
        date: '2026-07-25',
        title: '清水様：歯科薬お渡し',
        start_time: '00:00',
        end_time: '00:00',
        type: 'イベント',
        note: null,
      } as EventRead,
    ],
  ],
]);

function renderBoard() {
  return render(
    <StaffWeekBoard
      templates={templates}
      officeNameById={new Map([[OFFICE_ID, '稲毛']])}
      visits={visits}
      assignedStaffByTemplateWeekday={assigned}
      staffMap={staffMap}
      staffEventsByStaff={events}
      weekStart={WEEK_START}
      onPatientClick={vi.fn()}
    />,
  );
}

describe('StaffWeekBoard', () => {
  it('① スタッフ行にコース見出しと訪問明細 (時刻+患者名) が常時表示される', () => {
    renderBoard();
    const row = screen.getByTestId(`staff-week-row-${STAFF_1}`);
    expect(within(row).getByText(/優莉/)).toBeInTheDocument();
    const monCell = screen.getByTestId(`staff-week-cell-${STAFF_1}-0`);
    expect(within(monCell).getByText('稲毛 A')).toBeInTheDocument();
    expect(within(monCell).getByText(/美夢/)).toBeInTheDocument();
    expect(within(monCell).getByText(/09:00〜10:35/)).toBeInTheDocument();
    // 列ヘッダに日付
    expect(screen.getByText('7/20（月）')).toBeInTheDocument();
  });

  it('② イベント (緑) と 📝メモがセルに出る', () => {
    renderBoard();
    const tueCell = screen.getByTestId(`staff-week-cell-${STAFF_1}-1`);
    expect(within(tueCell).getByText(/09:00〜18:00 休み/)).toBeInTheDocument();
    const satCell = screen.getByTestId(`staff-week-cell-${STAFF_1}-5`);
    expect(within(satCell).getByText(/📝 清水様：歯科薬お渡し/)).toBeInTheDocument();
  });

  it('③ 担当なしの訪問は「（担当なし）」行に出る', () => {
    renderBoard();
    const row = screen.getByTestId('staff-week-row-__unassigned__');
    expect(within(row).getByText('（担当なし）')).toBeInTheDocument();
    expect(within(row).getByText(/担当無/)).toBeInTheDocument();
  });

  it('④ 新人スタッフに ⚠新人 表示', () => {
    renderBoard();
    const row = screen.getByTestId(`staff-week-row-${STAFF_2}`);
    expect(within(row).getByText(/⚠新人/)).toBeInTheDocument();
    expect(within(row).getByText(/文代/)).toBeInTheDocument();
  });

  // ─── 職員スケジュールタブ昇格 (2026-08-20・kaipoke-event-two-way-design.md §6-c) ──

  it('⑤ onAddEvent 指定時: セルの＋クリックで (staffId, date) が飛ぶ・担当なし行には出ない', () => {
    const onAddEvent = vi.fn();
    render(
      <StaffWeekBoard
        templates={templates}
        officeNameById={new Map([[OFFICE_ID, '稲毛']])}
        visits={visits}
        assignedStaffByTemplateWeekday={assigned}
        staffMap={staffMap}
        staffEventsByStaff={events}
        weekStart={WEEK_START}
        onAddEvent={onAddEvent}
      />,
    );
    screen.getByTestId(`staff-week-add-${STAFF_1}-2`).click();
    expect(onAddEvent).toHaveBeenCalledTimes(1);
    const [staffId, date] = onAddEvent.mock.calls[0]!;
    expect(staffId).toBe(STAFF_1);
    expect((date as Date).getDate()).toBe(22); // 2026-07-20(月) + 2 = 水曜
    // 担当なし行には＋を出さない (イベントはスタッフ紐付きが正典)
    expect(screen.queryByTestId('staff-week-add-__unassigned__-0')).not.toBeInTheDocument();
  });

  it('⑥ onEventClick 指定時: イベント帯クリックで (event, staffId) が飛ぶ', () => {
    const onEventClick = vi.fn();
    render(
      <StaffWeekBoard
        templates={templates}
        officeNameById={new Map([[OFFICE_ID, '稲毛']])}
        visits={visits}
        assignedStaffByTemplateWeekday={assigned}
        staffMap={staffMap}
        staffEventsByStaff={events}
        weekStart={WEEK_START}
        onEventClick={onEventClick}
      />,
    );
    screen.getByTestId('staff-week-event-00000000-0000-4000-8000-00000000e001').click();
    expect(onEventClick).toHaveBeenCalledTimes(1);
    expect(onEventClick.mock.calls[0]![0].id).toBe('00000000-0000-4000-8000-00000000e001');
    expect(onEventClick.mock.calls[0]![1]).toBe(STAFF_1);
  });

  it('⑦ showAllStaff: 訪問もイベントも無い在籍スタッフの行も出る', () => {
    render(
      <StaffWeekBoard
        templates={templates}
        officeNameById={new Map([[OFFICE_ID, '稲毛']])}
        visits={[]}
        assignedStaffByTemplateWeekday={new Map()}
        staffMap={staffMap}
        staffEventsByStaff={new Map()}
        weekStart={WEEK_START}
        showAllStaff
      />,
    );
    expect(screen.getByTestId(`staff-week-row-${STAFF_1}`)).toBeInTheDocument();
    expect(screen.getByTestId(`staff-week-row-${STAFF_2}`)).toBeInTheDocument();
    expect(screen.queryByTestId('staff-week-empty')).not.toBeInTheDocument();
  });

  // ─── 週空間 A1 (weekly-space-design.md §4): コース貼り付け DnD + 休み表示 ──

  const COURSE_ID = '00000000-0000-4000-8000-00000000c001';

  const makeDataTransfer = (payload: object | null) => ({
    types: payload ? [COURSE_DND_MIME] : [],
    getData: (t: string) => (payload && t === COURSE_DND_MIME ? JSON.stringify(payload) : ''),
    setData: vi.fn(),
    dropEffect: '',
    effectAllowed: '',
  });

  it('⑧ offByStaffWeekday: 休みバッジがセルに出る', () => {
    render(
      <StaffWeekBoard
        templates={templates}
        officeNameById={new Map([[OFFICE_ID, '稲毛']])}
        visits={visits}
        assignedStaffByTemplateWeekday={assigned}
        staffMap={staffMap}
        staffEventsByStaff={new Map()}
        weekStart={WEEK_START}
        offByStaffWeekday={
          new Map([
            [
              `${STAFF_1}:0`,
              {
                id: 'o1',
                staff_id: STAFF_1,
                date: '2026-07-20',
                weekday: 0,
                type: '休み',
                start_time: null,
                end_time: null,
                note: '私用',
              } as WeekOverrideRead,
            ],
          ])
        }
      />,
    );
    const badge = screen.getByTestId(`staff-week-off-${STAFF_1}-0`);
    expect(badge).toHaveTextContent('休み');
    // 他セルには出ない
    expect(screen.queryByTestId(`staff-week-off-${STAFF_1}-1`)).not.toBeInTheDocument();
  });

  it('⑨ onCourseDrop: セルへのドロップで (courseId, staffId, weekday) が飛ぶ', () => {
    const onCourseDrop = vi.fn();
    render(
      <StaffWeekBoard
        templates={templates}
        officeNameById={new Map([[OFFICE_ID, '稲毛']])}
        visits={visits}
        assignedStaffByTemplateWeekday={assigned}
        staffMap={staffMap}
        staffEventsByStaff={new Map()}
        weekStart={WEEK_START}
        onCourseDrop={onCourseDrop}
      />,
    );
    const cell = screen.getByTestId(`staff-week-cell-${STAFF_1}-0`);
    fireEvent.drop(cell, {
      dataTransfer: makeDataTransfer({ courseId: COURSE_ID, weekday: 0 }),
    });
    expect(onCourseDrop).toHaveBeenCalledWith(COURSE_ID, STAFF_1, 0);
    // 担当なし行はドロップ先にならない
    onCourseDrop.mockClear();
    const unassignedCell = screen.getByTestId('staff-week-cell-__unassigned__-2');
    fireEvent.drop(unassignedCell, {
      dataTransfer: makeDataTransfer({ courseId: COURSE_ID, weekday: 2 }),
    });
    expect(onCourseDrop).not.toHaveBeenCalled();
  });

  it('⑩ courseIdByTemplateWeekday: コース帯チップがドラッグ可能になり payload を積む', () => {
    const onCourseDrop = vi.fn();
    const onCourseDragChange = vi.fn();
    render(
      <StaffWeekBoard
        templates={templates}
        officeNameById={new Map([[OFFICE_ID, '稲毛']])}
        visits={visits}
        assignedStaffByTemplateWeekday={assigned}
        staffMap={staffMap}
        staffEventsByStaff={new Map()}
        weekStart={WEEK_START}
        onCourseDrop={onCourseDrop}
        onCourseDragChange={onCourseDragChange}
        courseIdByTemplateWeekday={new Map([[`${TPL_A}:0`, COURSE_ID]])}
      />,
    );
    const chip = screen.getByTestId(`staff-week-course-chip-${TPL_A}-0`);
    expect(chip).toHaveAttribute('draggable', 'true');
    const dt = makeDataTransfer(null);
    fireEvent.dragStart(chip, { dataTransfer: dt });
    expect(dt.setData).toHaveBeenCalledWith(
      COURSE_DND_MIME,
      JSON.stringify({ courseId: COURSE_ID, weekday: 0 }),
    );
    expect(onCourseDragChange).toHaveBeenCalledWith({ courseId: COURSE_ID, weekday: 0 });
    // course_id が引けないチップ (火曜B) はドラッグ不可のまま
    const tueChip = screen.getByTestId(`staff-week-course-chip-${TPL_B}-1`);
    expect(tueChip).toHaveAttribute('draggable', 'false');
  });
});
