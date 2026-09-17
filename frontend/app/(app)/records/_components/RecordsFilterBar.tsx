'use client';

/**
 * `/records` のフィルタ行（モック ⑤）。
 *
 * 期間タブ（今週 / 今月 / 過去 / すべて）＋ 患者・スタッフ・拠点・状態・確認済み
 * ＋ 検索（300ms デバウンス）。`EventsFilterBar` と同じ流儀で、**絞り込みは
 * すべて BE パラメータ**にする — 一覧は 50 件の窓なので FE 側で削ると窓の外が
 * 拾えなくなる。本コンポーネントは状態を親に返すだけでフェッチには関与しない。
 */

import * as React from 'react';
import { Search } from 'lucide-react';

import { Input } from '@/components/ui/input';
import { PatientCombobox } from '@/components/master/PatientCombobox';
import { RECORD_STATUS_OPTIONS } from '@/components/records/recordFormat';
import { useOffices } from '@/lib/queries/offices';
import { useStaffList } from '@/lib/queries/staff';

export type RecordPeriodTab = 'week' | 'month' | 'past' | 'all';

export const RECORD_PERIOD_TABS: ReadonlyArray<{ key: RecordPeriodTab; label: string }> = [
  { key: 'week', label: '今週' },
  { key: 'month', label: '今月' },
  { key: 'past', label: '過去' },
  { key: 'all', label: 'すべて' },
];

/** 確認済みフィルタ。'' = すべて。 */
export type ReviewedFilter = '' | 'yes' | 'no';

export interface RecordsFilterState {
  tab: RecordPeriodTab;
  patientId: string;
  staffId: string;
  officeId: string;
  status: string;
  reviewed: ReviewedFilter;
  /** 患者名・スタッフ名・要約の部分一致。 */
  q: string;
}

export const DEFAULT_RECORDS_FILTER: RecordsFilterState = {
  tab: 'week',
  patientId: '',
  staffId: '',
  officeId: '',
  status: '',
  reviewed: '',
  q: '',
};

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
 * 期間タブ → BE の from/to（`eventPeriodRange` と同型）。
 *
 *   今週   = 月曜 〜 日曜
 *   今月   = 月初 〜 月末
 *   過去   = 〜 昨日
 *   すべて = 指定なし
 *
 * 並び順は常に新しい順（記録は「直近に何があったか」を読む画面）。
 */
export function recordPeriodRange(
  tab: RecordPeriodTab,
  today: Date = new Date(),
): { from?: string; to?: string } {
  const base = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  switch (tab) {
    case 'week': {
      // 月曜始まり（getDay(): 0=日 → 月曜まで 6 日戻す）。
      const back = (base.getDay() + 6) % 7;
      const monday = shiftDays(base, -back);
      return { from: isoLocalDate(monday), to: isoLocalDate(shiftDays(monday, 6)) };
    }
    case 'month': {
      const first = new Date(base.getFullYear(), base.getMonth(), 1);
      const last = new Date(base.getFullYear(), base.getMonth() + 1, 0);
      return { from: isoLocalDate(first), to: isoLocalDate(last) };
    }
    case 'past':
      // from は付けない = 過去全部を新しい順。50 件の窓が直近側に効く。
      return { to: isoLocalDate(shiftDays(base, -1)) };
    case 'all':
    default:
      return {};
  }
}

/** 期間タブ以外の絞り込みが 1 つでも効いているか（解除リンクの出し分け）。 */
export function isRecordsFiltered(state: RecordsFilterState): boolean {
  return (
    !!state.patientId ||
    !!state.staffId ||
    !!state.officeId ||
    !!state.status ||
    state.reviewed !== '' ||
    state.q.trim() !== ''
  );
}

/** 検索入力のデバウンス (ms)。 */
const SEARCH_DEBOUNCE_MS = 300;

/**
 * 検索語の下限 (文字)。1 文字の部分一致は全件に近い結果を BE に作らせるだけで
 * 誰の役にも立たないので送らない（レビュー M-1）。
 */
const SEARCH_MIN_LEN = 2;

/** 検索語の上限 (文字)。貼り付け事故で長大なクエリを飛ばさない。 */
const SEARCH_MAX_LEN = 100;

/** 入力 → BE へ送る検索語（短すぎる間は「指定なし」）。 */
export function normalizeSearchTerm(raw: string): string {
  const trimmed = raw.trim().slice(0, SEARCH_MAX_LEN);
  return trimmed.length >= SEARCH_MIN_LEN ? trimmed : '';
}

