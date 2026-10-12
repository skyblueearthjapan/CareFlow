/**
 * コースの作業場 (2026-10-13): 盤面の幅の計算・全拠点の盤面・最適化の報告・右の列の幅を変える境目。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

const { reportFn } = vi.hoisted(() => ({ reportFn: vi.fn() }));
vi.mock('@/lib/queries/templateWs', () => ({
  useReport: () => reportFn(),
}));

import { useResizableWidth, clampWidth } from '@/components/agent/useResizableWidth';
import { buildGrid, dayCounts, isNoCourse } from '@/lib/templateWs/helpers';
import type { DraftView, OptimiseReport, SlotCard, Step } from '@/lib/templateWs/types';
import { MetricsHeader } from '../MetricsHeader';
import { OptimiseReportPanel, ReportBody } from '../OptimiseReport';
import { ResizeHandle } from '../ResizeHandle';
import { HistoryList, LatestStepCard, Tray } from '../SidePanels';
import { DAY_COL_MIN_PX, LABEL_COL_PX, TemplateBoard } from '../TemplateBoard';
import { DOCK_DEFAULT_PX } from '../TemplateWorkspace';

function slot(over: Partial<SlotCard>): SlotCard {
  return {
    key: 'k1', patient_id: 'p1', patient: '青木 花子 様', code: 'P1', slot_index: 0, weekday: 0, start: 570, dur: 35,
    course_id: 'cA', base_course_id: 'cA', base_start: 570, locked: false, new: false, held: false, pair: false,
    course_changed: false, time_changed: false, changed: false, win: [570, 780], why: '希望 時間帯',
    time_type: '時間帯', placed: true, unplaced_reason: null, ...over,
  };
}

const m = { travel_min: 130, conflicts: 5, over_cap: 1, no_course: 5, unstaffed: 0 };

/** 全拠点の下書き: 稲（A・B）と 都（A）。都の方は 1 名コースなし。 */
function allView(): DraftView {
  return {
    id: 'd1', name: '全体', status: 'draft', version: 3, office: { id: null, label: '全拠点' },
    offices: [{ id: 'o1', label: '稲' }, { id: 'o2', label: '都' }], all_offices: true,
    scenario: { staff_count: {}, drop_staff: [], add_patients: {}, prefs: {}, red: 'keep' },
    compare: { name: 'いまの型（マスター）', draft_id: null },
    courses: [
      { id: 'cA', label: '稲A', short: 'A', manager: false, office_id: 'o1', office: '稲' },
      { id: 'cB', label: '稲B', short: 'B', manager: false, office_id: 'o1', office: '稲' },
      { id: 'tA', label: '都A', short: 'A', manager: false, office_id: 'o2', office: '都' },
    ],
    days: [
      {
        weekday: 0, name: '月', notes: [],
        courses: [
          { id: 'cA', label: '稲A', manager: false, virtual: false, staff: '山田', shift: [540, 1080], events: [], cap: 6, office_id: 'o1' },
          { id: 'cB', label: '稲B', manager: false, virtual: false, staff: '佐藤', shift: [540, 1080], events: [], cap: 6, office_id: 'o1' },
          { id: 'tA', label: '都A', manager: false, virtual: false, staff: '鈴木', shift: [540, 1080], events: [], cap: 6, office_id: 'o2' },
        ],
        slots: [
          slot({ key: 'k1', office_id: 'o1' }),
          slot({ key: 'k2', patient: '井上 太郎 様', course_id: 'cB', start: 690, changed: true, course_changed: true, base_course_id: 'cA', office_id: 'o1' }),
          slot({ key: 't1', patient: '都の方 様', course_id: null, base_course_id: null, start: 600, office_id: 'o2' }),
        ],
        metrics: { master: { ...m }, draft: { ...m }, changes: 1, new_placed: 0, new_unplaced: 0 },
      },
    ],
    tray: [{ patient_id: 'p9', code: 'P9', name: '新しい方 様', days: ['月'], reason: null, state: 'waiting', added: false, office: '都' }],
    staff: [], totals: { master: m, draft: { ...m, conflicts: 0 } },
    totals_by_office: [
      { id: 'o1', label: '稲', master: { ...m, conflicts: 4 }, draft: { ...m, conflicts: 0 } },
      { id: 'o2', label: '都', master: { ...m, conflicts: 1 }, draft: { ...m, conflicts: 0 } },
    ],
    steps: [], latest_step: null, can_undo: false, can_redo: false,
  };
}

