import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { WeekOverviewVisit } from '@/components/schedule/v2/CourseWeekOverview';
import {
  WeekTimelineBoard,
  type WeekTimelineOption,
} from '@/components/schedule/timeline/WeekTimelineBoard';

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

describe('WeekTimelineBoard', () => {
  it('曜日ヘッダ(月〜土)と日付を出す', () => {
    render(
      <WeekTimelineBoard
        selectedTemplateId="t1"
        options={OPTIONS}
        onSelectTemplate={() => {}}
        visits={[]}
        weekdayDates={['7/7', '7/8', '7/9', '7/10', '7/11', '7/12']}
      />,
    );
    for (const d of ['月', '火', '水', '木', '金', '土']) {
      expect(screen.getByText(d)).toBeInTheDocument();
    }
    expect(screen.getByText('7/7')).toBeInTheDocument();
  });

  it('選択中コースの訪問だけを曜日列に描く', () => {
    render(
      <WeekTimelineBoard
        selectedTemplateId="t1"
        options={OPTIONS}
        onSelectTemplate={() => {}}
        visits={[
          wv({ id: 'a', weekday: 0, course_template_id: 't1' }),
          wv({ id: 'b', weekday: 2, course_template_id: 't1' }),
          wv({ id: 'other', weekday: 0, course_template_id: 't2' }), // 別コース → 出ない
        ]}
      />,
    );
    expect(screen.getByTestId('wtl-visit-a')).toBeInTheDocument();
    expect(screen.getByTestId('wtl-visit-b')).toBeInTheDocument();
    expect(screen.queryByTestId('wtl-visit-other')).toBeNull();
  });

  it('性別でカード地色が変わる', () => {
    render(
      <WeekTimelineBoard
        selectedTemplateId="t1"
        options={OPTIONS}
        onSelectTemplate={() => {}}
        visits={[
          wv({ id: 'm', weekday: 0, patient_sex: 'male', end_time: '10:00:00' }),
          wv({ id: 'f', weekday: 1, patient_sex: 'female', end_time: '10:00:00' }),
        ]}
      />,
    );
    expect(screen.getByTestId('wtl-visit-m').style.background).toBe('var(--sched-male-bg)');
    expect(screen.getByTestId('wtl-visit-f').style.background).toBe('var(--sched-female-bg)');
  });

  it('capacityByWeekday を渡すと n/N件 表示になる', () => {
    render(
      <WeekTimelineBoard
        selectedTemplateId="t1"
        options={OPTIONS}
        onSelectTemplate={() => {}}
        visits={[wv({ id: 'a', weekday: 0, end_time: '10:00:00' })]}
        capacityByWeekday={() => 6}
      />,
    );
    expect(screen.getByText('1/6件')).toBeInTheDocument();
  });

  it('時間帯が重なる訪問は左右レーンに分かれる', () => {
    render(
      <WeekTimelineBoard
        selectedTemplateId="t1"
        options={OPTIONS}
        onSelectTemplate={() => {}}
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

  it('コース選択で onSelectTemplate を呼ぶ', () => {
    const onSel = vi.fn();
    render(
      <WeekTimelineBoard
        selectedTemplateId="t1"
        options={OPTIONS}
        onSelectTemplate={onSel}
        visits={[]}
      />,
    );
    const sel = screen.getByTestId('week-timeline-course-select') as HTMLSelectElement;
    sel.value = 't2';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    expect(onSel).toHaveBeenCalledWith('t2');
  });

  it('カードクリックで onPatientClick(patientId) を呼ぶ', () => {
    const onClick = vi.fn();
    render(
      <WeekTimelineBoard
        selectedTemplateId="t1"
        options={OPTIONS}
        onSelectTemplate={() => {}}
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
        selectedTemplateId="t1"
        options={OPTIONS}
        onSelectTemplate={() => {}}
        visits={[wv({ id: 'noend', weekday: 3, start_time: '11:00:00', end_time: null })]}
      />,
    );
    expect(screen.getByTestId('wtl-visit-noend')).toBeInTheDocument();
  });
});