const selectCls =
  'h-8 rounded-md border border-border-default bg-bg-base px-2 text-[13px] text-text-primary';

interface RecordsFilterBarProps {
  value: RecordsFilterState;
  onChange: (next: RecordsFilterState) => void;
  /** 絞り込み後の件数（BE の total）。 */
  count: number;
  /**
   * staff ロール（BE が `staff_id` を自分に固定する）か。
   *
   * PO 決定「全ロール同一表示・権限外は disabled」に従い、選んでも効かない
   * スタッフ／拠点セレクトは隠さず無効化して理由を出す（レビュー L-2）。
   */
  staffScoped?: boolean;
}

export function RecordsFilterBar({
  value,
  onChange,
  count,
  staffScoped = false,
}: RecordsFilterBarProps) {
  const { offices } = useOffices({ limit: 100 });
  const staffQuery = useStaffList({ limit: 200 });

  // 検索欄はタイプ中の反応を優先してローカル state を持ち、300ms 後に親へ流す。
  const [text, setText] = React.useState(value.q);
  const onChangeRef = React.useRef(onChange);
  onChangeRef.current = onChange;
  const valueRef = React.useRef(value);
  valueRef.current = value;

  React.useEffect(() => {
    const next = normalizeSearchTerm(text);
    if (next === valueRef.current.q) return;
    const id = window.setTimeout(() => {
      onChangeRef.current({ ...valueRef.current, q: next });
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [text]);

  // 1 文字だけ打った状態は「絞り込めていない」— 黙って全件を出すと誤解を招く。
  const tooShort = text.trim().length > 0 && text.trim().length < SEARCH_MIN_LEN;

  const patch = (next: Partial<RecordsFilterState>) => onChange({ ...value, ...next });

  const clearAll = () => {
    setText('');
    onChange({ ...DEFAULT_RECORDS_FILTER, tab: value.tab });
  };

  return (
    <div className="space-y-2" data-testid="records-filter-bar">
      <div className="flex flex-wrap items-center gap-2">
        {/* 期間タブ */}
        <div
          role="tablist"
          aria-label="期間"
          className="inline-flex overflow-hidden rounded-lg border border-border-default"
        >
          {RECORD_PERIOD_TABS.map((t, i) => (
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
        <div className="relative min-w-[220px] flex-1">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted"
          />
          <Input
            type="search"
            aria-label="訪問記録を検索"
            placeholder="要約・患者名・スタッフ名で検索"
            className="h-8 pl-8 text-[13px]"
            maxLength={SEARCH_MAX_LEN}
            value={text}
            onChange={(e) => setText(e.target.value.slice(0, SEARCH_MAX_LEN))}
          />
        </div>
      </div>

      {tooShort && (
        <p className="text-xs text-text-muted" data-testid="records-search-hint">
          {SEARCH_MIN_LEN} 文字以上で検索できます。
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <PatientCombobox
          value={value.patientId}
          onChange={(id) => patch({ patientId: id })}
          includeInactive
          placeholder="患者: すべて"
          className="h-8 w-56 text-[13px]"
        />

        <select
          aria-label="スタッフ"
          className={`${selectCls} disabled:cursor-not-allowed disabled:opacity-60`}
          value={value.staffId}
          disabled={staffScoped}
          title={staffScoped ? '自分の記録のみ表示されます' : undefined}
          onChange={(e) => patch({ staffId: e.target.value })}
        >
          <option value="">スタッフ: すべて</option>
          {(staffQuery.data ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>

        <select
          aria-label="拠点"
          className={`${selectCls} disabled:cursor-not-allowed disabled:opacity-60`}
          value={value.officeId}
          disabled={staffScoped}
          title={staffScoped ? '自分の記録のみ表示されます' : undefined}
          onChange={(e) => patch({ officeId: e.target.value })}
        >
          <option value="">拠点: すべて</option>
          {offices.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>

        <select
          aria-label="状態"
          className={selectCls}
          value={value.status}
          onChange={(e) => patch({ status: e.target.value })}
        >
          <option value="">状態: すべて</option>
          {RECORD_STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>

        <select
          aria-label="確認済み"
          className={selectCls}
          value={value.reviewed}
          onChange={(e) => patch({ reviewed: e.target.value as ReviewedFilter })}
        >
          <option value="">確認済み: すべて</option>
          <option value="no">未確認</option>
          <option value="yes">確認済み</option>
        </select>

        <div className="flex items-center gap-2 text-xs text-text-muted">
          <span data-testid="records-count">{count}件</span>
          {isRecordsFiltered(value) && (
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
    </div>
  );
}
