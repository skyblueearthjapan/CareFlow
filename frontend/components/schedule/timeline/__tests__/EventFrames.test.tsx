/**
 * スタッフ枠/イベント行 (PO確定 2026-07-26) — イベントを盤面の中に編み込む描画契約。
 *
 * ① 日リスト: コース無し・イベントありスタッフが「枠」グループとして出る (📝含む)
 * ② 日タイムライン: スタッフ枠の列ヘッダと本体が出る
 * ③ 週リスト: グリッド先頭に「イベント」専用行が出る
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';

import { TimelineDayList } from '../TimelineDayList';
import {
  TimelineDayBoard,
  type StaffEventFrame,
  type TimelineCourseColumn,
} from '../TimelineDayBoard';
import { CourseWeekOverview } from '@/components/schedule/v2/CourseWeekOverview';
import type { StaffRead } from '@/lib/schemas/staff';
import type { EventRead } from '@/lib/schemas/staff-events';

const STAFF_1 = '00000000-0000-4000-8000-000000000001';

const staff = {
  id: STAFF_1,
  name: '宇田川　優莉',
  sex: 'female',
} as unknown as StaffRead;

function ev(partial: Partial<EventRead> & { id: string }): EventRead {
  return {
    date: '2026-07-21',
    title: '休み',
    start_time: '09:00',
    end_time: '18:00',
    type: 'イベント',
    note: null,
    ...partial,
  } as EventRead;
}

const frames: StaffEventFrame[] = [
  {
    staff,
    events: [
      ev({ id: '00000000-0000-4000-8000-00000000e001' }),
      ev({
        id: '00000000-0000-4000-8000-00000000e002',
        title: '清水様：歯科薬お渡し',
        start_time: '00:00',
        end_time: '00:00',
      }),
    ],
  },
];

describe('イベントのスタッフ枠', () => {
  it('① 日リスト: スタッフ枠グループが出る (休み + 📝メモ)', () => {
    render(<TimelineDayList courses={[]} staffFrames={frames} />);
    const frame = screen.getByTestId(`day-list-staff-frame-${STAFF_1}`);
    expect(within(frame).getByText(/優莉/)).toBeInTheDocument();
    expect(within(frame).getByText(/イベント 2件/)).toBeInTheDocument();
    expect(within(frame).getByText('休み')).toBeInTheDocument();
    expect(within(frame).getByText(/09:00〜18:00/)).toBeInTheDocument();
    expect(within(frame).getByText('📝')).toBeInTheDocument();
    expect(within(frame).getByText(/歯科薬お渡し/)).toBeInTheDocument();
  });

  it('② 日タイムライン: スタッフ枠の列 (ヘッダ+本体) が出る', () => {
    render(<TimelineDayBoard columns={[]} staffFrames={frames} weekdayLabel="火" />);
    expect(screen.getByTestId(`tl-staff-frame-header-${STAFF_1}`)).toBeInTheDocument();
    const header = screen.getByTestId(`tl-staff-frame-header-${STAFF_1}`);
    expect(within(header).getByText(/優莉/)).toBeInTheDocument();
    expect(within(header).getByText('2件')).toBeInTheDocument();
    // 本体列 (イベント帯 + 📝チップ)
    const body = screen.getByTestId(`tl-staff-frame-${STAFF_1}`);
    expect(within(body).getByText('休み')).toBeInTheDocument();
    expect(within(body).getByTestId(`tl-memos-staff-frame-${STAFF_1}`)).toBeInTheDocument();
  });

  it('④ 日タイムライン: 新人担当コースは select に現在値として名前（新人）が出る', () => {
    // 候補一覧 (staffOptions) から新人は除外されるが (らく助発の割当封鎖)、
    // カイポケ取込で担当になったコースが（未割当）に見えてはいけない (PO報告 2026-07-26)。
    const trainee = {
      id: STAFF_1,
      name: '髙梨桂子',
      is_trainee: true,
    } as unknown as StaffRead;
    const col = {
      key: 'tpl-e:2',
      template: { id: 'tpl-e', label: 'E' },
      course: { id: 'c-e', assigned_staff_id: STAFF_1 },
      officeName: '稲毛',
      visits: [],
      assignedStaff: trainee,
      assignedStaffMissing: false,
      freeGaps: [],
      capacity: { filled: 0, max: 6 },
      staffEvents: [],
      staffOptions: [],
    } as unknown as TimelineCourseColumn;
    render(
      <TimelineDayBoard
        columns={[col]}
        weekdayLabel="水"
        onChangeAssignedStaff={vi.fn()}
      />,
    );
    const select = screen.getByTestId('tl-staff-select-tpl-e:2') as HTMLSelectElement;
    expect(select.value).toBe(STAFF_1);
    expect(screen.getByText(/髙梨桂子（新人）/)).toBeInTheDocument();
  });

  it('③ 週リスト: グリッド先頭に「イベント」専用行が出る', () => {
    const OFFICE_ID = '00000000-0000-4000-8000-00000000000f';
    const tpl = {
      id: '00000000-0000-4000-8000-0000000000aa',
      office_id: OFFICE_ID,
      label: 'A',
      capacity_mon: 6,
      capacity_tue: 6,
      capacity_wed: 6,
      capacity_thu: 6,
      capacity_fri: 6,
      capacity_sat: 6,
      capacity_sun: 0,
    };
    render(
      <CourseWeekOverview
        templates={[tpl] as unknown as Parameters<typeof CourseWeekOverview>[0]['templates']}
        officeNameById={new Map([[OFFICE_ID, '稲毛']])}
        visits={[]}
        onJumpToDay={vi.fn()}
        eventFramesByWeekday={new Map([[1, frames]])}
      />,
    );
    expect(screen.getByTestId('course-week-overview-row-header-events')).toBeInTheDocument();
    const cell = screen.getByTestId('course-week-overview-events-cell-1');
    // 2イベント (休み + 📝) それぞれの行にスタッフ名が出る
    expect(within(cell).getAllByText(/優莉/).length).toBe(2);
    expect(within(cell).getByText(/09:00-18:00 休み/)).toBeInTheDocument();
    // 火曜以外のセルは空
    const monCell = screen.getByTestId('course-week-overview-events-cell-0');
    expect(within(monCell).queryByText(/休み/)).not.toBeInTheDocument();
  });
});
