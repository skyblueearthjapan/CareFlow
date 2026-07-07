import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { CourseGridVisit } from '@/components/schedule/v2/CourseDayTable';
import {
  TimelineDayBoard,
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

  it('会議・イベントを全幅帯 + カイポケ反映外バッジで描く', () => {
    render(
      <TimelineDayBoard
        columns={[
          column({
            key: 'c1',
            staffEvents: [
              {
                id: 'e1',
                date: '2026-07-07',
                title: '接遇研修',
                start_time: '13:00',
                end_time: '13:45',
                type: '研修',
              } as TimelineCourseColumn['staffEvents'][number],
            ],
          }),
        ]}
        weekdayLabel="月"
      />,
    );
    expect(screen.getByText('研修: 接遇研修')).toBeInTheDocument();
    expect(screen.getByText('カイポケ反映外')).toBeInTheDocument();
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

  it('コース0件は案内を出す', () => {
    render(<TimelineDayBoard columns={[]} weekdayLabel="日" />);
    expect(screen.getByText(/表示対象コースがありません/)).toBeInTheDocument();
  });
});