const optimiseStep: Step = {
  seq: 7, actor: 'human', actor_label: 'あなた', kind: 'optimise', reason: null, undone: false, version_after: 8,
  created_at: null, changed_slots: 66, changes: [], params_changed: false, has_report: true,
};

const report: OptimiseReport = {
  draft_id: 'd1', draft: '全体', seq: 7, actor_label: 'あなた', reason: null, undone: false, created_at: null,
  changed: 3,
  categories: [
    { kind: 'course_added', label: 'コースが無かった枠にコースを付けた', count: 1, items: [
      { key: 't1', weekday: 0, weekday_ja: '月', patient_id: 'p', patient: '都の方 様', office: '都',
        from_course: '（コースなし）', to_course: '都A', from_start: '10:00', to_start: '10:00', red: false }] },
    { kind: 'course_only', label: 'コースだけ変えた', count: 2, items: [] },
  ],
  days: [{
    weekday: 0, name: '月',
    master: { conflicts: 5, over_cap: 1, no_course: 5, unstaffed: 0, travel_min: 100, regular_counts: [5, 7, 3], manager_visits: 1 },
    before: { conflicts: 5, over_cap: 1, no_course: 5, unstaffed: 0, travel_min: 100, regular_counts: [5, 7, 3], manager_visits: 1 },
    after: { conflicts: 0, over_cap: 0, no_course: 0, unstaffed: 0, travel_min: 140, regular_counts: [6, 6, 6], manager_visits: 2 },
    changes: 3, travel: { delta: 40, reorder: 5, routed: 35, newly: { no_course: 5, unstaffed: 0, new: 0 } },
  }],
  travel: { before: 100, after: 140, delta: 40, reorder: 5, routed: 35 },
  text: ['変わった枠は 3 件です。', '移動の合計は 100 分 → 140 分（+40 分）に増えました。このうち +35 分は、前は道順に入っておらず移動に数えられていなかった訪問（コースの無い枠 5 件）を道順に入れた分です。'],
  cautions: ['管理者（M のコース）の訪問が増えました（月曜 1→2）。'],
};

describe('盤面の幅（1600px・サイドバーを開いた形・右の列 380px）', () => {
  it('月〜土が横にスクロールせずに収まる', () => {
    const viewport = 1600;
    const outer = 14 * 2; // AppShell の外の余白
    const sidebar = 232; // サイドバーを開いた形
    const mainPad = 24 * 2; // 本文の余白（1536px 以上は p-6）
    const handle = 12; // 境目
    const board = viewport - outer - sidebar - mainPad - DOCK_DEFAULT_PX - handle;
    const scrollbar = 17;
    const need = LABEL_COL_PX + 6 * DAY_COL_MIN_PX + 2;
    expect(board).toBe(900);
    expect(board - scrollbar).toBeGreaterThanOrEqual(need); // 883 ≥ 798
    // 1 列あたりの幅（読みやすさ: 時刻＋お名前 6 字ほど）
    expect(Math.floor((board - scrollbar - 2 - LABEL_COL_PX) / 6)).toBeGreaterThanOrEqual(130);
    // 右の列を広げても、盤面がいちばん狭い形で収まるのは右の列 ≈ 470px まで（それより広いと横スクロール）
    expect(viewport - outer - sidebar - mainPad - handle - scrollbar - need).toBeGreaterThanOrEqual(460);
  });
});

