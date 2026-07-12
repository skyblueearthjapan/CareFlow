/**
 * useAccompanimentController の選択ロジック単体テスト (§7.1)。
 * query/mutation フックはモックして、選択→実効集合→重複→PUT ペイロードを検証する。
 */
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  useAccompanimentController,
  type UseAccompanimentControllerParams,
} from '../useAccompanimentController';
import type { AccompanimentWeekVisit } from '../types';
import type { StaffRead } from '@/lib/schemas/staff';
import * as queries from '@/lib/queries/trainee_accompaniments';

vi.mock('@/lib/queries/trainee_accompaniments', () => ({
  useTraineeAccompaniments: vi.fn(),
  useUpdateTraineeAccompaniments: vi.fn(),
}));

const mutate = vi.fn();

function trainee(id: string, name: string): StaffRead {
  return { id, name, status: 'active', role: 'staff', is_trainee: true } as StaffRead;
}

function visit(
  over: Partial<AccompanimentWeekVisit> & { visitId: string; courseId: string },
): AccompanimentWeekVisit {
  return {
    patientId: `p-${over.visitId}`,
    patientName: `患者${over.visitId}`,
    weekday: 0,
    courseTemplateId: 't1',
    courseLabel: '稲毛A',
    startMin: 600,
    endMin: 630,
    ...over,
  };
}

function setup(params: Partial<UseAccompanimentControllerParams> = {}, weekVisits: AccompanimentWeekVisit[] = []) {
  return renderHook(() =>
    useAccompanimentController({
      isoYear: 2026,
      isoWeek: 29,
      canEdit: true,
      trainees: [trainee('s1', '髙梨')],
      weekVisits,
      resolveCourseId: (t, wd) => `${t}:${wd}`,
      weekdayDateLabel: (wd) => `d${wd}`,
      ...params,
    }),
  );
}

beforeEach(() => {
  mutate.mockReset();
  vi.mocked(queries.useTraineeAccompaniments).mockReturnValue({
    data: [],
    isLoading: false,
  } as unknown as ReturnType<typeof queries.useTraineeAccompaniments>);
  vi.mocked(queries.useUpdateTraineeAccompaniments).mockReturnValue({
    mutate,
    isPending: false,
  } as unknown as ReturnType<typeof queries.useUpdateTraineeAccompaniments>);
});

describe('useAccompanimentController', () => {
  it('canEdit + 新人1人以上で available、enter で 1人自動選択', () => {
    const { result } = setup();
    expect(result.current.available).toBe(true);
    expect(result.current.active).toBe(false);
    act(() => result.current.enter());
    expect(result.current.active).toBe(true);
    expect(result.current.bar?.selectedTraineeId).toBe('s1');
  });

  it('コース選択でそのコース内の全訪問が実効選択に入る', () => {
    const weekVisits = [
      visit({ visitId: 'v1', courseId: 'c1' }),
      visit({ visitId: 'v2', courseId: 'c1', startMin: 700, endMin: 730 }),
      visit({ visitId: 'v3', courseId: 'c2', weekday: 1 }),
    ];
    const { result } = setup({}, weekVisits);
    act(() => result.current.enter());
    act(() => result.current.binding.toggleCourse('c1', 't1', 0));
    expect(result.current.binding.isVisitSelected('v1')).toBe(true);
    expect(result.current.binding.isVisitSelected('v2')).toBe(true);
    expect(result.current.binding.isVisitSelected('v3')).toBe(false);
    expect(result.current.binding.isVisitInSelectedCourse('v1')).toBe(true);
    expect(result.current.bar?.courseCount).toBe(1);
  });

  it('コース内が時間重複すると確定不可・警告メッセージが出る', () => {
    const weekVisits = [
      visit({ visitId: 'v1', courseId: 'c1', startMin: 600, endMin: 660 }),
      visit({ visitId: 'v2', courseId: 'c1', startMin: 630, endMin: 690 }),
    ];
    const { result } = setup({}, weekVisits);
    act(() => result.current.enter());
    act(() => result.current.binding.toggleCourse('c1', 't1', 0));
    expect(result.current.binding.isVisitOverlapping('v1')).toBe(true);
    expect(result.current.bar?.canConfirm).toBe(false);
    expect(result.current.bar?.overlapMessages.length).toBeGreaterThan(0);
  });

  it('確定で PUT ペイロードを組む (既定チェック時は defaults も送る)', () => {
    const weekVisits = [
      visit({ visitId: 'v1', courseId: 'c1' }),
      visit({ visitId: 'v3', courseId: 'c2', weekday: 1, startMin: 780, endMin: 840 }),
    ];
    const { result } = setup({}, weekVisits);
    act(() => result.current.enter());
    act(() => result.current.binding.toggleCourse('c1', 't1', 0));
    act(() => result.current.binding.toggleVisit('v3'));
    act(() => result.current.bar?.onToggleSetDefault(true));
    act(() => result.current.bar?.onConfirm());
    expect(mutate).toHaveBeenCalledTimes(1);
    const payload = mutate.mock.calls[0]![0];
    expect(payload.trainee_staff_id).toBe('s1');
    expect(payload.course_ids).toEqual(['c1']);
    expect(payload.visit_ids).toEqual(['v3']);
    expect(payload.defaults).toEqual([{ weekday: 0, course_template_id: 't1' }]);
  });

  it('既定チェックなしでは defaults=null (既定に触れない)', () => {
    const weekVisits = [visit({ visitId: 'v1', courseId: 'c1' })];
    const { result } = setup({}, weekVisits);
    act(() => result.current.enter());
    act(() => result.current.binding.toggleCourse('c1', 't1', 0));
    act(() => result.current.bar?.onConfirm());
    expect(mutate.mock.calls[0]![0].defaults).toBeNull();
  });

  it('選択済みコース内の訪問は個別トグル不可', () => {
    const weekVisits = [visit({ visitId: 'v1', courseId: 'c1' })];
    const { result } = setup({}, weekVisits);
    act(() => result.current.enter());
    act(() => result.current.binding.toggleCourse('c1', 't1', 0));
    act(() => result.current.binding.toggleVisit('v1')); // no-op
    expect(result.current.bar?.visitCount).toBe(0);
  });
});
