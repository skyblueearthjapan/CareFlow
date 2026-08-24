/**
 * EventsFilterBar vitest (staff-event-history-design.md §2 Phase 1 /
 * docs/mockups/event-history-filter-mock.html)。
 *
 * カバーするシナリオ:
 *   1. 期間タブ — 既定は「今週」(先頭タブ)・切替で tab が親に返る
 *   2. eventPeriodRange — 今後/今週/過去/すべて の from/to と並び順
 *   3. 検索 — 300ms デバウンス後に 1 回だけ親へ流れる
 *   4. チップ — 定例を隠す / 出所の排他トグル / 研修のみ
 *   5. 件数表示 — 未絞り込み「N件」/ 絞り込み中「N件を表示中（全M件から絞り込み）」
 *   6. 絞り込みを解除 — 期間タブは保ったまま他をリセット
 *   7. toStaffEventFilters — BE クエリパラメータへの変換
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';

import {
  DEFAULT_EVENTS_FILTER,
  EventsFilterBar,
  eventPeriodRange,
  isEventsFiltered,
  todayIso,
  toStaffEventFilters,
  type EventsFilterState,
} from '../EventsFilterBar';

/** 2026-08-24 は月曜。今週 = 8/24(月)〜8/30(日)。 */
const MONDAY = new Date(2026, 7, 24);

function setup(overrides: Partial<EventsFilterState> = {}, counts = { count: 3, total: 10 }) {
  const value: EventsFilterState = { ...DEFAULT_EVENTS_FILTER, ...overrides };
  const onChange = vi.fn();
  const utils = render(
    <EventsFilterBar value={value} onChange={onChange} count={counts.count} total={counts.total} />,
  );
  return { onChange, value, ...utils };
}

describe('EventsFilterBar — 期間タブ', () => {
  it('1. 既定は「今週」が選択されており、他タブを押すと tab が返る', () => {
    // PO 2026-08-25: 開いた瞬間は「今週」(当初の「今後」から変更)。タブ順も今週が先頭。
    const { onChange } = setup();
    expect(DEFAULT_EVENTS_FILTER.tab).toBe('week');
    const tabs = screen.getAllByRole('tab').map((t) => t.textContent);
    expect(tabs).toEqual(['今週', '今後', '過去', 'すべて']);
    expect(screen.getByRole('tab', { name: '今週' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: '今後' })).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByRole('tab', { name: '過去' })).toHaveAttribute('aria-selected', 'false');

    fireEvent.click(screen.getByRole('tab', { name: '過去' }));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0]).toMatchObject({ tab: 'past' });
  });

  it('2. eventPeriodRange — 各タブの from/to と並び順', () => {
    const future = eventPeriodRange('future', MONDAY);
    expect(future.order).toBe('asc');
    expect(future.range).toEqual({ from: '2026-08-24', to: '2027-02-20' });

    const week = eventPeriodRange('week', MONDAY);
    expect(week.order).toBe('asc');
    expect(week.range).toEqual({ from: '2026-08-24', to: '2026-08-30' });

    // 週の途中 (木曜) でも月曜始まりで同じ週になる
    expect(eventPeriodRange('week', new Date(2026, 7, 27)).range).toEqual({
      from: '2026-08-24',
      to: '2026-08-30',
    });
    // 日曜は「その週」の末日 (月曜始まり)
    expect(eventPeriodRange('week', new Date(2026, 7, 30)).range).toEqual({
      from: '2026-08-24',
      to: '2026-08-30',
    });

    const past = eventPeriodRange('past', MONDAY);
    expect(past.order).toBe('desc');
    expect(past.range).toEqual({ to: '2026-08-23' });

    const all = eventPeriodRange('all', MONDAY);
    expect(all.order).toBe('asc');
    expect(all.range).toBeUndefined();

    expect(todayIso(MONDAY)).toBe('2026-08-24');
  });
});

describe('EventsFilterBar — 検索', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('3. 300ms デバウンスしてから 1 回だけ親へ通知する', () => {
    const { onChange } = setup();
    const input = screen.getByLabelText('イベントを検索');

    fireEvent.change(input, { target: { value: '鈴' } });
    fireEvent.change(input, { target: { value: '鈴木' } });
    act(() => {
      vi.advanceTimersByTime(299);
    });
    expect(onChange).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0]).toMatchObject({ q: '鈴木' });
  });

  it('3b. 前後の空白は落として渡す / 既存値と同じなら通知しない', () => {
    const { onChange } = setup({ q: '面談' });
    const input = screen.getByLabelText('イベントを検索');
    expect(input).toHaveValue('面談');

    fireEvent.change(input, { target: { value: '  面談  ' } });
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: ' 面談 松岡 ' } });
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0]).toMatchObject({ q: '面談 松岡' });
  });
});

