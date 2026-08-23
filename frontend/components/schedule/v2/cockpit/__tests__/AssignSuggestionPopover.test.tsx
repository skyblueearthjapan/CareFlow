/**
 * AssignSuggestionPopover — 「担当なし」コースを誰に入れるかの提案 (Phase 2-B)。
 *
 * ① 見出し (コース名・日付・件数・時間帯) がモックどおり出る
 * ② ◎ (whole_ok) は理由 + [このコースを割り当てる] / △ は理由のみ (1件ずつなら可)
 * ③ × は既定で折りたたみ・押すと開く
 * ④ ◎ が 0 件なら「丸ごと引き受けられる人はいません」の注記を出す
 * ⑤ 「1件ずつ分けて入れる」/ 閉じる が親へ飛ぶ・候補 hover が親へ流れる (2-D)
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

const mockUseAssignCandidates = vi.fn();
vi.mock('@/lib/queries/cockpit', () => ({
  useAssignCandidates: (...args: unknown[]) => mockUseAssignCandidates(...args),
}));

import { AssignSuggestionPopover, summarizeAssignCandidates } from '../AssignSuggestionPopover';
import type { AssignCandidatesRead } from '@/lib/schemas/v2/cockpit';

const COURSE_ID = '00000000-0000-4000-8000-0000000000c1';
const VISIT_1 = '00000000-0000-4000-8000-0000000000v1';
const VISIT_2 = '00000000-0000-4000-8000-0000000000v2';
const TAKANASHI = '00000000-0000-4000-8000-000000000001';
const KUMAZAWA = '00000000-0000-4000-8000-000000000002';
const UDAGAWA = '00000000-0000-4000-8000-000000000003';
const KAWANA = '00000000-0000-4000-8000-000000000004';

function candidate(
  staffId: string,
  name: string,
  status: 'ok' | 'warn' | 'ng',
  reasons: string[] = [],
  score = 1,
) {
  return {
    staff_id: staffId,
    name,
    sex: 'female',
    office_name: '稲毛',
    status,
    reasons: reasons.map((message) => ({ code: 'time_overlap', message })),
    score,
    load_today: 2,
  };
}

/** 1 グループ (= コース 1 本) の既定レスポンス。 */
function data(over: Partial<AssignCandidatesRead> = {}): AssignCandidatesRead {
  return {
    absent_staff: null,
    date: '2026-08-24',
    weekday: 0,
    groups: [
      {
        course_id: COURSE_ID,
        course_label: '稲毛C',
        visits: [],
        candidates: [
          candidate(TAKANASHI, '髙梨 桂子', 'ok', [], 3),
          candidate(KUMAZAWA, '熊澤 妙子', 'ok', [], 1),
          candidate(UDAGAWA, '宇田川 優莉', 'warn', ['13:30 が重なる']),
          candidate(KAWANA, '川名 千恵', 'ng', ['この日は休みです']),
        ],
      },
    ],
    warnings: [],
    whole_ok_staff_ids: [TAKANASHI, KUMAZAWA],
    whole_ok_by_course: { [COURSE_ID]: [TAKANASHI, KUMAZAWA] },
    ...over,
  } as AssignCandidatesRead;
}

function renderPopover(over: Partial<React.ComponentProps<typeof AssignSuggestionPopover>> = {}) {
  const handlers = {
    onAssignCourse: vi.fn(),
    onSplit: vi.fn(),
    onClose: vi.fn(),
    onHoverCandidate: vi.fn(),
  };
  render(
    <AssignSuggestionPopover
      date="2026-08-24"
      visitIds={[VISIT_1, VISIT_2]}
      courseLabel="稲毛C"
      visits={{ count: 6, startTime: '09:30', endTime: '17:35' }}
      {...handlers}
      {...over}
    />,
  );
  return handlers;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUseAssignCandidates.mockReturnValue({
    data: data(),
    isPending: false,
    isError: false,
    error: null,
    fetchStatus: 'idle',
    refetch: vi.fn(),
  });
});

