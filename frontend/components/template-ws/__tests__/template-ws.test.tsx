/**
 * コースの作業場: 盤面の組み立て・数の物差し・最新の編集（人 / らく助エージェント）・履歴・出口 2 つのダイアログ。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

const { previewFn, weekFn } = vi.hoisted(() => ({ previewFn: vi.fn(), weekFn: vi.fn() }));
vi.mock('@/lib/queries/templateWs', () => ({
  useCommitPreview: () => previewFn(),
  useWeekPreview: () => weekFn(),
  useCompare: () => ({ data: undefined, isLoading: false, error: null }),
  useInputs: () => ({ data: undefined, isLoading: false }),
}));

import { buildGrid, fmtMin, latestStepKeys, parseHm, stepTitle, tone } from '@/lib/templateWs/helpers';
import type { DraftView, SlotCard, Step } from '@/lib/templateWs/types';
import { CommitDialog } from '../Dialogs';
import { MetricsHeader } from '../MetricsHeader';
import { HistoryList, LatestStepCard, UndoRedo } from '../SidePanels';
import { TemplateBoard } from '../TemplateBoard';

function slot(over: Partial<SlotCard>): SlotCard {
  return {
    key: 'k1', patient_id: 'p1', patient: '青木 花子 様', code: 'P1', slot_index: 0, weekday: 0, start: 570, dur: 35,
    course_id: 'cA', base_course_id: 'cA', base_start: 570, locked: false, new: false, held: false, pair: false,
    course_changed: false, time_changed: false, changed: false, win: [570, 780], why: '希望 時間帯',
    time_type: '時間帯', placed: true, unplaced_reason: null, ...over,
  };
}

const step = (over: Partial<Step>): Step => ({
  seq: 1, actor: 'human', actor_label: 'あなた', kind: 'move', reason: null, undone: false, version_after: 2,
  created_at: null, changed_slots: 1, changes: [{ key: 'k2', patient_id: 'p2', weekday: 0, before: null, after: null }],
  params_changed: false, ...over,
});

function view(over: Partial<DraftView> = {}): DraftView {
  const m = { travel_min: 130, conflicts: 5, over_cap: 1, no_course: 5, unstaffed: 0 };
  return {
    id: 'd1', name: 'ina-v1', status: 'draft', version: 3, office: { id: 'o', label: '稲' },
    scenario: { staff_count: {}, drop_staff: [], add_patients: {}, prefs: {}, red: 'keep' },
    compare: { name: 'いまの型（マスター）', draft_id: null },
    courses: [
      { id: 'cA', label: '稲A', short: 'A', manager: false },
      { id: 'cB', label: '稲B', short: 'B', manager: false },
    ],
    days: [
      {
        weekday: 0, name: '月', notes: [],
        courses: [
          { id: 'cA', label: '稲A', manager: false, virtual: false, staff: '山田', shift: [540, 1080], events: [], cap: 6 },
          { id: 'cB', label: '稲B', manager: false, virtual: false, staff: '佐藤', shift: [540, 1080], events: [], cap: 6 },
        ],
        slots: [
          slot({ key: 'k1' }),
          slot({ key: 'k2', patient: '井上 太郎 様', course_id: 'cB', start: 690, changed: true, course_changed: true, base_course_id: 'cA' }),
          slot({ key: 'k3', patient: '赤田 次郎 様', start: 720, locked: true }),
          slot({ key: 'k4', patient: '誰か 様', course_id: null, placed: false, start: null }),
        ],
        metrics: { master: { ...m }, draft: { ...m, conflicts: 0 }, changes: 1, new_placed: 0, new_unplaced: 0 },
      },
    ],
    tray: [], staff: [], totals: { master: m, draft: { ...m, conflicts: 0, no_course: 0 } },
    steps: [], latest_step: null, can_undo: false, can_redo: false, ...over,
  };
}

describe('helpers', () => {
  it('分と時刻の変換', () => {
    expect(fmtMin(570)).toBe('9:30');
    expect(fmtMin(null)).toBe('--:--');
    expect(parseHm('09:05')).toBe(545);
    expect(parseHm('25:00')).toBeNull();
    expect(parseHm('abc')).toBeNull();
  });
  it('数の差の向き（小さいほどよい）', () => {
    expect(tone(5, 0)).toBe('good');
    expect(tone(0, 2)).toBe('bad');
    expect(tone(3, 3)).toBe('same');
  });
  it('盤面: 行 = コース・置いていない枠は出さない', () => {
    const rows = buildGrid(view());
    expect(rows.map((r) => r.id)).toEqual(['cA', 'cB']);
    expect(rows[0]!.cells[0]!.slots.map((s) => s.key)).toEqual(['k1', 'k3']);
    expect(rows[1]!.cells[0]!.slots.map((s) => s.key)).toEqual(['k2']);
    expect(rows[0]!.cells[1]).toBeUndefined();
  });
  it('「あなた」「らく助エージェント」の見出し', () => {
    expect(stepTitle(step({}))).toBe('あなたが枠を動かしました');
    expect(stepTitle(step({ actor: 'agent', actor_label: 'らく助エージェント', kind: 'optimise' }))).toBe(
      'らく助エージェントが最適化しました',
    );
  });
  it('最新の編集の枠だけ目立たせる（取り消し済みは出さない）', () => {
    expect([...latestStepKeys(view({ latest_step: step({}) }))]).toEqual(['k2']);
    expect(latestStepKeys(view({ latest_step: step({ undone: true }) })).size).toBe(0);
  });
});

describe('MetricsHeader', () => {
  it('前後の数と色の向きを出す', () => {
    render(<MetricsHeader view={view()} />);
    expect(screen.getByTestId('metric-conflicts')).toHaveTextContent('5 → 0');
    expect(screen.getByTestId('metric-conflicts')).toHaveAttribute('data-tone', 'good');
    expect(screen.getByTestId('metric-over_cap')).toHaveAttribute('data-tone', 'same');
    expect(screen.getByText('移動が間に合わない組')).toBeInTheDocument();
    expect(screen.getByText('人のいないコース')).toBeInTheDocument();
  });
});

describe('TemplateBoard', () => {
  const props = {
    readOnly: false, highlight: new Set<string>(['k2']), selectedKey: null,
    onSelect: vi.fn(), onDropToCourse: vi.fn(), onWeekdayRefused: vi.fn(),
  };
  it('変わった枠・赤ピン・最新の編集を見分けられる', () => {
    render(<TemplateBoard view={view()} {...props} />);
    expect(screen.getByTestId('slot-k2')).toHaveAttribute('data-changed', 'true');
    expect(screen.getByTestId('slot-k2')).toHaveAttribute('data-latest', 'true');
    expect(screen.getByTestId('slot-k1')).toHaveAttribute('data-changed', 'false');
    expect(screen.getByLabelText('赤ピン（曜日と時刻は動かしません）')).toBeInTheDocument();
    expect(screen.getByTestId('cell-0-cA')).toHaveTextContent('担当: 山田');
  });
  it('枠を押すと選べる', () => {
    const onSelect = vi.fn();
    render(<TemplateBoard view={view()} {...props} onSelect={onSelect} />);
    fireEvent.click(screen.getByTestId('slot-k1'));
    expect(onSelect).toHaveBeenCalledWith('k1');
  });
});

describe('最新の編集と履歴', () => {
  it('エージェントの編集に理由を出し、閉じられる', () => {
    const onDismiss = vi.fn();
    render(
      <LatestStepCard
        step={step({ actor: 'agent', actor_label: 'らく助エージェント', reason: '火曜の A を軽くしました' })}
        onDismiss={onDismiss}
      />,
    );
    const card = screen.getByTestId('latest-step');
    expect(card).toHaveAttribute('data-actor', 'agent');
    expect(card).toHaveTextContent('らく助エージェントが枠を動かしました');
    expect(card).toHaveTextContent('火曜の A を軽くしました');
    fireEvent.click(screen.getByText('閉じる'));
    expect(onDismiss).toHaveBeenCalled();
  });
  it('↶ ↷ は戻せるときだけ押せる', () => {
    const onUndo = vi.fn();
    const v = view({ can_undo: true, can_redo: false, steps: [step({}), step({ seq: 2, undone: true })] });
    render(
      <>
        <UndoRedo view={v} disabled={false} onUndo={onUndo} onRedo={vi.fn()} />
        <HistoryList view={v} />
      </>,
    );
    fireEvent.click(screen.getByTestId('undo'));
    expect(onUndo).toHaveBeenCalled();
    expect(screen.getByTestId('redo')).toBeDisabled();
    expect(screen.getByTestId('step-2').className).toContain('line-through');
  });
});

describe('CommitDialog（出口 2 つ）', () => {
  beforeEach(() => {
    previewFn.mockReturnValue({
      isLoading: false, error: null,
      data: {
        ready: true, blockers: [], warnings: [], note: '',
        counts: { patients: 3, slots: 5, course: 4, time: 1, new: 1 }, patients: [],
      },
    });
    weekFn.mockReturnValue({
      isLoading: false, error: null,
      data: {
        week_hash: 'h', counts: { course: 2, time: 1, add: 1, skipped: 3 }, skipped: [], course_notes: [], note: '',
      },
    });
  });
  const base = {
    open: true, draftId: 'd1', version: 3, isoYear: 2026, isoWeek: 42, busy: false,
    onClose: vi.fn(), onCommit: vi.fn(), onWeekApply: vi.fn(),
  };
  it('① 型に登録: 件数と「次に週を生成した週から」を落ち着いて伝える', () => {
    const onCommit = vi.fn();
    render(<CommitDialog {...base} onCommit={onCommit} />);
    expect(screen.getByTestId('commit-counts')).toHaveTextContent('患者 3 名');
    expect(screen.getByTestId('commit-master')).toHaveTextContent('次に「週を生成」した週から反映されます');
    fireEvent.click(screen.getByTestId('commit-run'));
    expect(onCommit).toHaveBeenCalled();
  });
  it('登録できない理由があれば押せない', () => {
    previewFn.mockReturnValue({
      isLoading: false, error: null,
      data: { ready: false, blockers: ['固定訪問の枠が変わった方がいます'], warnings: [], note: '', counts: { patients: 0, slots: 0, course: 0, time: 0, new: 0 }, patients: [] },
    });
    render(<CommitDialog {...base} />);
    expect(screen.getByTestId('commit-run')).toBeDisabled();
    expect(screen.getByText(/まだ登録できません/)).toBeInTheDocument();
  });
  it('② この週のコースに入れる: 4 つの件数を出し、ハッシュを渡す', () => {
    const onWeekApply = vi.fn();
    render(<CommitDialog {...base} onWeekApply={onWeekApply} />);
    fireEvent.click(screen.getByTestId('exit-week'));
    const t = screen.getByTestId('week-counts');
    expect(t).toHaveTextContent('コースが変わる訪問 2');
    expect(t).toHaveTextContent('時刻が変わる訪問 1');
    expect(t).toHaveTextContent('今週だけ足す訪問 1');
    expect(t).toHaveTextContent('その週に無くて入れられない枠 3');
    fireEvent.click(screen.getByTestId('week-run'));
    expect(onWeekApply).toHaveBeenCalledWith('h');
  });
});
