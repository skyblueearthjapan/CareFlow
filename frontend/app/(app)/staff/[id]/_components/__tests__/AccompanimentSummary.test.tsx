/**
 * 同行サマリ (スタッフ詳細) のテスト — general-accompaniment-design.md §4。
 *   - 今週の同行を一覧表示する (新人以外でも)
 *   - 週リンクの個別解除 = 週 PUT の減算で実現する
 */
import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AccompanimentSummary, buildWeekLinkIds } from '../AccompanimentSummary';
import type { TraineeAccompanimentItem } from '@/lib/schemas/trainee_accompaniment';
import { ApiError } from '@/lib/api-client';
import * as queries from '@/lib/queries/trainee_accompaniments';

vi.mock('@/lib/queries/trainee_accompaniments', () => ({
  useTraineeAccompaniments: vi.fn(),
  useTraineeAccompanimentDefaults: vi.fn(),
  useUpdateTraineeAccompaniments: vi.fn(),
}));

const mutateAsync = vi.fn();

const COURSE_LINK = {
  id: 'l-course',
  trainee_staff_id: 'g1',
  staff_name: '熊澤',
  kind: 'support',
  target_type: 'course',
  source: 'manual',
  course: { id: 'c1', weekday: 0, code: 'A', template_id: 't1' },
} as unknown as TraineeAccompanimentItem;

const VISIT_LINK = {
  id: 'l-visit',
  trainee_staff_id: 'g1',
  staff_name: '熊澤',
  kind: 'support',
  target_type: 'visit',
  source: 'manual',
  visit: { id: 'v1', date: '2026-08-18', start: '10:00', patient_name: '山田 太郎' },
} as unknown as TraineeAccompanimentItem;

function mockQueries(items: TraineeAccompanimentItem[]) {
  vi.mocked(queries.useTraineeAccompaniments).mockReturnValue({
    data: items,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof queries.useTraineeAccompaniments>);
  vi.mocked(queries.useTraineeAccompanimentDefaults).mockReturnValue({
    data: [],
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof queries.useTraineeAccompanimentDefaults>);
  vi.mocked(queries.useUpdateTraineeAccompaniments).mockReturnValue({
    mutateAsync,
    isPending: false,
  } as unknown as ReturnType<typeof queries.useUpdateTraineeAccompaniments>);
}

beforeEach(() => {
  mutateAsync.mockReset();
  mutateAsync.mockResolvedValue([]);
  mockQueries([COURSE_LINK, VISIT_LINK]);
});

describe('buildWeekLinkIds', () => {
  it('週リンクを course_ids / visit_ids に畳み、除外 id は落とす', () => {
    expect(buildWeekLinkIds([COURSE_LINK, VISIT_LINK])).toEqual({
      course_ids: ['c1'],
      visit_ids: ['v1'],
      unresolved: [],
    });
    expect(buildWeekLinkIds([COURSE_LINK, VISIT_LINK], 'l-visit')).toEqual({
      course_ids: ['c1'],
      visit_ids: [],
      unresolved: [],
    });
  });

  it('リンク先 id を解決できない行は黙って落とさず unresolved に返す', () => {
    const broken = { ...VISIT_LINK, id: 'l-broken', visit: null } as TraineeAccompanimentItem;
    const res = buildWeekLinkIds([COURSE_LINK, broken]);
    expect(res.visit_ids).toEqual([]);
    expect(res.unresolved.map((i) => i.id)).toEqual(['l-broken']);
  });
});

describe('AccompanimentSummary', () => {
  it('今週の同行をコース/患者個別ともに一覧表示する', () => {
    render(<AccompanimentSummary staffId="g1" />);
    const list = screen.getByTestId('trainee-week-accompaniments');
    expect(list.textContent).toContain('Aコース（丸ごと）');
    expect(list.textContent).toContain('山田 太郎');
  });

  it('canEdit=false では解除ボタンを出さない (閲覧専用)', () => {
    render(<AccompanimentSummary staffId="g1" />);
    expect(screen.queryByTestId('accompaniment-release-l-visit')).toBeNull();
  });

  it('個別解除は当該 1 件を差し引いた週 PUT を送る (既定には触れない)', async () => {
    render(<AccompanimentSummary staffId="g1" canEdit />);
    await act(async () => {
      screen.getByTestId('accompaniment-release-l-visit').click();
    });
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    const payload = mutateAsync.mock.calls[0]![0];
    expect(payload.staff_id).toBe('g1');
    expect(payload.course_ids).toEqual(['c1']);
    expect(payload.visit_ids).toEqual([]);
    expect(payload.defaults).toBeNull();
    expect(payload.acknowledge_constraint_warnings).toBeUndefined();
  });

  it('解除に失敗したらエラーを表示する', async () => {
    mutateAsync.mockRejectedValueOnce(new Error('週が固定されています'));
    render(<AccompanimentSummary staffId="g1" canEdit />);
    await act(async () => {
      screen.getByTestId('accompaniment-release-l-course').click();
    });
    await waitFor(() => expect(screen.getByText('週が固定されています')).toBeInTheDocument());
  });

  it('解除の NG/性別 422 は確認ダイアログ → OK で acknowledge つき再送 (HIGH-1)', async () => {
    mutateAsync.mockRejectedValueOnce(
      new ApiError('unprocessable', 422, {
        detail: {
          code: 'constraint_confirmation_required',
          warnings: [
            {
              kind: 'ng_staff',
              patient_id: 'p1',
              patient_name: '山田 太郎',
              staff_id: 'g1',
              staff_name: '熊澤',
            },
          ],
        },
      }),
    );
    render(<AccompanimentSummary staffId="g1" canEdit />);
    await act(async () => {
      screen.getByTestId('accompaniment-release-l-visit').click();
    });
    // 生の英語 422 ではなく確認ダイアログが出る。
    const dialog = await screen.findByTestId('constraint-override-confirm');
    expect(dialog.textContent).toContain('同行を解除しますか');
    await act(async () => {
      screen.getByTestId('constraint-override-ok').click();
    });
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(2));
    expect(mutateAsync.mock.calls[1]![0].acknowledge_constraint_warnings).toBe(true);
  });

  it('解除の重複 422 は日本語の理由で表示する (生の英語を出さない)', async () => {
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
    render(<AccompanimentSummary staffId="g1" canEdit />);
    await act(async () => {
      screen.getByTestId('accompaniment-release-l-visit').click();
    });
    const alert = await screen.findByTestId('accompaniment-release-error');
    expect(alert.textContent).toContain('8月18日(火)');
    expect(alert.textContent).toContain('山田 太郎様（稲毛A・ご自身の担当）');
    expect(alert.textContent).toContain('重なるため登録できません');
  });

  it('リンク先を解決できない行があれば送信せず中止する (破壊的操作の安全側)', async () => {
    const broken = { ...COURSE_LINK, id: 'l-broken', course: null } as TraineeAccompanimentItem;
    mockQueries([broken, VISIT_LINK]);
    render(<AccompanimentSummary staffId="g1" canEdit />);
    await act(async () => {
      screen.getByTestId('accompaniment-release-l-visit').click();
    });
    expect(mutateAsync).not.toHaveBeenCalled();
    expect((await screen.findByTestId('accompaniment-release-error')).textContent).toContain(
      '安全のため解除を中止しました',
    );
  });
});
