/**
 * SubstitutePanel — 「🛌 休みにする」の確認モーダル (PO 決定 2026-08-23)。
 *
 * ① 見出しと「予定 N件（コース …）は担当なしに戻します」の要約が出る
 * ② [担当なしに戻す] は onApply(null)
 * ③ その日を**丸ごと**引き受けられる人 (全グループ ◎) だけを最大 3 名出す
 * ④ 1 グループでも ◎ でない人は出さない (1 人ずつの提案はしない)
 * ⑤ 青ピンがあれば理由を出して実行不可
 * ⑥ [やめる] は onClose
 * ⑦ planned 以外 (打刻済み・完了・取消済み) は件数にも青ピン判定にも数えない
 *    = BE (staff-off-week) が動かす対象集合と一致させる
 * ⑧ 取得中 / 失敗中は主ボタンを押させない (「0 件」と混同させない・再試行できる)
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

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
const COURSE_2 = '00000000-0000-4000-8000-000000000c22';
const VISIT_1 = '00000000-0000-4000-8000-000000000a11';
const VISIT_2 = '00000000-0000-4000-8000-000000000a12';
const VISIT_3 = '00000000-0000-4000-8000-000000000a13';

const refetchMock = vi.fn();

function visit(id: string, name: string, pinned = false, status = 'planned') {
  return {
    visit_id: id,
    patient_id: `${id}-p`,
    patient_name: name,
    start_time: '09:00',
    end_time: '10:00',
    week_pinned: pinned,
    status,
  };
}

function cand(staffId: string, name: string, status: 'ok' | 'warn' | 'ng', score = 1) {
  return {
    staff_id: staffId,
    name,
    sex: null,
    office_name: '稲毛',
    status,
    reasons: status === 'ok' ? [] : [{ code: 'time_overlap', message: '重なる', visit_id: null }],
    score,
    load_today: 1,
  };
}

const DATA = {
  absent_staff: { id: STAFF_OFF, name: '川名' },
  date: '2026-08-20',
  weekday: 3,
  groups: [
    {
      course_id: COURSE_1,
      course_label: '稲毛A',
      visits: [visit(VISIT_1, '佐々木 様'), visit(VISIT_2, '中村 様')],
      candidates: [
        cand(CAND_OK, '髙梨', 'ok', 12.5),
        cand(CAND_WARN, '熊澤', 'warn'),
        cand(CAND_NG, '鈴木', 'ng'),
      ],
    },
  ],
  warnings: [],
};

/** useSubstituteCandidates の戻り (react-query の形) を作る。 */
function mockQuery(over: Record<string, unknown> = {}) {
  useSubstituteCandidatesMock.mockReturnValue({
    data: DATA,
    isPending: false,
    fetchStatus: 'idle',
    isError: false,
    error: null,
    refetch: refetchMock,
    ...over,
  });
}

function renderPanel(over: Partial<React.ComponentProps<typeof SubstitutePanel>> = {}) {
  const handlers = { onClose: vi.fn(), onApply: vi.fn() };
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
  mockQuery();
});

