/**
 * WeekTimelineBoard の新人同行モード連携 (§7.1/§7.2)。
 * binding を手組みして盤の挙動 (選択トグル・重複ハイライト・常時バッジ) を確認する。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { WeekOverviewVisit } from '@/components/schedule/v2/CourseWeekOverview';
import {
  WeekTimelineBoard,
  type WeekTimelineOption,
} from '@/components/schedule/timeline/WeekTimelineBoard';
import type { AccompanimentBinding } from '@/components/schedule/timeline/accompaniment/types';

const OPTIONS: WeekTimelineOption[] = [{ templateId: 't1', label: '稲毛A' }];

function wv(over: Partial<WeekOverviewVisit> & { id: string; weekday: number }): WeekOverviewVisit {
  return {
    patient_id: `p-${over.id}`,
    patient_name: `患者${over.id}`,
    course_template_id: 't1',
    start_time: '09:30:00',
    end_time: '10:05:00',
    ...over,
  } as WeekOverviewVisit;
}

function makeBinding(over: Partial<AccompanimentBinding> = {}): AccompanimentBinding {
  return {
    active: true,
    isCourseSelected: () => false,
    isVisitSelected: () => false,
    isVisitInSelectedCourse: () => false,
    isVisitOverlapping: () => false,
    toggleCourse: vi.fn(),
    toggleVisit: vi.fn(),
    visitBadgeName: () => null,
    courseBadgeName: () => null,
    resolveCourseId: (t, wd) => `${t}:${wd}`,
    ...over,
  };
}

describe('WeekTimelineBoard 新人同行モード', () => {
  it('モード中: 曜日コース列ヘッダのクリックで toggleCourse(courseId, templateId, weekday)', () => {
    const toggleCourse = vi.fn();
    render(
      <WeekTimelineBoard
        options={OPTIONS}
        visits={[]}
        accompaniment={makeBinding({ toggleCourse })}
      />,
    );
    screen.getByTestId('wtl-course-header-t1-0').click();
    expect(toggleCourse).toHaveBeenCalledWith('t1:0', 't1', 0);
  });

  it('モード中: カードクリックは toggleVisit を呼び、onPatientClick は呼ばない', () => {
    const toggleVisit = vi.fn();
    const onPatientClick = vi.fn();
    render(
      <WeekTimelineBoard
        options={OPTIONS}
        visits={[wv({ id: 'a', weekday: 0 })]}
        onPatientClick={onPatientClick}
        accompaniment={makeBinding({ toggleVisit })}
      />,
    );
    screen.getByTestId('wtl-visit-a').click();
    expect(toggleVisit).toHaveBeenCalledWith('a');
    expect(onPatientClick).not.toHaveBeenCalled();
  });

  it('モード中: 重複カードは赤枠 (border=error)、選択カードは data 属性が付く', () => {
    render(
      <WeekTimelineBoard
        options={OPTIONS}
        visits={[wv({ id: 'a', weekday: 0 }), wv({ id: 'b', weekday: 1 })]}
        accompaniment={makeBinding({
          isVisitOverlapping: (id) => id === 'a',
          isVisitSelected: (id) => id === 'b',
        })}
      />,
    );
    expect(screen.getByTestId('wtl-visit-a').className).toContain('ring-error');
    expect(screen.getByTestId('wtl-visit-b').getAttribute('data-accompaniment-selected')).toBe(
      'true',
    );
  });

  it('モード中: 選択済みコース内のカードは個別クリック不可 (ツールチップ案内)', () => {
    const toggleVisit = vi.fn();
    render(
      <WeekTimelineBoard
        options={OPTIONS}
        visits={[wv({ id: 'a', weekday: 0 })]}
        accompaniment={makeBinding({ isVisitInSelectedCourse: () => true, toggleVisit })}
      />,
    );
    const card = screen.getByTestId('wtl-visit-a');
    card.click();
    expect(toggleVisit).not.toHaveBeenCalled();
    expect(card.getAttribute('title')).toContain('コース丸ごとに含まれています');
  });

  it('モード外: 個別同行バッジ 👥名 とコース同行ラベルを常時表示 (§7.2)', () => {
    render(
      <WeekTimelineBoard
        options={OPTIONS}
        visits={[wv({ id: 'a', weekday: 0 })]}
        accompaniment={makeBinding({
          active: false,
          visitBadgeName: (id) => (id === 'a' ? '髙梨' : null),
          courseBadgeName: (cid) => (cid === 't1:0' ? '川名' : null),
        })}
      />,
    );
    expect(screen.getByTestId('wtl-accompaniment-badge-a').textContent).toContain('髙梨');
    // コース同行はスタッフ名の右隣に 👥短縮チップで併記 (別行に積まない=高さ不変・PO要望)。
    // 「同行: 川名（新人）」の全文は title 属性に保持する。
    const courseAcc = screen.getByTestId('wtl-course-accompaniment-t1-0');
    expect(courseAcc.textContent).toContain('川名');
    expect(courseAcc.getAttribute('title')).toBe('同行: 川名（新人）');
    // モード外では選択ヘッダ (button) は出ない。
    expect(screen.queryByTestId('wtl-course-header-t1-0')).toBeNull();
  });
});