describe('EventsFilterBar — チップ', () => {
  it('4. 定例を隠す / 研修のみ はトグル', () => {
    const { onChange } = setup();
    fireEvent.click(screen.getByRole('button', { name: '定例を隠す' }));
    expect(onChange.mock.calls[0][0]).toMatchObject({ hideRegular: true });

    onChange.mockClear();
    fireEvent.click(screen.getByRole('button', { name: '研修のみ' }));
    expect(onChange.mock.calls[0][0]).toMatchObject({ trainingOnly: true });
  });

  it('4b. ON の状態をもう一度押すと解除される', () => {
    const { onChange } = setup({ hideRegular: true });
    const chip = screen.getByRole('button', { name: '定例を隠す' });
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(chip);
    expect(onChange.mock.calls[0][0]).toMatchObject({ hideRegular: false });
  });

  it('4c. 出所チップ (カイポケ取込 / 手動) は排他', () => {
    const { onChange } = setup({ source: 'kaipoke' });
    expect(screen.getByRole('button', { name: 'カイポケ取込' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: '手動' })).toHaveAttribute('aria-pressed', 'false');

    // 別の出所を押すと置き換わる (両立しない)
    fireEvent.click(screen.getByRole('button', { name: '手動' }));
    expect(onChange.mock.calls[0][0]).toMatchObject({ source: 'manual' });

    // 同じ出所を押すと解除
    onChange.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'カイポケ取込' }));
    expect(onChange.mock.calls[0][0]).toMatchObject({ source: null });
  });
});

describe('EventsFilterBar — 件数表示と解除', () => {
  it('5. 未絞り込みは「N件」', () => {
    setup({}, { count: 12, total: 12 });
    expect(screen.getByTestId('events-count')).toHaveTextContent('12件');
    expect(screen.queryByRole('button', { name: '絞り込みを解除' })).toBeNull();
  });

  it('5b. 絞り込み中は「N件を表示中（全M件から絞り込み）」+ 解除リンク', () => {
    setup({ hideRegular: true }, { count: 4, total: 30 });
    expect(screen.getByTestId('events-count')).toHaveTextContent(
      '4件を表示中（全30件から絞り込み）',
    );
    expect(screen.getByRole('button', { name: '絞り込みを解除' })).toBeInTheDocument();
  });

  it('6. 絞り込みを解除 — 期間タブは保ち、検索欄も空になる', () => {
    const { onChange } = setup(
      { tab: 'past', q: '鈴木', hideRegular: true, source: 'manual', trainingOnly: true },
      { count: 1, total: 40 },
    );
    fireEvent.click(screen.getByRole('button', { name: '絞り込みを解除' }));
    expect(onChange).toHaveBeenCalledWith({
      tab: 'past',
      q: '',
      hideRegular: false,
      source: null,
      trainingOnly: false,
    });
    expect(screen.getByLabelText('イベントを検索')).toHaveValue('');
  });

  it('6b. isEventsFiltered — 期間タブだけの変更は「絞り込み」ではない', () => {
    expect(isEventsFiltered(DEFAULT_EVENTS_FILTER)).toBe(false);
    expect(isEventsFiltered({ ...DEFAULT_EVENTS_FILTER, tab: 'past' })).toBe(false);
    expect(isEventsFiltered({ ...DEFAULT_EVENTS_FILTER, q: '  ' })).toBe(false);
    expect(isEventsFiltered({ ...DEFAULT_EVENTS_FILTER, q: '鈴木' })).toBe(true);
    expect(isEventsFiltered({ ...DEFAULT_EVENTS_FILTER, source: 'kaipoke' })).toBe(true);
    expect(isEventsFiltered({ ...DEFAULT_EVENTS_FILTER, trainingOnly: true })).toBe(true);
  });
});

describe('toStaffEventFilters', () => {
  it('7. BE クエリパラメータへ変換する (空値は undefined / null)', () => {
    expect(toStaffEventFilters(DEFAULT_EVENTS_FILTER, 'asc')).toEqual({
      q: undefined,
      source: null,
      type: null,
      order: 'asc',
      hideRegular: false,
    });

    expect(
      toStaffEventFilters(
        { tab: 'past', q: ' 面談 ', hideRegular: true, source: 'kaipoke', trainingOnly: true },
        'desc',
      ),
    ).toEqual({
      q: '面談',
      source: 'kaipoke',
      type: 'training',
      order: 'desc',
      hideRegular: true,
    });
  });
});
