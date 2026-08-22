/**
 * SubstitutePanel — 急な休みの代替候補パネル (週空間 Phase E)。
 *
 * ① group ごとに候補が ◎/△/× + 理由 + score で並ぶ
 * ② 候補クリックで onApply({toStaffId, groups:[{courseId, visitIds}]}) が飛ぶ
 * ③ 「休みにする」既定 ON → 付け替え時に onMarkOff も 1 回だけ呼ばれる
 * ④ チェックを外すと onMarkOff は呼ばれない
 * ⑤ 「（担当なし）へ戻す」は toStaffId=null
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const useSubstituteCandidatesMock = vi.fn();
vi.mock('@/lib/queries/cockpit', () => ({
  useSubstituteCandidates: (...args: unknown[]) => useSubstituteCandidatesMock(...args),
}));

import { SubstitutePanel } from '../SubstitutePanel';

const STAFF_OFF = '00000000-0000-4000-8000-0000000000ff';
const CAND_OK = '00000000-0000-4000-8000-00000000c001';
const CAND_WARN = '00000000-0000-4000-8000-00000000c002';
const CAND_NG = '00000000-0000-4000-8000-00000000c003';
const COURSE_1 = '00000000-0000-4000-8000-000000000c11';
const VISIT_1 = '00000000-0000-4000-8000-000000000a11';
const VISIT_2 = '00000000-0000-4000-8000-000000000a12';

const DATA = {
  absent_staff: { id: STAFF_OFF, name: '川名' },
  date: '2026-08-20',
  weekday: 3,
  groups: [
    {
      course_id: COURSE_1,
      course_label: '稲毛A',
      visits: [
        {
          visit_id: VISIT_1,
          patient_id: '00000000-0000-4000-8000-000000000b11',
          patient_name: '佐々木 様',
          start_time: '09:00',
          end_time: '10:00',
          week_pinned: false,
        },
        {
          visit_id: VISIT_2,
          patient_id: '00000000-0000-4000-8000-000000000b12',
          patient_name: '中村 様',
          start_time: '10:30',
          end_time: '11:15',
          week_pinned: false,
        },
      ],
      candidates: [
        {
          staff_id: CAND_OK,
          name: '髙梨',
          sex: 'female',
          office_name: '稲毛',
          status: 'ok',
          reasons: [],
          score: 12.5,
          load_today: 2,
        },
        {
          staff_id: CAND_WARN,
          name: '熊澤',
          sex: null,
          office_name: '都賀',
          status: 'warn',
          reasons: [{ code: 'time_overlap', message: '09:00 が重なる', visit_id: VISIT_1 }],
          score: 3,
          load_today: 4,
        },
        {
          staff_id: CAND_NG,
          name: '鈴木',
          sex: 'male',
          office_name: '幕張',
          status: 'ng',
          reasons: [{ code: 'off', message: 'この日は休み', visit_id: null }],
          score: -5,
          load_today: 0,
        },
      ],
    },
  ],
  warnings: [],
};

function renderPanel(over: Partial<React.ComponentProps<typeof SubstitutePanel>> = {}) {
  const handlers = { onClose: vi.fn(), onApply: vi.fn(), onMarkOff: vi.fn() };
  render(
    <SubstitutePanel
      staff={{ id: STAFF_OFF, name: '川名' }}
      date="2026-08-20"
      {...handlers}
      {...over}
    />,
  );
  return handlers;
}

beforeEach(() => {
  vi.clearAllMocks();
  useSubstituteCandidatesMock.mockReturnValue({
    data: DATA,
    isPending: false,
    fetchStatus: 'idle',
    isError: false,
    error: null,
  });
});

describe('SubstitutePanel', () => {
  it('候補が ◎ / △ / × と理由・score つきで並ぶ', () => {
    renderPanel();
    const ok = screen.getByTestId(`substitute-cand-${COURSE_1}-${CAND_OK}`);
    const warn = screen.getByTestId(`substitute-cand-${COURSE_1}-${CAND_WARN}`);
    const ng = screen.getByTestId(`substitute-cand-${COURSE_1}-${CAND_NG}`);
    expect(ok).toHaveTextContent('◎');
    expect(ok).toHaveTextContent('髙梨');
    expect(ok).toHaveTextContent('空き');
    expect(ok).toHaveTextContent('相性 12.5');
    expect(warn).toHaveTextContent('△');
    expect(warn).toHaveTextContent('09:00 が重なる');
    expect(ng).toHaveTextContent('×');
    expect(ng).toHaveTextContent('この日は休み');
    // group 見出し (コース名 + 件数)
    expect(screen.getByTestId(`substitute-group-${COURSE_1}`)).toHaveTextContent('稲毛A');
  });

  it('候補クリックで group 単位の付け替え payload が飛ぶ', async () => {
    const h = renderPanel();
    fireEvent.click(screen.getByTestId(`substitute-cand-${COURSE_1}-${CAND_OK}`));
    await waitFor(() => expect(h.onApply).toHaveBeenCalled());
    expect(h.onApply).toHaveBeenCalledWith({
      toStaffId: CAND_OK,
      groups: [{ courseId: COURSE_1, visitIds: [VISIT_1, VISIT_2] }],
    });
    // 「休みにする」既定 ON → 休み登録も 1 回
    expect(h.onMarkOff).toHaveBeenCalledTimes(1);
  });

  it('「休みにする」を外すと onMarkOff は呼ばれない', async () => {
    const h = renderPanel();
    fireEvent.click(screen.getByTestId('substitute-mark-off'));
    fireEvent.click(screen.getByTestId(`substitute-cand-${COURSE_1}-${CAND_OK}`));
    await waitFor(() => expect(h.onApply).toHaveBeenCalled());
    expect(h.onMarkOff).not.toHaveBeenCalled();
  });

  it('（担当なし）へ戻すは toStaffId=null', async () => {
    const h = renderPanel();
    fireEvent.click(screen.getByTestId(`substitute-unassign-${COURSE_1}`));
    await waitFor(() => expect(h.onApply).toHaveBeenCalled());
    expect(h.onApply).toHaveBeenCalledWith({
      toStaffId: null,
      groups: [{ courseId: COURSE_1, visitIds: [VISIT_1, VISIT_2] }],
    });
  });

  it('onMarkOff が終わってから onApply を呼ぶ (休み登録 → 付け替えの順)', async () => {
    // 遅延する onMarkOff を渡し、「解決前に onApply が走らない」ことを見る。
    const order: string[] = [];
    let releaseMarkOff: (() => void) | null = null;
    const onMarkOff = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          order.push('markOff:start');
          releaseMarkOff = () => {
            order.push('markOff:done');
            resolve();
          };
        }),
    );
    const onApply = vi.fn(() => {
      order.push('apply');
    });
    renderPanel({ onMarkOff, onApply });

    fireEvent.click(screen.getByTestId(`substitute-cand-${COURSE_1}-${CAND_OK}`));
    await waitFor(() => expect(onMarkOff).toHaveBeenCalled());
    // 休みの登録が終わるまで付け替えは走らない。
    expect(onApply).not.toHaveBeenCalled();

    releaseMarkOff!();
    await waitFor(() => expect(onApply).toHaveBeenCalled());
    expect(order).toEqual(['markOff:start', 'markOff:done', 'apply']);
  });

  it('× 候補は押せず、理由が title に出る', () => {
    const h = renderPanel();
    const ng = screen.getByTestId(`substitute-cand-${COURSE_1}-${CAND_NG}`);
    expect(ng).toBeDisabled();
    expect(ng).toHaveAttribute('title', expect.stringContaining('この日は休み'));
    fireEvent.click(ng);
    expect(h.onApply).not.toHaveBeenCalled();
    // △ (時間の重なりだけ) は押せる — 隠さず警告して選ばせる
    expect(screen.getByTestId(`substitute-cand-${COURSE_1}-${CAND_WARN}`)).toBeEnabled();
  });

  it('canEdit=false のときはローディング文言を出さない', () => {
    // enabled=false のとき react-query は status='pending' / fetchStatus='idle'
    useSubstituteCandidatesMock.mockReturnValue({
      data: undefined,
      isPending: true,
      fetchStatus: 'idle',
      isError: false,
      error: null,
    });
    renderPanel({ canEdit: false });
    expect(screen.queryByTestId('substitute-loading')).toBeNull();
  });

  it('取得中はローディング文言を出す', () => {
    useSubstituteCandidatesMock.mockReturnValue({
      data: undefined,
      isPending: true,
      fetchStatus: 'fetching',
      isError: false,
      error: null,
    });
    renderPanel();
    expect(screen.getByTestId('substitute-loading')).toBeInTheDocument();
  });

  it('予定が無い日は「休みにする」だけを出す', () => {
    useSubstituteCandidatesMock.mockReturnValue({
      data: { ...DATA, groups: [] },
      isPending: false,
      fetchStatus: 'idle',
      isError: false,
      error: null,
    });
    const h = renderPanel();
    expect(screen.getByTestId('substitute-empty')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('substitute-only-off'));
    expect(h.onMarkOff).toHaveBeenCalledTimes(1);
    expect(h.onApply).not.toHaveBeenCalled();
  });
});