describe('全拠点の盤面', () => {
  it('拠点ごとに行を並べ、コースなしの行も拠点ごと', () => {
    const rows = buildGrid(allView());
    expect(rows.map((r) => [r.office, r.label])).toEqual([
      ['稲', '稲A'], ['稲', '稲B'], ['都', '都A'], ['都', 'コースなし'],
    ]);
    expect(isNoCourse(rows[3]!.id)).toBe(true);
    expect(rows[3]!.cells[0]!.slots.map((s) => s.key)).toEqual(['t1']);
    expect(dayCounts(allView(), 0)).toEqual({ total: 3, byOffice: [{ label: '稲', n: 2 }, { label: '都', n: 1 }] });
  });
  it('拠点の見出しの行・曜日の見出しに拠点ごとの件数', () => {
    render(
      <TemplateBoard
        view={allView()} readOnly={false} highlight={new Set()} selectedKey={null}
        onSelect={vi.fn()} onDropToCourse={vi.fn()} onWeekdayRefused={vi.fn()}
      />,
    );
    expect(screen.getByTestId('office-head-o1')).toHaveTextContent('稲');
    expect(screen.getByTestId('office-head-o2')).toHaveTextContent('都');
    expect(screen.getByTestId('day-head-0')).toHaveTextContent('3 件（稲 2・都 1）');
    // 変わった枠は「←元のコース」の名前を出す
    expect(screen.getByTestId('slot-k2')).toHaveTextContent('←稲A');
  });
  it('報告から押した枠を光らせる', () => {
    render(
      <TemplateBoard
        view={allView()} readOnly={false} highlight={new Set()} selectedKey={null} flashKey="t1"
        onSelect={vi.fn()} onDropToCourse={vi.fn()} onWeekdayRefused={vi.fn()}
      />,
    );
    expect(screen.getByTestId('slot-t1')).toHaveAttribute('data-flash', 'true');
    expect(screen.getByTestId('slot-k1')).toHaveAttribute('data-flash', 'false');
  });
  it('数の物差し: 合計と、指を置くと拠点ごと', () => {
    render(<MetricsHeader view={allView()} />);
    expect(screen.getByTestId('metric-conflicts')).toHaveTextContent('5 → 0');
    expect(screen.getByTestId('metric-conflicts')).toHaveAttribute('title', '拠点ごと: 稲 4→0・都 1→0');
  });
  it('まだ枠の無い方に拠点を添える', () => {
    render(<Tray view={allView()} />);
    expect(screen.getByTestId('tray')).toHaveTextContent('都・月曜・最適化で置きます');
  });
});

describe('最適化の報告', () => {
  beforeEach(() => reportFn.mockReturnValue({ isLoading: false, error: null, data: report }));
  it('最適化の帯と履歴の行から［くわしく］で開ける', () => {
    const onOpen = vi.fn();
    render(<LatestStepCard step={optimiseStep} onDismiss={vi.fn()} onOpenReport={onOpen} />);
    fireEvent.click(screen.getByTestId('latest-report'));
    expect(onOpen).toHaveBeenCalledWith(7);
    render(<HistoryList view={{ ...allView(), steps: [optimiseStep] }} onOpenReport={onOpen} />);
    fireEvent.click(screen.getByTestId('step-report-7'));
    expect(onOpen).toHaveBeenCalledTimes(2);
  });
  it('手で動かした手順には［くわしく］を出さない', () => {
    render(<LatestStepCard step={{ ...optimiseStep, kind: 'move' }} onDismiss={vi.fn()} onOpenReport={vi.fn()} />);
    expect(screen.queryByTestId('latest-report')).toBeNull();
  });
  it('説明・気になる点・種類ごと・曜日ごとの表を出し、行を押すと盤面の枠を光らせる', () => {
    const onFlash = vi.fn();
    render(<OptimiseReportPanel draftId="d1" seq={7} onClose={vi.fn()} onFlash={onFlash} />);
    expect(screen.getByTestId('report-text')).toHaveTextContent('道順に入れた分');
    expect(screen.getByTestId('report-cautions')).toHaveTextContent('管理者');
    fireEvent.click(screen.getByTestId('report-kind-course_added'));
    fireEvent.click(screen.getByTestId('report-item-t1'));
    expect(onFlash).toHaveBeenCalledWith('t1');
    expect(screen.getByTestId('report-item-t1')).toHaveTextContent('（コースなし） 10:00');
    const days = within(screen.getByTestId('report-days'));
    expect(days.getByText('5・7・3 → 6・6・6')).toBeInTheDocument();
    expect(days.getByText('100 → 140')).toBeInTheDocument();
    expect(days.getByText('+35')).toBeInTheDocument();
  });
  it('画面の言葉に命令やエンジンの言葉・「直す/修正」を出さない', () => {
    const { container } = render(<ReportBody report={report} onFlash={vi.fn()} />);
    const t = container.textContent ?? '';
    for (const w of ['--', 'rk ', 'json', 'JSON', '直す', '修正', '補正']) expect(t).not.toContain(w);
  });
});

