'use client';

/**
 * 「研修日 / イベント」カードのフィルタバー
 * (staff-event-history-design.md §2 Phase 1 / docs/mockups/event-history-filter-mock.html)。
 *
 * 期間タブ + 検索 (300ms デバウンス) + 種類チップ + 件数表示。**絞り込みは
 * 常に BE パラメータで行う** — 一覧は limit 200 の窓なので、FE 側で削ると
 * 「過去」が窓の外に落ちて拾えなくなる。本コンポーネントは状態を親に返す
 * だけで、フェッチには関与しない。
 */
import * as React from 'react';
import { Search } from 'lucide-react';

import { FilterChip } from '@/components/ui/filter-chip';
import { Input } from '@/components/ui/input';
import type { PartialDateRange, StaffEventFilters } from '@/lib/queries/staff-events';

/**
 * 期間タブ。既定は「今週」(PO 2026-08-25: 開いた瞬間に見たいのは今週の予定。
 * 当初の Q4 回答「今後」から変更)。「今後」は 2 番目のタブとして残す。
 */
export type EventPeriodTab = 'future' | 'week' | 'past' | 'all';

export const EVENT_PERIOD_TABS: ReadonlyArray<{ key: EventPeriodTab; label: string }> = [
  { key: 'week', label: '今週' },
  { key: 'future', label: '今後' },
  { key: 'past', label: '過去' },
  { key: 'all', label: 'すべて' },
];

export interface EventsFilterState {
  tab: EventPeriodTab;
  /** 検索語 (title + note の部分一致)。 */
  q: string;
  /** 定例 (固定イベント + 固定イベント既定のタイトル) を隠す。 */
  hideRegular: boolean;
  /** 出所チップ (排他)。null = 指定なし。 */
  source: 'kaipoke' | 'manual' | null;
  /** 研修のみ。 */
  trainingOnly: boolean;
}

export const DEFAULT_EVENTS_FILTER: EventsFilterState = {
  tab: 'week',
  q: '',
  hideRegular: false,
  source: null,
  trainingOnly: false,
};

/** 「今後」タブの先読み日数 (現行 EventsCard の窓と同じ)。 */
const FUTURE_DAYS = 180;

function isoLocalDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function shiftDays(d: Date, days: number): Date {
  const next = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  next.setDate(next.getDate() + days);
  return next;
}

/**
 * 期間タブ → BE の from/to と並び順。
 *
 *   今後   = 今日 〜 +180日 / 近い順
 *   今週   = 月曜 〜 日曜   / 近い順
 *   過去   = 〜 昨日        / 新しい順 (遡り)
 *   すべて = 期間指定なし   / 古い順 (現行と同じ並び)
 */
export function eventPeriodRange(
  tab: EventPeriodTab,
  today: Date = new Date(),
): { range?: PartialDateRange; order: 'asc' | 'desc' } {
  const base = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  switch (tab) {
    case 'future':
      return {
        range: { from: isoLocalDate(base), to: isoLocalDate(shiftDays(base, FUTURE_DAYS)) },
        order: 'asc',
      };
    case 'week': {
      // 月曜始まり (getDay(): 0=日 → 月曜まで 6 日戻す)。
      const back = (base.getDay() + 6) % 7;
      const monday = shiftDays(base, -back);
      return {
        range: { from: isoLocalDate(monday), to: isoLocalDate(shiftDays(monday, 6)) },
        order: 'asc',
      };
    }
    case 'past':
      // from は付けない = 過去全部を新しい順。limit 200 の窓が直近側に効く。
      return { range: { to: isoLocalDate(shiftDays(base, -1)) }, order: 'desc' };
    case 'all':
    default:
      return { range: undefined, order: 'asc' };
  }
}

/** 今日 (ローカル) の YYYY-MM-DD。行のハイライト判定に使う。 */
export function todayIso(today: Date = new Date()): string {
  return isoLocalDate(today);
}

/** 期間タブ以外の絞り込みが 1 つでも効いているか (件数表示・解除リンクの出し分け)。 */
export function isEventsFiltered(state: EventsFilterState): boolean {
  return !!state.q.trim() || state.hideRegular || state.source !== null || state.trainingOnly;
}

