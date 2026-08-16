/**
 * useAccompanimentController の選択ロジック単体テスト (§7.1)。
 * query/mutation フックはモックして、選択→実効集合→重複→PUT ペイロードを検証する。
 * 一般化 (general-accompaniment-design.md §4) で追加した対象二択・重複 422 の理由表示・
 * NG/性別の確認フロー・複数名バッジもここで押さえる。
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  useAccompanimentController,
  type UseAccompanimentControllerParams,
} from '../useAccompanimentController';
import type { AccompanimentWeekVisit } from '../types';
import type { StaffRead } from '@/lib/schemas/staff';
import { ApiError } from '@/lib/api-client';
import * as queries from '@/lib/queries/trainee_accompaniments';

vi.mock('@/lib/queries/trainee_accompaniments', () => ({
  useTraineeAccompaniments: vi.fn(),
  useUpdateTraineeAccompaniments: vi.fn(),
}));

const mutateAsync = vi.fn();

function staff(id: string, name: string, isTrainee = true): StaffRead {
  return { id, name, status: 'active', role: 'staff', is_trainee: isTrainee } as StaffRead;
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
    sameAddressKey: null,
    ...over,
  };
}

function setup(
  params: Partial<UseAccompanimentControllerParams> = {},
  weekVisits: AccompanimentWeekVisit[] = [],
) {
  return renderHook(() =>
    useAccompanimentController({
      isoYear: 2026,
      isoWeek: 29,
      canEdit: true,
      staffOptions: [staff('s1', '髙梨')],
      weekVisits,
      resolveCourseId: (t, wd) => `${t}:${wd}`,
      weekdayDateLabel: (wd) => `d${wd}`,
      ...params,
    }),
  );
}

beforeEach(() => {
  mutateAsync.mockReset();
  mutateAsync.mockResolvedValue([]);
  vi.mocked(queries.useTraineeAccompaniments).mockReturnValue({
    data: [],
    isLoading: false,
  } as unknown as ReturnType<typeof queries.useTraineeAccompaniments>);
  vi.mocked(queries.useUpdateTraineeAccompaniments).mockReturnValue({
    mutateAsync,
    isPending: false,
  } as unknown as ReturnType<typeof queries.useUpdateTraineeAccompaniments>);
});

describe('useAccompanimentController', () => {
  it('canEdit + スタッフ1人以上で available、enter で 1人自動選択', () => {
    const { result } = setup();
    expect(result.current.available).toBe(true);
    expect(result.current.active).toBe(false);
    act(() => result.current.enter());
    expect(result.current.active).toBe(true);
    expect(result.current.bar?.selectedStaffId).toBe('s1');
    // 既定は「コース(曜日)単位」。
    expect(result.current.bar?.targetMode).toBe('course');
    expect(result.current.binding.isCourseArmed).toBe(true);
    expect(result.current.binding.isVisitArmed).toBe(false);
  });

  it('新人でない一般スタッフも同行者に選べる (一般化)', () => {
    const { result } = setup({ staffOptions: [staff('g1', '熊澤', false)] });
    expect(result.current.available).toBe(true);
    act(() => result.current.enter());
    expect(result.current.bar?.selectedStaffId).toBe('g1');
    expect(result.current.bar?.staffOptions[0]?.is_trainee).toBe(false);
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

  it('対象の二択: コース単位では患者カード、患者単位ではコースヘッダを受け付けない', () => {
    const weekVisits = [
      visit({ visitId: 'v1', courseId: 'c1' }),
      visit({ visitId: 'v3', courseId: 'c2', weekday: 1, startMin: 780, endMin: 840 }),
    ];
    const { result } = setup({}, weekVisits);
    act(() => result.current.enter());
    // コース単位中は個別トグルが効かない。
    act(() => result.current.binding.toggleVisit('v3'));
    expect(result.current.bar?.visitCount).toBe(0);
    // 患者単位に切り替えるとコースヘッダが効かなくなり、個別は効く。
    act(() => result.current.bar?.onChangeTargetMode('visit'));
    expect(result.current.binding.isCourseArmed).toBe(false);
    expect(result.current.binding.isVisitArmed).toBe(true);
    act(() => result.current.binding.toggleCourse('c1', 't1', 0));
    expect(result.current.bar?.courseCount).toBe(0);
    act(() => result.current.binding.toggleVisit('v3'));
    expect(result.current.bar?.visitCount).toBe(1);
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

  it('確定で PUT ペイロードを組む (既定チェック時は defaults も送る)', async () => {
    const weekVisits = [
      visit({ visitId: 'v1', courseId: 'c1' }),
      visit({ visitId: 'v3', courseId: 'c2', weekday: 1, startMin: 780, endMin: 840 }),
    ];
    const { result } = setup({}, weekVisits);
    act(() => result.current.enter());
    act(() => result.current.binding.toggleCourse('c1', 't1', 0));
    act(() => result.current.bar?.onChangeTargetMode('visit'));
    act(() => result.current.binding.toggleVisit('v3'));
    act(() => result.current.bar?.onToggleSetDefault(true));
    act(() => result.current.bar?.onConfirm());
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    const payload = mutateAsync.mock.calls[0]![0];
    expect(payload.trainee_staff_id).toBe('s1');
    expect(payload.course_ids).toEqual(['c1']);
    expect(payload.visit_ids).toEqual(['v3']);
    expect(payload.defaults).toEqual([{ weekday: 0, course_template_id: 't1' }]);
    // 初回は acknowledge を送らない。
    expect(payload.acknowledge_constraint_warnings).toBeUndefined();
  });

  it('既定チェックなしでは defaults=null (既定に触れない)', async () => {
    const weekVisits = [visit({ visitId: 'v1', courseId: 'c1' })];
    const { result } = setup({}, weekVisits);
    act(() => result.current.enter());
    act(() => result.current.binding.toggleCourse('c1', 't1', 0));
    act(() => result.current.bar?.onConfirm());
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync.mock.calls[0]![0].defaults).toBeNull();
  });

  it('選択済みコース内の訪問は個別トグル不可', () => {
    const weekVisits = [visit({ visitId: 'v1', courseId: 'c1' })];
    const { result } = setup({}, weekVisits);
    act(() => result.current.enter());
    act(() => result.current.binding.toggleCourse('c1', 't1', 0));
    act(() => result.current.bar?.onChangeTargetMode('visit'));
    act(() => result.current.binding.toggleVisit('v1')); // no-op
    expect(result.current.bar?.visitCount).toBe(0);
  });

  it('重複 422 は「なぜ登録できないか」を理由つきで警告に出す (確定#1)', async () => {
    mutateAsync.mockRejectedValueOnce(
      new ApiError('unprocessable', 422, {
        detail: {
          code: 'accompaniment_overlap',
          conflicts: [
            {
              date: '2026-08-18',
              weekday: 1,
              start: '10:00',
              end: '10:35',
              patient_name: '山田 太郎',
              course_label: '稲毛A',
              reason: 'own_duty',
            },
          ],
        },
      }),
    );
    const weekVisits = [visit({ visitId: 'v1', courseId: 'c1' })];
    const { result } = setup({}, weekVisits);
    act(() => result.current.enter());
    act(() => result.current.binding.toggleCourse('c1', 't1', 0));
    act(() => result.current.bar?.onConfirm());
    await waitFor(() =>
      expect(result.current.bar?.serverOverlapMessages.length).toBeGreaterThan(0),
    );
    const msg = result.current.bar!.serverOverlapMessages[0]!;
    expect(msg).toContain('8月18日(火)');
    expect(msg).toContain('10:00〜10:35');
    expect(msg).toContain('山田 太郎様（稲毛A・ご自身の担当）');
    expect(msg).toContain('重なるため登録できません');
    // 理由が消えるまで確定はブロック。
    expect(result.current.bar?.canConfirm).toBe(false);
    // モードは開いたまま (やり直せる)。
    expect(result.current.active).toBe(true);
  });

  it('NG/性別 422 は確認ダイアログ → OK で acknowledge つき再送 (確定#4)', async () => {
    mutateAsync.mockRejectedValueOnce(
      new ApiError('unprocessable', 422, {
        detail: {
          code: 'constraint_confirmation_required',
          warnings: [
            {
              kind: 'ng_staff',
              patient_id: 'p1',
              patient_name: '山田 太郎',
              staff_id: 's1',
              staff_name: '髙梨',
            },
          ],
        },
      }),
    );
    const weekVisits = [visit({ visitId: 'v1', courseId: 'c1' })];
    const { result } = setup({}, weekVisits);
    act(() => result.current.enter());
    act(() => result.current.binding.toggleCourse('c1', 't1', 0));
    act(() => result.current.bar?.onConfirm());
    await waitFor(() => expect(result.current.constraintDialogProps.open).toBe(true));
    expect(result.current.constraintDialogProps.warnings).toHaveLength(1);
    expect(result.current.constraintDialogProps.title).toContain('同行として登録');

    act(() => result.current.constraintDialogProps.onConfirm());
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(2));
    expect(mutateAsync.mock.calls[1]![0].acknowledge_constraint_warnings).toBe(true);
    await waitFor(() => expect(result.current.active).toBe(false));
  });

  it('常時表示バッジは 1 訪問/1コースの複数名を配列で返す (確定#5)', () => {
    vi.mocked(queries.useTraineeAccompaniments).mockReturnValue({
      data: [
        {
          id: 'l1',
          trainee_staff_id: 's1',
          staff_name: '髙梨',
          kind: 'trainee',
          target_type: 'visit',
          source: 'manual',
          visit: { id: 'v1', date: '2026-07-14' },
        },
        {
          id: 'l2',
          trainee_staff_id: 'g1',
          trainee_staff_name: '熊澤',
          kind: 'support',
          target_type: 'visit',
          source: 'manual',
          visit: { id: 'v1', date: '2026-07-14' },
        },
        {
          id: 'l3',
          trainee_staff_id: 'g1',
          staff_name: '青柳',
          kind: 'support',
          target_type: 'course',
          source: 'manual',
          course: { id: 'c1', weekday: 0, code: 'A', template_id: 't1' },
        },
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof queries.useTraineeAccompaniments>);
    const { result } = setup();
    expect(result.current.binding.visitBadgeName('v1')).toEqual(['熊澤', '髙梨']);
    expect(result.current.binding.courseBadgeName('c1')).toEqual(['青柳']);
    expect(result.current.binding.visitBadgeName('v9')).toEqual([]);
  });
});
