/**
 * 重なり (lanes ≥ 2) カードの氏名可読性 — mac-ui-crossplatform-design.md §2-B1。
 *
 * 狭い画面 (MacBook 1280〜1440px) では列が最小幅 172px に張り付き、重なり訪問で
 * 等分されたカードは ≈80px しか無い。そこで
 *   1. 等分カードは氏名を一段小さく (2 lanes=12px / 3 lanes 以上=11px)
 *   2. 高さがあれば (≥ TL_SHOW_SVC_PX) 氏名を 2 行まで折り返す (truncate しない)
 *   3. 2 行化したカードは時刻行を「さらに高さがある時」だけ出す。種別は 3 等分以上で省略
 *   4. 単独カード (lanes=1) は従来どおり 13px・truncate・種別あり
 *   5. キャンセル済みは 2 行化せず「キャンセル」を必ず出す (レビュー MED-1)
 *   6. 同住所ペア枠のメンバー行も lanes ≥ 2 なら 11px・2 行・時刻バッジ無し (レビュー HIGH-2)
 */
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { CourseGridVisit } from '@/components/schedule/v2/courseGrid';
import {
  TimelineDayBoard,
  type TimelineCourseColumn,
} from '@/components/schedule/timeline/TimelineDayBoard';

function visit(over: Partial<CourseGridVisit> & { id: string }): CourseGridVisit {
  return {
    patient_id: `p-${over.id}`,
    patient_name: `患者${over.id}`,
    patient_address: null,
    patient_requires_multiple_staff: false,
    patient_sex_restriction_label: null,
    patient_time_type: '定期',
    required_staff_count: 1,
    start_slot: '09:30',
    ...over,
  } as CourseGridVisit;
}

function column(visits: CourseGridVisit[]): TimelineCourseColumn {
  return {
    template: { id: 't1', office_id: 'o1', label: 'A' } as TimelineCourseColumn['template'],
    course: { id: 'c1', assigned_staff_id: 's1' } as TimelineCourseColumn['course'],
    officeName: '稲毛',
    visits,
    assignedStaff: {
      id: 's1',
      name: '田中 一郎',
      sex: 'male',
    } as TimelineCourseColumn['assignedStaff'],
    freeGaps: [],
    capacity: { filled: 0, max: 6 },
    staffEvents: [],
    staffOptions: [],
    key: 'c1',
  };
}

function renderVisits(visits: CourseGridVisit[]) {
  return render(<TimelineDayBoard columns={[column(visits)]} weekdayLabel="月" />);
}

function nameEl(cardId: string, name: string) {
  return within(screen.getByTestId(`tl-visit-${cardId}`)).getByText(name);
}

describe('TimelineDayBoard — 重なりカードの氏名 (§2-B1)', () => {
  it('単独カード (lanes=1) は従来どおり 13px・truncate・種別あり', () => {
    renderVisits([visit({ id: 'a', start_time: '09:30:00', end_time: '10:30:00' })]);
    const el = nameEl('a', '患者a');
    expect(el.className).toContain('text-[13px]');
    expect(el.className).toContain('truncate');
    expect(el.className).not.toContain('line-clamp-2');
    expect(within(screen.getByTestId('tl-visit-a')).getByText('定期')).toBeInTheDocument();
  });

  it('2 lanes の 60 分カードは 12px・2 行折り返し・時刻行と種別あり', () => {
    renderVisits([
      visit({ id: 'a', start_time: '09:30:00', end_time: '10:30:00' }),
      visit({ id: 'b', start_time: '10:00:00', end_time: '11:00:00' }),
    ]);
    const el = nameEl('a', '患者a');
    expect(el.className).toContain('text-[12px]');
    expect(el.className).toContain('line-clamp-2');
    expect(el.className).toContain('break-words');
    expect(el.className).not.toContain('truncate');
    const card = screen.getByTestId('tl-visit-a');
    // 60 分 = 101px ≥ 62 → 時刻行は出る。2 等分なら種別 (定期) も出す (truncate 任せ)。
    expect(within(card).getByText(/09:30・60分/)).toBeInTheDocument();
    expect(within(card).getByText('定期')).toBeInTheDocument();
    expect(card.className).toContain('px-1.5');
  });

  it('2 lanes の 30 分カード (49px) は氏名 2 行のみで時刻行を出さない (溢れ防止)', () => {
    renderVisits([
      visit({ id: 'a', start_time: '09:30:00', end_time: '10:00:00' }),
      visit({ id: 'b', start_time: '09:45:00', end_time: '10:15:00' }),
    ]);
    expect(nameEl('a', '患者a').className).toContain('line-clamp-2');
    expect(within(screen.getByTestId('tl-visit-a')).queryByText(/09:30・30分/)).toBeNull();
  });

  it('3 lanes 以上は 11px で種別を省略する', () => {
    renderVisits([
      visit({ id: 'a', start_time: '09:30:00', end_time: '10:30:00' }),
      visit({ id: 'b', start_time: '09:40:00', end_time: '10:40:00' }),
      visit({ id: 'c', start_time: '09:50:00', end_time: '10:50:00' }),
    ]);
    expect(nameEl('a', '患者a').className).toContain('text-[11px]');
    const card = screen.getByTestId('tl-visit-a');
    expect(within(card).getByText(/09:30・60分/)).toBeInTheDocument();
    expect(within(card).queryByText('定期')).toBeNull();
  });

  it('キャンセル済みの 2 lanes 30 分カードは 2 行化せず「キャンセル」を出す', () => {
    renderVisits([
      visit({ id: 'a', start_time: '09:30:00', end_time: '10:00:00', status: 'cancelled' }),
      visit({ id: 'b', start_time: '09:45:00', end_time: '10:15:00' }),
    ]);
    const el = nameEl('a', '患者a');
    expect(el.className).toContain('truncate');
    expect(el.className).not.toContain('line-clamp-2');
    expect(within(screen.getByTestId('tl-visit-a')).getByText('キャンセル')).toBeInTheDocument();
  });

  it('同住所ペア枠のメンバー行も lanes ≥ 2 なら 11px・2 行・時刻バッジ無し', () => {
    renderVisits([
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
      // ペア枠 (16:00→17:30 占有) と重なる単独訪問 → ペア枠は 2 lanes に等分される。
      visit({ id: 'x', start_time: '16:20:00', end_time: '17:00:00' }),
    ]);
    const box = screen.getByTestId('tl-pair-pair:sa1:sa2');
    expect(box.style.width).toContain('50%');
    const row = screen.getByTestId('tl-visit-sa1');
    const name = within(row).getByText('安永 一');
    expect(name.className).toContain('text-[11px]');
    expect(name.className).toContain('line-clamp-2');
    expect(within(row).queryByText(/16:00・35分/)).toBeNull();
  });

  it('同住所ペア枠が単独 (lanes=1) なら従来どおり 12px・truncate・時刻バッジあり', () => {
    renderVisits([
      visit({
        id: 'sa1',
        patient_name: '安永 一',
        start_time: '16:00:00',
        end_time: '16:35:00',
        same_address_group_id: 'g1',
      }),
      visit({
        id: 'sa2',
        patient_name: '菅原 二',
        start_time: '16:00:00',
        end_time: '16:35:00',
        same_address_group_id: 'g1',
      }),
    ]);
    const row = screen.getByTestId('tl-visit-sa1');
    const name = within(row).getByText('安永 一');
    expect(name.className).toContain('text-[12px]');
    expect(name.className).toContain('truncate');
    expect(within(row).getByText(/16:00・35分/)).toBeInTheDocument();
  });
});
