import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { WeekOverviewVisit } from '@/components/schedule/v2/CourseWeekOverview';
import {
  WeekTimelineBoard,
  type WeekTimelineOption,
} from '@/components/schedule/timeline/WeekTimelineBoard';
import { genderPalette } from '@/lib/scheduling/timeline';

const OPTIONS: WeekTimelineOption[] = [
  { templateId: 't1', label: '稲毛A・田中 一郎' },
  { templateId: 't2', label: '稲毛B・佐藤 花子' },
];

function wv(over: Partial<WeekOverviewVisit> & { id: string; weekday: number }): WeekOverviewVisit {
  return {
    patient_id: `p-${over.id}`,
    patient_name: `患者${over.id}`,
    course_template_id: 't1',
    start_time: '09:30:00',
    ...over,
  } as WeekOverviewVisit;
}

describe('WeekTimelineBoard (全コース縦積み)', () => {
  it('全コースが縦積みでセクション表示される (切替セレクタなし)', () => {
    render(<WeekTimelineBoard options={OPTIONS} visits={[]} />);
    expect(screen.getByTestId('wtl-section-t1')).toBeInTheDocument();
    expect(screen.getByTestId('wtl-section-t2')).toBeInTheDocument();
    expect(screen.getByText('稲毛A・田中 一郎')).toBeInTheDocument();
    expect(screen.getByText('稲毛B・佐藤 花子')).toBeInTheDocument();
    expect(screen.queryByTestId('week-timeline-course-select')).toBeNull();
  });

  it('曜日ヘッダ(月〜土)と日付を出す (各セクション)', () => {
    render(
      <WeekTimelineBoard
        options={[OPTIONS[0]!]}
        visits={[]}
        weekdayDates={['7/7', '7/8', '7/9', '7/10', '7/11', '7/12']}
      />,
    );
    for (const d of ['月', '火', '水', '木', '金', '土']) {
      expect(screen.getByText(d)).toBeInTheDocument();
    }
    expect(screen.getByText('7/7')).toBeInTheDocument();
  });

  it('訪問は自コースのセクションにだけ描かれる', () => {
    render(
      <WeekTimelineBoard
        options={OPTIONS}
        visits={[
          wv({ id: 'a', weekday: 0, course_template_id: 't1' }),
          wv({ id: 'other', weekday: 0, course_template_id: 't2' }),
        ]}
      />,
    );
    // 両コース分のセクションがあり、それぞれの visit が 1 回ずつ描かれる。
    expect(screen.getByTestId('wtl-visit-a')).toBeInTheDocument();
    expect(screen.getByTestId('wtl-visit-other')).toBeInTheDocument();
  });

  it('性別でカード地色が変わる', () => {
    render(
      <WeekTimelineBoard
        options={[OPTIONS[0]!]}
        visits={[
          wv({ id: 'm', weekday: 0, patient_sex: 'male', end_time: '10:00:00' }),
          wv({ id: 'f', weekday: 1, patient_sex: 'female', end_time: '10:00:00' }),
        ]}
      />,
    );
    expect(screen.getByTestId('wtl-visit-m').style.background).toBe('var(--sched-male-bg)');
    expect(screen.getByTestId('wtl-visit-f').style.background).toBe('var(--sched-female-bg)');
  });

  it('曜日ヘッダの担当は性別色アバター + 太字名 (日ビューと同じ視覚言語)', () => {
    render(
      <WeekTimelineBoard
        options={[OPTIONS[0]!]}
        visits={[]}
        staffByWeekday={(tid, wd) =>
          wd === 0
            ? { name: '川名 千恵', sex: 'female' }
            : wd === 1
              ? { name: '田中 一郎', sex: 'male' }
              : null
        }
      />,
    );
    expect(screen.getByText('川名 千恵')).toBeInTheDocument();
    expect(screen.getByText('田中 一郎')).toBeInTheDocument();
    // アバターは頭文字 + 性別色 (女性=female パレット)。
    const avatarF = screen.getByTestId('wtl-staff-avatar-t1-0');
    expect(avatarF.textContent).toBe('川');
    expect(avatarF.style.background).toBe(genderPalette('female').bg);
    const avatarM = screen.getByTestId('wtl-staff-avatar-t1-1');
    expect(avatarM.textContent).toBe('田');
    expect(avatarM.style.background).toBe(genderPalette('male').bg);
    // 担当なしの曜日は「（未割当）」。
    expect(screen.getAllByText('（未割当）').length).toBeGreaterThan(0);
  });

  it('capacityByWeekday を渡すと n/N件 表示になる', () => {
    render(
      <WeekTimelineBoard
        options={[OPTIONS[0]!]}
        visits={[wv({ id: 'a', weekday: 0, end_time: '10:00:00' })]}
        capacityByWeekday={() => 6}
      />,
    );
    expect(screen.getByText('1/6件')).toBeInTheDocument();
  });

  it('同住所・同時刻の2名は上下重ねの90分占有ペアボックスにまとまる', () => {
    render(
      <WeekTimelineBoard
        options={[OPTIONS[0]!]}
        visits={[
          wv({
            id: 'pa',
            weekday: 0,
            start_time: '09:30:00',
            end_time: '10:05:00',
            same_address_key: 'addr1',
            patient_sex: 'female',
          }),
          wv({
            id: 'pb',
            weekday: 0,
            start_time: '09:30:00',
            end_time: '10:05:00',
            same_address_key: 'addr1',
            patient_sex: 'male',
          }),
        ]}
      />,
    );
    const box = screen.getByTestId('wtl-pair-pair:pa:pb');
    expect(box).toBeInTheDocument();
    expect(screen.getByText(/同住所 90分占有/)).toBeInTheDocument();
    // 2名とも箱の中に上下で入る (左右半分割ではない)。
    expect(screen.getByTestId('wtl-visit-pa')).toBeInTheDocument();
    expect(screen.getByTestId('wtl-visit-pb')).toBeInTheDocument();
    // ペアボックスは全幅 (レーン分割されない)。
    expect(box.style.left).toBe('2px');
    expect(box.style.right).toBe('2px');
  });

  it('同住所でも時刻が違えばペアにしない', () => {
    render(
      <WeekTimelineBoard
        options={[OPTIONS[0]!]}
        visits={[
          wv({
            id: 'x',
            weekday: 0,
            start_time: '09:30:00',
            end_time: '10:00:00',
            same_address_key: 'addr1',
          }),
          wv({
            id: 'y',
            weekday: 0,
            start_time: '13:00:00',
            end_time: '13:30:00',
            same_address_key: 'addr1',
          }),
        ]}
      />,
    );
    expect(screen.queryByTestId(/^wtl-pair-/)).toBeNull();
  });

  it('時間帯が重なる訪問は左右レーンに分かれる', () => {
    render(
      <WeekTimelineBoard
        options={[OPTIONS[0]!]}
        visits={[
          wv({ id: 'x', weekday: 0, start_time: '09:30:00', end_time: '10:30:00' }),
          wv({ id: 'y', weekday: 0, start_time: '10:00:00', end_time: '11:00:00' }),
        ]}
      />,
    );
    const x = screen.getByTestId('wtl-visit-x');
    const y = screen.getByTestId('wtl-visit-y');
    expect(x.style.width).toContain('50%');
    expect(y.style.width).toContain('50%');
    expect(x.style.left).not.toBe(y.style.left);
  });

  it('カードクリックで onPatientClick(patientId) を呼ぶ', () => {
    const onClick = vi.fn();
    render(
      <WeekTimelineBoard
        options={[OPTIONS[0]!]}
        visits={[wv({ id: 'a', weekday: 0, end_time: '10:05:00' })]}
        onPatientClick={onClick}
      />,
    );
    screen.getByTestId('wtl-visit-a').click();
    expect(onClick).toHaveBeenCalledWith('p-a');
  });

  it('終了時刻が無い訪問も既定35分で描く(落ちない)', () => {
    render(
      <WeekTimelineBoard
        options={[OPTIONS[0]!]}
        visits={[wv({ id: 'noend', weekday: 3, start_time: '11:00:00', end_time: null })]}
      />,
    );
    expect(screen.getByTestId('wtl-visit-noend')).toBeInTheDocument();
  });

  it('コース0件は案内を出す', () => {
    render(<WeekTimelineBoard options={[]} visits={[]} />);
    expect(screen.getByText(/表示対象コースがありません/)).toBeInTheDocument();
  });
});