describe('summarizeAssignCandidates', () => {
  it('◎ は whole_ok_staff_ids が正・1 グループでも ng があれば ×・並びは ◎→△→×', () => {
    const rows = summarizeAssignCandidates(data());
    expect(rows.map((r) => [r.name, r.status])).toEqual([
      ['髙梨 桂子', 'ok'], // score 3 が先
      ['熊澤 妙子', 'ok'],
      ['宇田川 優莉', 'warn'],
      ['川名 千恵', 'ng'],
    ]);
  });

  it('whole_ok に入っていない ok は △ (1件ずつなら可) に落ちる', () => {
    const rows = summarizeAssignCandidates(data({ whole_ok_staff_ids: [TAKANASHI] }));
    expect(rows.find((r) => r.staffId === KUMAZAWA)?.status).toBe('warn');
  });

  it('複数グループ: 理由は重複を除いて合算し、score は合計する', () => {
    const base = data();
    const rows = summarizeAssignCandidates({
      ...base,
      groups: [base.groups[0]!, base.groups[0]!],
      whole_ok_staff_ids: [],
    });
    const udagawa = rows.find((r) => r.staffId === UDAGAWA);
    expect(udagawa?.reasons).toEqual(['13:30 が重なる']);
    expect(rows.find((r) => r.staffId === TAKANASHI)?.score).toBe(6);
  });
});