describe('SubstitutePanel', () => {
  it('見出しと「予定 N件（コース …）は担当なしに戻します」が出る', () => {
    renderPanel();
    expect(screen.getByTestId('substitute-panel')).toHaveTextContent(
      '川名さんを 8/20(木) 休みにします',
    );
    const summary = screen.getByTestId('substitute-summary');
    expect(summary).toHaveTextContent('予定 2件');
    expect(summary).toHaveTextContent('稲毛A');
    expect(summary).toHaveTextContent('担当なしに戻します');
  });

  it('[担当なしに戻す] で onApply(null)', () => {
    const h = renderPanel();
    fireEvent.click(screen.getByTestId('substitute-unassign'));
    expect(h.onApply).toHaveBeenCalledWith(null);
  });

  it('コースを丸ごと引き受けられる人だけを出す (◎ 以外は出さない)', () => {
    renderPanel();
    expect(screen.getByTestId(`substitute-cand-${CAND_OK}`)).toHaveTextContent(
      '髙梨さんに割り当てる',
    );
    expect(screen.getByTestId(`substitute-cand-${CAND_OK}`)).toHaveTextContent('空き');
    expect(screen.queryByTestId(`substitute-cand-${CAND_WARN}`)).toBeNull();
    expect(screen.queryByTestId(`substitute-cand-${CAND_NG}`)).toBeNull();
  });

  it('候補ボタンは onApply(staffId)', () => {
    const h = renderPanel();
    fireEvent.click(screen.getByTestId(`substitute-cand-${CAND_OK}`));
    expect(h.onApply).toHaveBeenCalledWith(CAND_OK);
  });

  it('1 グループでも ◎ でない人は「丸ごと」に数えない', () => {
    mockQuery({
      data: {
        ...DATA,
        groups: [
          DATA.groups[0],
          {
            course_id: COURSE_2,
            course_label: '都賀B',
            visits: [visit(VISIT_3, '高橋 様')],
            // 髙梨はこちらでは △ → 丸ごとは引き受けられない
            candidates: [cand(CAND_OK, '髙梨', 'warn')],
          },
        ],
      },
    });
    renderPanel();
    expect(screen.queryByTestId('substitute-whole-day')).toBeNull();
    expect(screen.getByTestId('substitute-summary')).toHaveTextContent('予定 3件');
  });

  it('planned 以外 (完了・取消済み) は件数にも青ピン判定にも数えない', () => {
    mockQuery({
      data: {
        ...DATA,
        groups: [
          {
            ...DATA.groups[0],
            visits: [
              visit(VISIT_1, '佐々木 様'),
              // 完了済み + 青ピン。BE は据え置くので、ここで実行不可にしてはいけない。
              visit(VISIT_2, '中村 様', true, 'completed'),
            ],
          },
        ],
      },
    });
    renderPanel();
    expect(screen.getByTestId('substitute-summary')).toHaveTextContent('予定 1件');
    expect(screen.queryByTestId('substitute-pinned')).toBeNull();
    expect(screen.getByTestId('substitute-unassign')).toBeEnabled();
  });

  it('青ピンがあると理由を出して実行できない', () => {
    mockQuery({
      data: {
        ...DATA,
        groups: [
          {
            ...DATA.groups[0],
            visits: [visit(VISIT_1, '佐々木 様', true), visit(VISIT_2, '中村 様')],
          },
        ],
      },
    });
    const h = renderPanel();
    expect(screen.getByTestId('substitute-pinned')).toHaveTextContent('佐々木 様');
    expect(screen.getByTestId('substitute-unassign')).toBeDisabled();
    expect(screen.getByTestId(`substitute-cand-${CAND_OK}`)).toBeDisabled();
    fireEvent.click(screen.getByTestId('substitute-unassign'));
    expect(h.onApply).not.toHaveBeenCalled();
  });

  it('[やめる] は onClose', () => {
    const h = renderPanel();
    fireEvent.click(screen.getByTestId('substitute-cancel'));
    expect(h.onClose).toHaveBeenCalledTimes(1);
  });

  it('予定が無い日は「休みにする」だけを出す', () => {
    mockQuery({ data: { ...DATA, groups: [] } });
    const h = renderPanel();
    expect(screen.getByTestId('substitute-summary')).toHaveTextContent('渡す予定がありません');
    expect(screen.getByTestId('substitute-unassign')).toHaveTextContent('休みにする');
    fireEvent.click(screen.getByTestId('substitute-unassign'));
    expect(h.onApply).toHaveBeenCalledWith(null);
  });

  it('canEdit=false のときはローディング文言を出さず実行もできない', () => {
    // enabled=false のとき react-query は status='pending' / fetchStatus='idle'
    mockQuery({ data: undefined, isPending: true, fetchStatus: 'idle' });
    renderPanel({ canEdit: false });
    expect(screen.queryByTestId('substitute-loading')).toBeNull();
    expect(screen.getByTestId('substitute-unassign')).toBeDisabled();
  });

  it('取得中はローディング文言 + 主ボタン disabled (0件と混同させない)', () => {
    mockQuery({ data: undefined, isPending: true, fetchStatus: 'fetching' });
    const h = renderPanel();
    expect(screen.getByTestId('substitute-loading')).toBeInTheDocument();
    expect(screen.getByTestId('substitute-summary')).toHaveTextContent('確認しています');
    expect(screen.getByTestId('substitute-unassign')).toBeDisabled();
    fireEvent.click(screen.getByTestId('substitute-unassign'));
    expect(h.onApply).not.toHaveBeenCalled();
  });

  it('取得に失敗したら実行不可 + 再試行ボタン', () => {
    mockQuery({
      data: undefined,
      isPending: false,
      fetchStatus: 'idle',
      isError: true,
      error: new Error('boom'),
    });
    renderPanel();
    expect(screen.getByTestId('substitute-error')).toHaveTextContent('boom');
    expect(screen.getByTestId('substitute-unassign')).toBeDisabled();
    fireEvent.click(screen.getByTestId('substitute-retry'));
    expect(refetchMock).toHaveBeenCalledTimes(1);
  });

  it('submitting 中は二度押しできない', () => {
    renderPanel({ submitting: true });
    expect(screen.getByTestId('substitute-unassign')).toBeDisabled();
    expect(screen.getByTestId(`substitute-cand-${CAND_OK}`)).toBeDisabled();
  });
});
