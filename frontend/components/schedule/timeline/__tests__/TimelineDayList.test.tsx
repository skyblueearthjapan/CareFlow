import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { CourseListItem, VisitListItem } from '@/components/schedule/WeekdayScheduleCard';
import { TimelineDayList } from '@/components/schedule/timeline/TimelineDayList';
import { genderPalette } from '@/lib/scheduling/timeline';

function v(over: Partial<VisitListItem> & { key: string }): VisitListItem {
  return {
    patient_id: `p-${over.key}`,
    start_time: '09:30:00',
    patient_name: `患者${over.key}`,
    ...over,
  } as VisitListItem;
}

function course(over: Partial<CourseListItem> & { key: string }): CourseListItem {
  return {
    title: '稲毛 A コース',
    office_name: '稲毛',
    course_code: 'A',
    staff_name: '田中 一郎',
    visits: [],
    capacity: { filled: 0, max: 6 },
    ...over,
  } as CourseListItem;
}

describe('TimelineDayList', () => {
  it('グループ見出しに拠点+コード・担当者名・n/N件を出す', () => {
    render(
      <TimelineDayList
        courses={[
          course({ key: 'c1', capacity: { filled: 2, max: 6 }, visits: [v({ key: 'a' })] }),
        ]}
      />,
    );
    expect(screen.getByText('稲毛A')).toBeInTheDocument();
    expect(screen.getByText('田中 一郎')).toBeInTheDocument();
    expect(screen.getByText('2/6件')).toBeInTheDocument();
  });

  it('列見出し(時刻/利用者/時間/住所 など)を出す', () => {
    render(<TimelineDayList courses={[course({ key: 'c1', visits: [v({ key: 'a' })] })]} />);
    expect(screen.getByText('時刻')).toBeInTheDocument();
    expect(screen.getByText('利用者')).toBeInTheDocument();
    expect(screen.getByText('住所 / 条件')).toBeInTheDocument();
  });

  it('訪問行に氏名・実動時間・住所を出す', () => {
    render(
      <TimelineDayList
        courses={[
          course({
            key: 'c1',
            visits: [
              v({
                key: 'a',
                patient_name: '青柳 あい',
                duration_min: 35,
                address: '稲毛区小仲台2-5',
              }),
            ],
          }),
        ]}
      />,
    );
    expect(screen.getByText('青柳 あい')).toBeInTheDocument();
    expect(screen.getByText('35 分')).toBeInTheDocument();
    expect(screen.getByText(/稲毛区小仲台/)).toBeInTheDocument();
  });

  it('患者名クリックで onPatientClick を呼ぶ', () => {
    const onClick = vi.fn();
    render(
      <TimelineDayList
        courses={[course({ key: 'c1', visits: [v({ key: 'a' })] })]}
        onPatientClick={onClick}
      />,
    );
    screen.getByText('患者a').click();
    expect(onClick).toHaveBeenCalledWith('p-a');
  });

  it('空きがあり容量に余裕があれば空き枠行を時刻順に差し込む', () => {
    render(
      <TimelineDayList
        courses={[
          course({
            key: 'c1',
            capacity: { filled: 1, max: 6 },
            visits: [v({ key: 'a', start_time: '09:30:00', duration_min: 35 })],
            freeGaps: [{ startMin: 13 * 60, endMin: 14 * 60, label: '13:00〜14:00' }],
          }),
        ]}
      />,
    );
    expect(screen.getByTestId('tdl-gap-780')).toBeInTheDocument();
    expect(screen.getByText(/空き時間/)).toBeInTheDocument();
  });

  it('満員(remaining<=0)のときは空き枠を出さない', () => {
    render(
      <TimelineDayList
        courses={[
          course({
            key: 'c1',
            capacity: { filled: 6, max: 6 },
            visits: [v({ key: 'a', start_time: '09:30:00', duration_min: 35 })],
            freeGaps: [{ startMin: 13 * 60, endMin: 14 * 60, label: '13:00〜14:00' }],
          }),
        ]}
      />,
    );
    expect(screen.queryByTestId('tdl-gap-780')).toBeNull();
  });

  it('連続する同住所の2名は琥珀の囲みにまとまる (M-1)', () => {
    render(
      <TimelineDayList
        courses={[
          course({
            key: 'c1',
            visits: [
              v({ key: 'sa1', patient_name: '安永 一', same_address_group_id: 'g1' }),
              v({ key: 'sa2', patient_name: '菅原 二', same_address_group_id: 'g1' }),
              v({ key: 'solo', patient_name: '田中 三' }),
            ],
          }),
        ]}
      />,
    );
    expect(screen.getByTestId('tdl-sameaddr-0')).toBeInTheDocument();
    expect(screen.getByText(/同住所（2名/)).toBeInTheDocument();
  });

  it('移動警告のある行は薄い赤背景＋行ツールチップになる (M-2)', () => {
    render(
      <TimelineDayList
        courses={[
          course({
            key: 'c1',
            visits: [
              v({
                key: 'w',
                warnings: [{ type: 'travel_time_shortage', message: '移動が厳しい' }],
              }),
            ],
          }),
        ]}
      />,
    );
    const row = screen.getByTestId('tdl-row-w');
    expect(row.className).toContain('bg-error-bg/40');
    expect(row.getAttribute('title')).toBe('移動が厳しい');
  });

  it('性別ドット/左帯の色が患者性別で変わる', () => {
    render(
      <TimelineDayList
        courses={[
          course({
            key: 'c1',
            visits: [v({ key: 'm', patient_sex: 'male' }), v({ key: 'f', patient_sex: 'female' })],
          }),
        ]}
      />,
    );
    const rm = screen.getByTestId('tdl-row-m');
    const rf = screen.getByTestId('tdl-row-f');
    expect(rm.style.borderLeftColor).toBe(genderPalette('male').bar);
    expect(rf.style.borderLeftColor).toBe(genderPalette('female').bar);
  });
});