// jsdom には PointerEvent が無い（clientX が渡らない）ので、MouseEvent で足す
if (typeof window.PointerEvent === 'undefined') {
  class PointerEventPolyfill extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 0;
    }
  }
  (window as unknown as { PointerEvent: typeof MouseEvent }).PointerEvent = PointerEventPolyfill;
}

function Dock() {
  const w = useResizableWidth({ storageKey: 'test-dock', defaultWidth: 380, min: 300 });
  return (
    <div>
      <ResizeHandle handleProps={w.handleProps} label="らく助エージェントの列の幅" testId="h" />
      <aside data-testid="dock" style={{ width: w.width }} />
    </div>
  );
}

describe('右の列の幅を変える境目', () => {
  beforeEach(() => {
    window.localStorage.clear();
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1600 });
  });
  it('最小 300px・画面の 50% まで', () => {
    expect(clampWidth(100, 300, 800)).toBe(300);
    expect(clampWidth(1200, 300, 800)).toBe(800);
  });
  it('separator・←→ で変え、覚え、ダブルクリックで既定の幅に戻す', () => {
    const { unmount } = render(<Dock />);
    const h = screen.getByRole('separator', { name: 'らく助エージェントの列の幅' });
    expect(h).toHaveAttribute('aria-valuenow', '380');
    expect(h).toHaveAttribute('aria-valuemax', '800');
    fireEvent.keyDown(h, { key: 'ArrowLeft' });
    expect(h).toHaveAttribute('aria-valuenow', '396');
    expect(screen.getByTestId('dock').style.width).toBe('396px');
    fireEvent.keyDown(h, { key: 'ArrowRight', shiftKey: true });
    expect(h).toHaveAttribute('aria-valuenow', '332');
    expect(window.localStorage.getItem('test-dock')).toBe('332');
    unmount();
    render(<Dock />);
    const h2 = screen.getByRole('separator');
    expect(h2).toHaveAttribute('aria-valuenow', '332'); // 覚えた幅
    fireEvent.keyDown(h2, { key: 'End' });
    expect(h2).toHaveAttribute('aria-valuenow', '800');
    fireEvent.doubleClick(h2);
    expect(h2).toHaveAttribute('aria-valuenow', '380');
    expect(window.localStorage.getItem('test-dock')).toBeNull();
  });
  it('ドラッグ: 左へ動かすほど広くなる', () => {
    render(<Dock />);
    const h = screen.getByRole('separator');
    fireEvent.pointerDown(h, { clientX: 1000, button: 0, pointerId: 1 });
    fireEvent.pointerMove(h, { clientX: 900, pointerId: 1 });
    fireEvent.pointerUp(h, { clientX: 900, pointerId: 1 });
    expect(h).toHaveAttribute('aria-valuenow', '480');
    expect(window.localStorage.getItem('test-dock')).toBe('480');
  });
  it('localStorage が使えなくても動く', () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    render(<Dock />);
    const h = screen.getByRole('separator');
    fireEvent.keyDown(h, { key: 'ArrowLeft' });
    expect(h).toHaveAttribute('aria-valuenow', '396');
    spy.mockRestore();
    set.mockRestore();
  });
});

describe('最適化にかかる時間の見込み（backend の plan_seconds と同じ式）', () => {
  it('1 拠点はこれまでどおり・全拠点は 1 日あたりを拠点の数で分け、全体は 180 秒まで', async () => {
    const { estimateSeconds } = await import('@/lib/templateWs/helpers');
    expect(estimateSeconds(20, 1, 6)).toBe(120);
    expect(estimateSeconds(20, 2, 6)).toBe(120);
    expect(estimateSeconds(60, 1, 6)).toBe(180);
    expect(estimateSeconds(60, 2, 6)).toBe(180);
    expect(estimateSeconds(20, 1, 1)).toBe(20);
  });
});