/** フィルタ状態 → `useStaffEvents` のクエリパラメータ。 */
export function toStaffEventFilters(
  state: EventsFilterState,
  order: 'asc' | 'desc',
): StaffEventFilters {
  return {
    q: state.q.trim() || undefined,
    source: state.source,
    type: state.trainingOnly ? 'training' : null,
    order,
    hideRegular: state.hideRegular,
  };
}

interface EventsFilterBarProps {
  value: EventsFilterState;
  onChange: (next: EventsFilterState) => void;
  /** 絞り込み後の件数。 */
  count: number;
  /** 期間タブのみ適用した件数 (「全M件から絞り込み」の M)。 */
  total: number;
}

/** 検索入力のデバウンス (ms)。 */
const SEARCH_DEBOUNCE_MS = 300;

export function EventsFilterBar({ value, onChange, count, total }: EventsFilterBarProps) {
  // 検索欄はタイプ中の反応を優先してローカル state を持ち、300ms 後に親へ流す。
  const [text, setText] = React.useState(value.q);
  const onChangeRef = React.useRef(onChange);
  onChangeRef.current = onChange;
  const valueRef = React.useRef(value);
  valueRef.current = value;

  React.useEffect(() => {
    if (text.trim() === valueRef.current.q.trim()) return;
    const id = window.setTimeout(() => {
      onChangeRef.current({ ...valueRef.current, q: text.trim() });
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [text]);

  const filtered = isEventsFiltered(value);

  const patch = (next: Partial<EventsFilterState>) => onChange({ ...value, ...next });

  const clearAll = () => {
    setText('');
    onChange({ ...DEFAULT_EVENTS_FILTER, tab: value.tab });
  };

  return (
    <div className="mb-3 flex flex-wrap items-center gap-2" data-testid="events-filter-bar">
      {/* 期間タブ */}
      <div
        role="tablist"
        aria-label="期間"
        className="inline-flex overflow-hidden rounded-lg border border-border-default"
      >
        {EVENT_PERIOD_TABS.map((t, i) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={value.tab === t.key}
            onClick={() => patch({ tab: t.key })}
            className={[
              'px-3 py-1 text-[13px]',
              i > 0 ? 'border-l border-border-default' : '',
              value.tab === t.key
                ? 'bg-brand-primary font-bold text-white'
                : 'bg-bg-base text-text-secondary hover:bg-bg-muted',
            ].join(' ')}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* 検索 */}
      <div className="relative min-w-[180px] flex-1">
        <Search
          aria-hidden="true"
          className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted"
        />
        <Input
          type="search"
          aria-label="イベントを検索"
          placeholder="タイトル・備考で検索（患者名・会議名など）"
          className="h-8 pl-8 text-[13px]"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </div>

      {/* 種類チップ */}
      <div className="flex flex-wrap gap-1.5">
        <FilterChip
          active={value.hideRegular}
          onClick={() => patch({ hideRegular: !value.hideRegular })}
          className="px-2.5 py-0.5 text-xs"
        >
          定例を隠す
        </FilterChip>
        <FilterChip
          active={value.source === 'kaipoke'}
          onClick={() => patch({ source: value.source === 'kaipoke' ? null : 'kaipoke' })}
          className="px-2.5 py-0.5 text-xs"
        >
          カイポケ取込
        </FilterChip>
        <FilterChip
          active={value.source === 'manual'}
          onClick={() => patch({ source: value.source === 'manual' ? null : 'manual' })}
          className="px-2.5 py-0.5 text-xs"
        >
          手動
        </FilterChip>
        <FilterChip
          active={value.trainingOnly}
          onClick={() => patch({ trainingOnly: !value.trainingOnly })}
          className="px-2.5 py-0.5 text-xs"
        >
          研修のみ
        </FilterChip>
      </div>

      {/* 件数 + 解除 */}
      <div className="flex w-full items-center gap-2 text-xs text-text-muted">
        <span data-testid="events-count">
          {filtered ? `${count}件を表示中（全${total}件から絞り込み）` : `${count}件`}
        </span>
        {filtered && (
          <button
            type="button"
            onClick={clearAll}
            className="text-brand-primary underline hover:text-brand-primary-hover"
          >
            絞り込みを解除
          </button>
        )}
      </div>
    </div>
  );
}