describe('AssignSuggestionPopover', () => {
  it('① 見出しに コース名・日付・件数・時間帯 を出す', () => {
    renderPopover();
    expect(screen.getByTestId('assign-suggestion-popover')).toHaveTextContent('稲毛C を誰に？');
    expect(screen.getByTestId('assign-suggestion-sub')).toHaveTextContent(
      '8/24(月)・6件・09:30〜17:35',
    );
  });

  it('② ◎ は理由 + [このコースを割り当てる]・押すと親へ staffId が飛ぶ', () => {
    const h = renderPopover();
    const ok = screen.getByTestId(`assign-suggestion-ok-${TAKANASHI}`);
    expect(ok).toHaveTextContent('髙梨 桂子');
    expect(ok).toHaveTextContent('空き・稲毛');
    fireEvent.click(screen.getByTestId(`assign-suggestion-apply-${TAKANASHI}`));
    expect(h.onAssignCourse).toHaveBeenCalledWith(TAKANASHI);
  });

  it('② △ は理由だけ・割当ボタンを出さない (「1件ずつなら可」)', () => {
    renderPopover();
    const warn = screen.getByTestId(`assign-suggestion-warn-${UDAGAWA}`);
    expect(warn).toHaveTextContent('13:30 が重なる');
    expect(warn).toHaveTextContent('1件ずつなら可');
    expect(screen.queryByTestId(`assign-suggestion-apply-${UDAGAWA}`)).not.toBeInTheDocument();
  });

  it('③ × は既定で折りたたみ・トグルで開く', () => {
    renderPopover();
    expect(screen.queryByTestId('assign-suggestion-ng-list')).not.toBeInTheDocument();
    const toggle = screen.getByTestId('assign-suggestion-ng-toggle');
    expect(toggle).toHaveTextContent('× 休み・NG・資格不可 1名を表示');
    fireEvent.click(toggle);
    expect(screen.getByTestId(`assign-suggestion-ng-${KAWANA}`)).toHaveTextContent(
      'この日は休みです',
    );
  });

  it('④ ◎ が 0 件なら「丸ごと引き受けられる人はいません」と出す', () => {
    mockUseAssignCandidates.mockReturnValue({
      data: data({ whole_ok_staff_ids: [] }),
      isPending: false,
      isError: false,
      error: null,
      fetchStatus: 'idle',
      refetch: vi.fn(),
    });
    renderPopover();
    expect(screen.getByTestId('assign-suggestion-no-whole')).toHaveTextContent(
      'コースを丸ごと引き受けられる人はいません',
    );
    expect(screen.queryByTestId(`assign-suggestion-apply-${TAKANASHI}`)).not.toBeInTheDocument();
  });

  it('④c 理由なしの △ しか居ない = コース内の時間が重なっている、と言い分ける (L3)', () => {
    const base = data();
    mockUseAssignCandidates.mockReturnValue({
      data: {
        ...base,
        groups: [
          {
            ...base.groups[0]!,
            // 1 人ずつなら全員 ok。丸ごとは誰も無理 (訪問同士が重なっている)。
            candidates: [
              candidate(TAKANASHI, '髙梨 桂子', 'ok', [], 3),
              candidate(KUMAZAWA, '熊澤 妙子', 'ok', [], 1),
            ],
          },
        ],
        whole_ok_staff_ids: [],
      },
      isPending: false,
      isError: false,
      error: null,
      fetchStatus: 'idle',
      refetch: vi.fn(),
    });
    renderPopover();
    expect(screen.getByTestId('assign-suggestion-no-whole')).toHaveTextContent(
      'コース内で時間が重なるため1人では回れません',
    );
  });

  it('④d 理由つきの △ が混ざるときは従来の文言のまま', () => {
    mockUseAssignCandidates.mockReturnValue({
      data: data({ whole_ok_staff_ids: [] }),
      isPending: false,
      isError: false,
      error: null,
      fetchStatus: 'idle',
      refetch: vi.fn(),
    });
    renderPopover();
    expect(screen.getByTestId('assign-suggestion-no-whole')).toHaveTextContent(
      'コースを丸ごと引き受けられる人はいません',
    );
  });

  it('④b 取得中は「0名」ではなく「確認しています…」と言う', () => {
    mockUseAssignCandidates.mockReturnValue({
      data: undefined,
      isPending: true,
      isError: false,
      error: null,
      fetchStatus: 'fetching',
      refetch: vi.fn(),
    });
    renderPopover();
    expect(screen.getByTestId('assign-suggestion-loading')).toBeInTheDocument();
    expect(screen.queryByTestId('assign-suggestion-no-whole')).not.toBeInTheDocument();
  });

  it('⑤ 「1件ずつ分けて入れる」/ 閉じる / hover が親へ飛ぶ', () => {
    const h = renderPopover();
    fireEvent.click(screen.getByTestId('assign-suggestion-split'));
    expect(h.onSplit).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId('assign-suggestion-close'));
    expect(h.onClose).toHaveBeenCalledTimes(1);
    // 2-D: 候補行 hover → タイムライン/リストのハイライト。
    fireEvent.mouseEnter(screen.getByTestId(`assign-suggestion-ok-${KUMAZAWA}`));
    expect(h.onHoverCandidate).toHaveBeenLastCalledWith(KUMAZAWA);
  });

  it('⑥ 実行中 (submitting) は割当ボタンを押させない', () => {
    renderPopover({ submitting: true });
    expect(screen.getByTestId(`assign-suggestion-apply-${TAKANASHI}`)).toBeDisabled();
  });

  it('⑦ M1: course_id ではなく「担当なし行の訪問」を visit_ids で問い合わせる', () => {
    renderPopover();
    const params = mockUseAssignCandidates.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(params).toEqual({ date: '2026-08-24', visit_ids: [VISIT_1, VISIT_2] });
    expect(params.course_id).toBeUndefined();
  });

  it('⑧ L2: 閉じるとアンカー (バッジ) へフォーカスが戻る', () => {
    const anchor = document.createElement('button');
    document.body.appendChild(anchor);
    const { unmount } = render(
      <AssignSuggestionPopover
        date="2026-08-24"
        visitIds={[VISIT_1]}
        courseLabel="稲毛C"
        visits={{ count: 1, startTime: '09:30', endTime: '10:05' }}
        anchorEl={anchor}
        onAssignCourse={vi.fn()}
        onSplit={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(document.activeElement).not.toBe(anchor);
    unmount();
    expect(document.activeElement).toBe(anchor);
    anchor.remove();
  });
});
