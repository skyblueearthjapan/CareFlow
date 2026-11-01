/**
 * 実現性の印・帯 (設計 docs/plans/schedule-feasibility-alerts-design-2026-11-01.md 第 1 段)。
 *
 * - 印: 赤「！」/ 黄「△」・押すと理由・押してもカードのドラッグ/クリックへ伝わらない
 * - 週の時間軸 (WeekTimelineBoard) のカードに付く (単独・同住所ペア)・Provider の外では出ない
 * - 帯: 件数・一覧・行を押すとカードへ・管理者以外 (enabled=false) は出ない
 * - 取り込み結果の帯 (ImportFeasibilityPanel)
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { WeekOverviewVisit } from '@/components/schedule/v2/CourseWeekOverview';
import type { FeasibilityMarksResponse } from '@/lib/queries/feasibility';

const marksState: { data: FeasibilityMarksResponse | undefined; lastEnabled: boolean | null } = {
  data: undefined,
  lastEnabled: null,
};

vi.mock('@/lib/queries/feasibility', () => ({
  useFeasibilityMarks: (p: { enabled?: boolean }) => {
    marksState.lastEnabled = p.enabled ?? true;
    return { data: p.enabled === false ? undefined : marksState.data };
  },
}));

const toastInfo = vi.fn();
vi.mock('sonner', () => ({ toast: { info: (...a: unknown[]) => toastInfo(...a) } }));

import { ImportFeasibilityPanel } from '@/app/(app)/integrations/kaipoke/_components/ImportFeasibilityPanel';
import { FeasibilityBand } from '@/components/schedule/feasibility/FeasibilityBand';
import {
  FeasibilityMark,
  FeasibilityMarksProvider,
} from '@/components/schedule/feasibility/FeasibilityMark';
import { WeekTimelineBoard } from '@/components/schedule/timeline/WeekTimelineBoard';

const REASON_HARD =
  '前の予定（甲様 〜10:35）から 5 分しかありません。移動に約 17 分かかります（直線 5.8 km）。';

function response(over: Partial<FeasibilityMarksResponse> = {}): FeasibilityMarksResponse {
  return {
    dateFrom: '2026-11-02',
    dateTo: '2026-11-08',
    hardCount: 1,
    softCount: 1,
    unassignedCount: 0,
    items: [
      {
        tier: 'hard',
        kind: '移動不可',
        day: '2026-11-02',
        at: '10:40',
        staff: '看護X',
        patientName: '乙川 二郎',
        reason: REASON_HARD,
        visitIds: ['a'],
      },
      {
        tier: 'soft',
        kind: 'バッファ不足',
        day: '2026-11-03',
        at: '09:30',
        staff: '看護Y',
        patientName: '丙山 三郎',
        reason: '前の予定から 12 分です。',
        visitIds: ['pb'],
      },
    ],
    marks: {
      a: [{ tier: 'hard', kind: '移動不可', reason: REASON_HARD }],
      pb: [{ tier: 'soft', kind: 'バッファ不足', reason: '前の予定から 12 分です。' }],
    },
    ...over,
  };
}

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

const VISITS: WeekOverviewVisit[] = [
  wv({ id: 'a', weekday: 0, start_time: '10:40:00', end_time: '11:15:00' }),
  wv({ id: 'b', weekday: 0, start_time: '13:00:00', end_time: '13:35:00' }),
  wv({ id: 'pa', weekday: 1, same_address_key: 'addr1' }),
  wv({ id: 'pb', weekday: 1, same_address_key: 'addr1' }),
];

beforeEach(() => {
  marksState.data = response();
  marksState.lastEnabled = null;
  toastInfo.mockReset();
});

describe('FeasibilityMark', () => {
  it('赤は「！」で、押すと理由が出る', () => {
    render(
      <FeasibilityMark
        marks={[{ tier: 'hard', kind: '移動不可', reason: REASON_HARD }]}
        testId="m"
      />,
    );
    const mark = screen.getByTestId('m');
    expect(mark).toHaveAttribute('data-tier', 'hard');
    expect(mark).toHaveTextContent('！');
    fireEvent.click(mark);
    const pop = screen.getByTestId('m-reasons');
    expect(within(pop).getByText(REASON_HARD)).toBeInTheDocument();
    expect(within(pop).getByText(/無理（移動不可）/)).toBeInTheDocument();
  });

  it('黄だけなら「△」・2 件以上なら数を添え、いちばん重い色にする', () => {
    const { rerender } = render(
      <FeasibilityMark marks={[{ tier: 'soft', kind: 'バッファ不足', reason: 'x' }]} testId="m" />,
    );
    expect(screen.getByTestId('m')).toHaveAttribute('data-tier', 'soft');
    expect(screen.getByTestId('m')).toHaveTextContent('△');
    rerender(
      <FeasibilityMark
        marks={[
          { tier: 'soft', kind: 'バッファ不足', reason: 'x' },
          { tier: 'hard', kind: '重なり', reason: 'y' },
        ]}
        testId="m"
      />,
    );
    expect(screen.getByTestId('m')).toHaveAttribute('data-tier', 'hard');
    expect(screen.getByTestId('m')).toHaveTextContent('2');
  });

  it('押してもカード (親) のクリック・ドラッグの開始へ伝えない', () => {
    const onClick = vi.fn();
    const onPointerDown = vi.fn();
    render(
      <div onClick={onClick} onPointerDown={onPointerDown}>
        <FeasibilityMark marks={[{ tier: 'hard', kind: '重なり', reason: 'y' }]} testId="m" />
      </div>,
    );
    fireEvent.pointerDown(screen.getByTestId('m'));
    fireEvent.click(screen.getByTestId('m'));
    expect(onPointerDown).not.toHaveBeenCalled();
    expect(onClick).not.toHaveBeenCalled();
  });

  it('印が無ければ何も出さない', () => {
    const { container } = render(<FeasibilityMark marks={[]} testId="m" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('週の時間軸のカードの印', () => {
  it('Provider の中では印の付いた訪問 (単独・同住所ペア) にだけ印が出る', () => {
    render(
      <FeasibilityMarksProvider dateFrom="2026-11-02" dateTo="2026-11-08" enabled>
        <WeekTimelineBoard options={[{ templateId: 't1', label: '稲毛A' }]} visits={VISITS} />
      </FeasibilityMarksProvider>,
    );
    expect(screen.getByTestId('wtl-feas-mark-a')).toHaveAttribute('data-tier', 'hard');
    expect(screen.getByTestId('wtl-feas-mark-pb')).toHaveAttribute('data-tier', 'soft');
    expect(screen.queryByTestId('wtl-feas-mark-b')).toBeNull();
    expect(screen.queryByTestId('wtl-feas-mark-pa')).toBeNull();
    // 印はカード (<button>) の中に入れない (ボタンの入れ子にしない)
    expect(within(screen.getByTestId('wtl-visit-a')).queryByTestId('wtl-feas-mark-a')).toBeNull();
  });

  it('管理者以外 (enabled=false) と Provider の外では印が出ない', () => {
    const { unmount } = render(
      <FeasibilityMarksProvider dateFrom="2026-11-02" dateTo="2026-11-08" enabled={false}>
        <WeekTimelineBoard options={[{ templateId: 't1', label: '稲毛A' }]} visits={VISITS} />
      </FeasibilityMarksProvider>,
    );
    expect(marksState.lastEnabled).toBe(false);
    expect(screen.queryByTestId('wtl-feas-mark-a')).toBeNull();
    unmount();
    render(<WeekTimelineBoard options={[{ templateId: 't1', label: '稲毛A' }]} visits={VISITS} />);
    expect(screen.queryByTestId('wtl-feas-mark-a')).toBeNull();
  });
});

describe('FeasibilityBand', () => {
  it('件数を出し、一覧の行を押すとそのカードへ移る', () => {
    const scrollIntoView = vi.fn();
    render(
      <FeasibilityMarksProvider dateFrom="2026-11-02" dateTo="2026-11-08" enabled>
        <FeasibilityBand scopeLabel="この週" cardTestIdPrefixes={['wtl-visit-']} />
        <button
          type="button"
          data-testid="wtl-visit-a"
          ref={(el) => {
            if (el) el.scrollIntoView = scrollIntoView;
          }}
        >
          card
        </button>
      </FeasibilityMarksProvider>,
    );
    expect(screen.getByTestId('feasibility-band-hard')).toHaveTextContent('1');
    expect(screen.getByTestId('feasibility-band-soft')).toHaveTextContent('1');
    expect(screen.getByText('この週に確かめたい予定があります')).toBeInTheDocument();
    expect(screen.queryByTestId('feasibility-band-item-list')).toBeNull();
    fireEvent.click(screen.getByTestId('feasibility-band-toggle'));
    const row = screen.getByTestId('feasibility-band-item-0');
    expect(row).toHaveTextContent('11/2（月）');
    expect(row).toHaveTextContent('乙川 二郎様');
    expect(row).toHaveTextContent(REASON_HARD);
    fireEvent.click(row);
    expect(scrollIntoView).toHaveBeenCalled();
    expect(screen.getByTestId('wtl-visit-a')).toHaveClass('ring-error');
    // 画面に無いカードは案内だけ出す
    fireEvent.click(screen.getByTestId('feasibility-band-item-1'));
    expect(toastInfo).toHaveBeenCalled();
  });

  it('赤・黄が 0 件のときと、管理者以外のときは出さない', () => {
    marksState.data = response({ hardCount: 0, softCount: 0, items: [], marks: {} });
    const { unmount } = render(
      <FeasibilityMarksProvider dateFrom="2026-11-02" dateTo="2026-11-08" enabled>
        <FeasibilityBand scopeLabel="この週" cardTestIdPrefixes={['wtl-visit-']} />
      </FeasibilityMarksProvider>,
    );
    expect(screen.queryByTestId('feasibility-band')).toBeNull();
    unmount();
    marksState.data = response();
    render(
      <FeasibilityMarksProvider dateFrom="2026-11-02" dateTo="2026-11-08" enabled={false}>
        <FeasibilityBand scopeLabel="この週" cardTestIdPrefixes={['wtl-visit-']} />
      </FeasibilityMarksProvider>,
    );
    expect(screen.queryByTestId('feasibility-band')).toBeNull();
  });
});

describe('ImportFeasibilityPanel (取り込み結果の画面)', () => {
  it('新しく出た無理・注意の件数と一覧を出す', () => {
    const r = response();
    render(
      <ImportFeasibilityPanel
        feasibility={{
          ok: true,
          hardCount: 1,
          softCount: 1,
          unassignedCount: 2,
          items: r.items,
          message: null,
        }}
      />,
    );
    expect(screen.getByTestId('import-feasibility')).toHaveAttribute('data-state', 'found');
    expect(screen.getByTestId('import-feasibility-hard')).toHaveTextContent('1');
    expect(screen.getByTestId('import-feasibility-soft')).toHaveTextContent('1');
    expect(screen.getByTestId('import-feasibility-item-0')).toHaveTextContent(REASON_HARD);
    expect(screen.getByTestId('import-feasibility-unassigned')).toHaveTextContent('2');
  });

  it('何も無いときは緑の 1 行・判定できなかったときはその文・null は出さない', () => {
    const { rerender, container } = render(
      <ImportFeasibilityPanel
        feasibility={{
          ok: true,
          hardCount: 0,
          softCount: 0,
          unassignedCount: 0,
          items: [],
          message: null,
        }}
      />,
    );
    expect(screen.getByTestId('import-feasibility')).toHaveTextContent(
      '取り込みで新しく出た無理・注意はありません。',
    );
    rerender(
      <ImportFeasibilityPanel
        feasibility={{
          ok: false,
          hardCount: 0,
          softCount: 0,
          unassignedCount: 0,
          items: [],
          message: '取り込み後の確認ができませんでした（取り込みは終わっています）',
        }}
      />,
    );
    expect(screen.getByTestId('import-feasibility')).toHaveAttribute('data-state', 'failed');
    expect(screen.getByTestId('import-feasibility')).toHaveTextContent('確認ができませんでした');
    rerender(<ImportFeasibilityPanel feasibility={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
