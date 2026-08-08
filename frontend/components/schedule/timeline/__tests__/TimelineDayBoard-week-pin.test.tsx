/**
 * 週のピン (青ピン) の盤面挙動 — PO 決定 2026-08-08.
 * 仕様: docs/plans/pin-and-movability-spec.md
 *
 * 赤ピン (型のピン) は型と一致する訪問にしか刺せないため、型とズレた訪問を
 * 今の位置で守る手段が無かった。青ピンはその穴を埋める。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

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
    required_staff_count: 1,
    start_slot: '09:30',
    start_time: '10:25',
    end_time: '11:00',
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
    capacity: { filled: visits.length, max: 6 },
    staffEvents: [],
    staffOptions: [],
    key: 'col-1',
  } as TimelineCourseColumn;
}

function renderBoard(v: CourseGridVisit, onToggleWeekPin?: (id: string, next: boolean) => void) {
  return render(<TimelineDayBoard columns={[column([v])]} onToggleWeekPin={onToggleWeekPin} />);
}

describe('週のピン (青ピン)', () => {
  it('未固定の訪問はトグルが「未ピン」状態で出る', () => {
    renderBoard(visit({ id: 'v1', source: 'auto' }), vi.fn());
    const btn = screen.getByTestId('tl-week-pin-toggle-v1');
    expect(btn).toHaveAttribute('data-week-pinned', 'false');
    expect(btn).toHaveAttribute('aria-pressed', 'false');
  });

  it('型とズレた訪問 (source=auto) でも今週固定できる — 赤ピンとの決定的な違い', async () => {
    const onToggle = vi.fn();
    // 型は 13:00 / 実配置は 10:25 = 赤ピンは刺せない状態。
    renderBoard(visit({ id: 'v2', source: 'auto', master_start_time: '13:00' }), onToggle);

    await userEvent.click(screen.getByTestId('tl-week-pin-toggle-v2'));
    expect(onToggle).toHaveBeenCalledWith('v2', true);
  });

  it('今週固定済みの訪問は青い画鋲が立ち、トグルで解除できる', async () => {
    const onToggle = vi.fn();
    renderBoard(visit({ id: 'v3', source: 'manual_week' }), onToggle);

    const btn = screen.getByTestId('tl-week-pin-toggle-v3');
    expect(btn).toHaveAttribute('data-week-pinned', 'true');
    expect(document.querySelector('[data-icon="corner-week-push-pin"]')).toBeInTheDocument();

    await userEvent.click(btn);
    expect(onToggle).toHaveBeenCalledWith('v3', false);
  });

  it('week_pinned フラグだけでも青ピンが立つ — 取込 (import) の訪問を固定した状態', () => {
    // PO 決定 2026-08-09: 実体はフラグ。source は import のまま = 出所保持。
    renderBoard(visit({ id: 'v7f', source: 'import', week_pinned: true }), vi.fn());
    expect(document.querySelector('[data-icon="corner-week-push-pin"]')).toBeInTheDocument();
    const btn = screen.getByTestId('tl-week-pin-toggle-v7f');
    expect(btn).toHaveAttribute('data-week-pinned', 'true');
  });

  it('赤ピン中でも青ピンのトグルは出る（先に青を仕込んでから一括赤解除する運用のため）', async () => {
    // 旧仕様は「赤ピン中は青トグルを出さない」だったが、全件ピン留めすると青の
    // 入口が全滅し、一括赤解除前の保護を仕込めなかった (PO 指摘 2026-08-08)。
    const onToggle = vi.fn();
    renderBoard(visit({ id: 'v4', source: 'auto', is_pinned: true }), onToggle);
    const btn = screen.getByTestId('tl-week-pin-toggle-v4');
    expect(btn).toBeInTheDocument();
    await userEvent.click(btn);
    expect(onToggle).toHaveBeenCalledWith('v4', true);
  });

  it('赤と青が両方刺さっていれば画鋲が 2 本並ぶ（青は赤の左隣）', () => {
    renderBoard(visit({ id: 'v6', source: 'manual_week', is_pinned: true }), vi.fn());
    expect(document.querySelector('[data-icon="corner-push-pin"]')).toBeInTheDocument();
    const blue = document.querySelector('[data-icon="corner-week-push-pin"]');
    expect(blue).toBeInTheDocument();
    // 青は赤の左隣スロットへ避ける (right-[14px])。
    expect(blue?.getAttribute('class') ?? '').toContain('right-[14px]');
  });

  it('赤トグルは型と一致する訪問で操作でき、ズレていれば理由を示して無効', () => {
    const onTogglePin = vi.fn();
    // 一致 (fixed_visit_id あり) → 有効
    render(
      <TimelineDayBoard
        columns={[column([visit({ id: 'v7', source: 'auto', fixed_visit_id: 'pfv-7' })])]}
        onTogglePin={onTogglePin}
      />,
    );
    expect(screen.getByTestId('tl-pin-toggle-v7')).not.toBeDisabled();
  });

  it('赤トグル: 型とズレた訪問は無効で、型の時刻を理由に出す（論点1）', () => {
    render(
      <TimelineDayBoard
        columns={[
          column([
            visit({ id: 'v8', source: 'auto', fixed_visit_id: null, master_start_time: '13:00' }),
          ]),
        ]}
        onTogglePin={vi.fn()}
      />,
    );
    const btn = screen.getByTestId('tl-pin-toggle-v8');
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute(
      'title',
      '固定訪問スケジュールは 13:00 です。この時間帯ではピン留めできません',
    );
  });

  it('ハンドラ未指定 (閲覧のみ) ではトグルを描画しない', () => {
    renderBoard(visit({ id: 'v5', source: 'manual_week' }));
    expect(screen.queryByTestId('tl-week-pin-toggle-v5')).not.toBeInTheDocument();
    // 表示 (青い画鋲) は残る — 状態は読めるべきなので。
    expect(document.querySelector('[data-icon="corner-week-push-pin"]')).toBeInTheDocument();
  });
});
