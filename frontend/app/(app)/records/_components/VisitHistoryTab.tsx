'use client';

/**
 * `/records` の「打刻履歴」タブ（設計 `visit-history-design-2026-09-30.md` §4）。
 *
 * QR で読み取った到着・退出の時刻を、期間を決めて一覧する。請求時に紙の時間確認表と
 * 照らし合わせるための画面なので、Excel と A4 へそのまま出せる。
 *
 * 絞り込み・並び・集計は**すべて BE**（1 ページ 50 件の窓なので FE で削らない・数えない）。
 * 看護師別・患者別の見出し行の件数も BE の `groups`（絞り込み結果全体での件数）から出す。
 * `groups` の無い応答（古い BE）だけは**このページに出ている行から数え**、ページを
 * またぐ見出しは「このページに N 件」と書き分ける。
 *
 * 実績の時刻の調整（設計 `actual-time-adjust-design-2026-09-30.md` §8-2）: 「調整」
 * バッジ・到着 / 退出欄の下の読取時刻・集計帯「時刻の調整」・絞り込み「時刻の調整あり」。
 * 合わせる操作は詳細ダイアログから。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useSession } from 'next-auth/react';
import { ChevronLeft, ChevronRight, Download, Printer, Search } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import { RakusukeNote } from '@/components/brand/Rakusuke';
import { PatientCombobox } from '@/components/master/PatientCombobox';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { triggerBlobDownload } from '@/lib/api/patientsExcel';
import { jstHm } from '@/lib/format/actualTime';
import { isAdminRole } from '@/lib/rbac';
import { cn } from '@/lib/utils';
import { useOffices } from '@/lib/queries/offices';
import { useStaffList } from '@/lib/queries/staff';
import {
  useVisitHistory,
  useVisitHistoryExport,
  type VisitHistoryFilters,
  type VisitHistoryRow,
  type VisitHistorySort,
  type VisitHistoryStateFilter,
} from '@/lib/queries/visit-history';

import { normalizeSearchTerm } from './RecordsFilterBar';
import { VisitHistoryDetailDialog } from './VisitHistoryDetailDialog';
import { VisitHistoryPrintDialog } from './VisitHistoryPrintDialog';
import {
  HISTORY_PRESETS,
  MAX_RANGE_DAYS,
  formatHistoryDate,
  formatRangeLabel,
  groupRows,
  matchPreset,
  nurseName,
  plannedRange,
  presetRange,
  rangeDays,
  remarkBadges,
  shiftRange,
  type HistoryRange,
  type RemarkTone,
} from './visitHistoryFormat';

/** 1 ページの件数（設計 §4）。 */
const PAGE_SIZE = 50;

/** 検索入力のデバウンス (ms)。音声記録タブと同じ。 */
const SEARCH_DEBOUNCE_MS = 300;

/** 検索語の上限（字）。BE の `q`（`max_length=100`）と同じ。 */
const SEARCH_MAX_LEN = 100;

const STATE_OPTIONS: ReadonlyArray<{ value: VisitHistoryStateFilter; label: string }> = [
  { value: '', label: '打刻: すべて' },
  { value: 'in', label: '打刻あり' },
  { value: 'nodep', label: '退出なし' },
  { value: 'none', label: '打刻なし' },
  { value: 'adjusted', label: '時刻の調整あり' },
  { value: 'special', label: '代行・予定外' },
];

const SORT_OPTIONS: ReadonlyArray<{ value: VisitHistorySort; label: string }> = [
  { value: 'date', label: '日付順' },
  { value: 'staff', label: '看護師別' },
  { value: 'patient', label: '患者別' },
];

interface HistoryFilterState {
  patientId: string;
  staffId: string;
  officeId: string;
  state: VisitHistoryStateFilter;
  q: string;
}

const EMPTY_FILTER: HistoryFilterState = {
  patientId: '',
  staffId: '',
  officeId: '',
  state: '',
  q: '',
};

const selectCls =
  'h-8 rounded-md border border-border-default bg-bg-base px-2 text-[13px] text-text-primary';

const segCls = 'inline-flex overflow-hidden rounded-lg border border-border-default';

function segButtonCls(active: boolean, first: boolean): string {
  return cn(
    'px-3 py-1 text-[13px]',
    !first && 'border-l border-border-default',
    active
      ? 'bg-brand-primary font-bold text-white'
      : 'bg-bg-base text-text-secondary hover:bg-bg-muted',
  );
}

const REMARK_BADGE: Record<
  RemarkTone,
  { variant: 'success' | 'warning' | 'info' | 'secondary'; className?: string }
> = {
  success: { variant: 'success' },
  warning: { variant: 'warning' },
  info: { variant: 'info' },
  muted: { variant: 'secondary', className: 'text-text-secondary' },
  unplanned: { variant: 'secondary', className: 'bg-unplanned-bg text-unplanned' },
  // 調整は注意ではないので、注意色にしない（モックの `.bd.fix`）。
  adjust: {
    variant: 'secondary',
    className: 'border-brand-primary-light bg-brand-primary-50 text-brand-primary-hover',
  },
};

/** 到着 / 退出欄の下に添える小さな 1 行（「読取 13:06」など）。 */
function TimeSub({ children, testId }: { children: string; testId: string }) {
  return (
    <span
      className="block text-xs font-normal leading-tight text-text-secondary"
      data-testid={testId}
    >
      {children}
    </span>
  );
}

function Kpi({
  label,
  value,
  unit,
  tone,
  testId,
}: {
  label: string;
  value: number;
  unit: string;
  tone?: 'ok' | 'warn';
  testId: string;
}) {
  return (
    <div
      className="min-w-[120px] rounded-lg border border-border-default bg-bg-base px-3.5 py-1.5"
      data-testid={testId}
    >
      <div className="text-xs text-text-secondary">{label}</div>
      <div
        className={cn(
          'tnum text-xl font-bold leading-tight',
          tone === 'ok' && 'text-success',
          tone === 'warn' && 'text-warning-strong',
        )}
      >
        {value}
        <span className="ml-1 text-xs font-medium text-text-secondary">{unit}</span>
      </div>
    </div>
  );
}

export function VisitHistoryTab() {
  const { data: session, status: sessionStatus } = useSession();
  // staff は BE が自分の分に固定する。効かないセレクトは隠さず無効化して理由を出す。
  // セッション取得中は判定を保留する（音声記録タブの `staffScoped` と同じ）。
  const staffScoped = sessionStatus === 'loading' ? false : !isAdminRole(session?.user?.role);

  const { offices } = useOffices({ limit: 100 });
  const staffQuery = useStaffList({ limit: 200 });

  const [range, setRange] = useState<HistoryRange>(() => presetRange('month'));
  const [filter, setFilter] = useState<HistoryFilterState>(EMPTY_FILTER);
  const [sort, setSort] = useState<VisitHistorySort>('date');
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<VisitHistoryRow | null>(null);
  const [printOpen, setPrintOpen] = useState(false);

  // 条件が変わったら 1 ページ目へ戻す（窓だけ残って空表示になるのを防ぐ）。
  const changeRange = (next: HistoryRange) => {
    setRange(next);
    setPage(0);
  };
  const patch = (next: Partial<HistoryFilterState>) => {
    setFilter((prev) => ({ ...prev, ...next }));
    setPage(0);
  };

  // 検索欄はタイプ中の反応を優先してローカル state を持ち、300ms 後に絞り込みへ流す。
  const [text, setText] = useState('');
  const appliedQ = useRef(filter.q);
  appliedQ.current = filter.q;
  useEffect(() => {
    const next = normalizeSearchTerm(text);
    if (next === appliedQ.current) return;
    const id = window.setTimeout(() => {
      setFilter((prev) => ({ ...prev, q: next }));
      setPage(0);
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [text]);
  const tooShort = text.trim().length === 1;

  const days = rangeDays(range.from, range.to);
  const tooLong = days > MAX_RANGE_DAYS;

  const filters = useMemo<VisitHistoryFilters>(
    () => ({
      from: range.from,
      to: range.to,
      patientId: filter.patientId || null,
      staffId: filter.staffId || null,
      officeId: filter.officeId || null,
      state: filter.state || null,
      q: filter.q || null,
      sort,
    }),
    [range.from, range.to, filter, sort],
  );

  const query = useVisitHistory({
    ...filters,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
    enabled: !tooLong,
  });
  const exportFile = useVisitHistoryExport();

  const items = useMemo(() => query.data?.items ?? [], [query.data]);
  const total = query.data?.total ?? 0;
  const summary = query.data?.summary;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // 看護師別・患者別は見出し行を挟む。日付順は見出しなしの 1 グループ。
  const groups = useMemo(
    () =>
      groupRows(items, (row) =>
        sort === 'staff'
          ? nurseName(row) || '（担当なし）'
          : sort === 'patient'
            ? (row.patient_name ?? '（患者名なし）')
            : '',
      ),
    [items, sort],
  );
  const hasMore = (page + 1) * PAGE_SIZE < total;
  // 見出し行の件数は BE の `groups`（絞り込み結果全体）から。無い応答は null。
  const groupCounts = useMemo(() => {
    const list = query.data?.groups;
    return list ? new Map(list.map((g) => [g.label, g])) : null;
  }, [query.data]);

  // 開いている詳細は、取り直した一覧の同じ訪問で差し替える（時刻を合わせた直後に
  // 新しい値を見せる）。
  const selectedRow = useMemo(
    () => (selected ? (items.find((r) => r.visit_id === selected.visit_id) ?? selected) : null),
    [items, selected],
  );

  // 詳細から時刻を合わせた / 戻した結果、その行が絞り込みから外れることがある
  // （「時刻の調整あり」で絞って読取時刻に戻した、など）。一覧に居なくなった行は
  // 差し替える先が無く、開いたときの古い時刻を出し続けてしまうので、取り直した一覧に
  // 居なければ詳細を閉じる。`data` は、合わせた時点の一覧（= 取り直す前）。
  const [adjusted, setAdjusted] = useState<{
    visitId: string;
    data: typeof query.data;
  } | null>(null);
  useEffect(() => {
    if (!adjusted) return;
    // 取り直した結果がまだ届いていない。
    if (query.data === adjusted.data || query.isPlaceholderData || !query.data) return;
    setAdjusted(null);
    if (selected?.visit_id !== adjusted.visitId) return;
    if (query.data.items.some((r) => r.visit_id === adjusted.visitId)) return;
    setSelected(null);
    toast.info('絞り込みの条件から外れたため、詳細を閉じました');
  }, [adjusted, query.data, query.isPlaceholderData, selected]);

  const activePreset = matchPreset(range);
  const isFiltered =
    !!filter.patientId || !!filter.staffId || !!filter.officeId || !!filter.state || !!filter.q;

  const clearAll = () => {
    setText('');
    setFilter(EMPTY_FILTER);
    setPage(0);
  };

  const runExport = async () => {
    try {
      const { blob, filename } = await exportFile.mutateAsync(filters);
      triggerBlobDownload(blob, filename);
      toast.success('Excel を出力しました');
    } catch (e) {
      toast.error(`Excel の出力に失敗しました: ${apiErrorMessage(e)}`);
    }
  };

  const rate =
    summary && summary.visits > 0 ? Math.round((summary.with_arrival / summary.visits) * 100) : 0;

  return (
    <>
      {/* コンソール箱（/monitor・音声記録タブと同じ型）。 */}
      <div
        className="flex flex-col overflow-hidden rounded-lg border border-border-default bg-bg-base shadow-outer-card"
        data-testid="history-tab"
      >
        <div className="space-y-2 border-b border-border-default px-5 py-2.5">
          <div className="flex flex-wrap items-center gap-2">
            {/* 期間 */}
            <div role="group" aria-label="期間" className={segCls}>
              {HISTORY_PRESETS.map((p, i) => (
                <button
                  key={p.key}
                  type="button"
                  aria-pressed={activePreset === p.key}
                  onClick={() => changeRange(presetRange(p.key))}
                  className={segButtonCls(activePreset === p.key, i === 0)}
                >
                  {p.label}
                </button>
              ))}
              <button
                type="button"
                aria-pressed={range.unit === 'custom'}
                onClick={() => changeRange({ ...range, unit: 'custom' })}
                className={segButtonCls(range.unit === 'custom', false)}
              >
                期間指定
              </button>
            </div>

            {range.unit === 'custom' ? (
              <div className="flex items-center gap-1.5 text-[13px] text-text-secondary">
                <Input
                  type="date"
                  aria-label="期間の開始日"
                  className="h-8 w-auto px-2 text-[13px]"
                  value={range.from}
                  onChange={(e) => {
                    const from = e.target.value;
                    if (!from) return;
                    changeRange({ unit: 'custom', from, to: range.to < from ? from : range.to });
                  }}
                />
                〜
                <Input
                  type="date"
                  aria-label="期間の終了日"
                  className="h-8 w-auto px-2 text-[13px]"
                  value={range.to}
                  onChange={(e) => {
                    const to = e.target.value;
                    if (!to) return;
                    changeRange({ unit: 'custom', from: range.from > to ? to : range.from, to });
                  }}
                />
              </div>
            ) : (
              <div className="inline-flex items-center gap-1">
                <button
                  type="button"
                  aria-label="前の期間"
                  onClick={() => changeRange(shiftRange(range, -1))}
                  className="flex h-7 w-7 items-center justify-center rounded-md border border-border-default bg-bg-base text-text-secondary hover:bg-bg-muted"
                >
                  <ChevronLeft className="h-4 w-4" aria-hidden="true" />
                </button>
                <span
                  className="tnum min-w-[236px] text-center text-[13px] font-bold text-text-primary"
                  data-testid="history-range-label"
                >
                  {formatRangeLabel(range.from, range.to)}
                </span>
                <button
                  type="button"
                  aria-label="次の期間"
                  onClick={() => changeRange(shiftRange(range, 1))}
                  className="flex h-7 w-7 items-center justify-center rounded-md border border-border-default bg-bg-base text-text-secondary hover:bg-bg-muted"
                >
                  <ChevronRight className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>
            )}

            <span className="flex-1" />

            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void runExport()}
              disabled={tooLong || exportFile.isPending}
              title="いまの絞り込みのまま Excel（.xlsx）で出力します"
              data-testid="history-export-button"
            >
              <Download className="h-4 w-4" aria-hidden="true" />
              {exportFile.isPending ? '出力中…' : 'Excel で出力'}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => setPrintOpen(true)}
              disabled={tooLong}
              data-testid="history-print-button"
            >
              <Printer className="h-4 w-4" aria-hidden="true" />
              A4 で印刷
            </Button>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <PatientCombobox
              value={filter.patientId}
              onChange={(id) => patch({ patientId: id })}
              includeInactive
              placeholder="患者: すべて"
              className="h-8 w-56 text-[13px]"
            />

            <select
              aria-label="スタッフ"
              className={`${selectCls} disabled:cursor-not-allowed disabled:opacity-60`}
              value={filter.staffId}
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
              value={filter.officeId}
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
              aria-label="打刻"
              className={selectCls}
              value={filter.state}
              onChange={(e) => patch({ state: e.target.value as VisitHistoryStateFilter })}
            >
              {STATE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>

            <div className="relative min-w-[220px] flex-1">
              <Search
                aria-hidden="true"
                className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted"
              />
              <Input
                type="search"
                aria-label="打刻履歴を検索"
                placeholder="患者名・看護師名で検索"
                className="h-8 pl-8 text-[13px]"
                // BE の `q` は 100 字まで（超えると 422 で一覧ごと読めなくなる）。
                maxLength={SEARCH_MAX_LEN}
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
            </div>

            {/* 並び */}
            <div role="group" aria-label="並び" className={segCls}>
              {SORT_OPTIONS.map((o, i) => (
                <button
                  key={o.value}
                  type="button"
                  aria-pressed={sort === o.value}
                  onClick={() => {
                    setSort(o.value);
                    setPage(0);
                  }}
                  className={segButtonCls(sort === o.value, i === 0)}
                >
                  {o.label}
                </button>
              ))}
            </div>

            <div className="flex items-center gap-2 text-xs text-text-muted">
              <span data-testid="history-count">{total}件</span>
              {isFiltered && (
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

          {tooShort && (
            <p className="text-xs text-text-muted" data-testid="history-search-hint">
              2 文字以上で検索できます。
            </p>
          )}
        </div>

        {/* 集計帯（ページングする前の絞り込み結果全体・これからの予定は数えない）。 */}
        {summary && !tooLong && (
          <div
            className="flex flex-wrap gap-2 border-b border-border-default bg-bg-app px-5 py-2.5"
            data-testid="history-summary"
          >
            <Kpi
              label="訪問（予定＋予定外）"
              value={summary.visits}
              unit="件"
              testId="history-kpi-visits"
            />
            <Kpi
              label="打刻あり"
              value={summary.with_arrival}
              unit={`件 ・ ${rate}%`}
              tone="ok"
              testId="history-kpi-arrival"
            />
            <Kpi
              label="退出なし"
              value={summary.no_departure}
              unit="件"
              tone={summary.no_departure > 0 ? 'warn' : undefined}
              testId="history-kpi-nodep"
            />
            <Kpi label="打刻なし" value={summary.none} unit="件" testId="history-kpi-none" />
            {summary.adjusted != null && (
              <Kpi
                label="時刻の調整"
                value={summary.adjusted}
                unit="件"
                testId="history-kpi-adjusted"
              />
            )}
          </div>
        )}

        {tooLong ? (
          <div className="p-5" data-testid="history-range-too-long">
            <Alert>
              <AlertTitle>期間が長すぎます</AlertTitle>
              <AlertDescription>
                一度に表示できるのは {MAX_RANGE_DAYS} 日までです（いまは {days}{' '}
                日）。期間を短くしてください。
              </AlertDescription>
            </Alert>
          </div>
        ) : query.isError ? (
          <div className="p-5" data-testid="history-error">
            <Alert variant="destructive">
              <AlertTitle>打刻履歴を読み込めませんでした</AlertTitle>
              <AlertDescription className="whitespace-pre-wrap">
                {apiErrorMessage(query.error)}
              </AlertDescription>
            </Alert>
          </div>
        ) : query.isLoading ? (
          <div className="space-y-2 p-5">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : items.length === 0 ? (
          <div className="p-6" data-testid="history-empty">
            <RakusukeNote pose="think" title="この条件に当てはまる訪問はありません。" />
          </div>
        ) : (
          // 次の結果を待っている間は、前の結果を薄くして出し続ける（消さない）。
          <div
            className={cn('overflow-x-auto', query.isPlaceholderData && 'opacity-60')}
            aria-busy={query.isPlaceholderData || undefined}
          >
            <table className="w-full text-sm" data-testid="history-table">
              <thead>
                <tr className="border-b border-border-default text-left text-xs text-text-muted">
                  <th className="px-4 py-2 font-medium">日付</th>
                  <th className="px-4 py-2 font-medium">患者</th>
                  <th className="px-4 py-2 font-medium">予定</th>
                  <th className="px-4 py-2 font-medium">訪問した看護師</th>
                  <th className="px-4 py-2 font-medium">到着</th>
                  <th className="px-4 py-2 font-medium">退出</th>
                  <th className="px-4 py-2 font-medium">滞在</th>
                  <th className="px-4 py-2 font-medium">備考</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((group, gi) => {
                  const counted = groupCounts?.get(group.title);
                  // BE の件数が無いときだけ表示中の行から数える。ページの端にかかる
                  // 見出しは、前後のページにも行がありうる。
                  const partial =
                    !counted && ((gi === 0 && page > 0) || (gi === groups.length - 1 && hasMore));
                  return [
                    sort !== 'date' && (
                      <tr key={`g-${gi}`} data-testid="history-group-row">
                        <td
                          colSpan={8}
                          className="border-b border-border-default bg-brand-primary-50 px-4 py-1.5 text-[13px] font-bold text-brand-primary-hover"
                          title={
                            partial
                              ? '前後のページにも、この見出しの訪問がある場合があります'
                              : undefined
                          }
                        >
                          {group.title}
                          <span className="ml-2.5 font-medium text-text-secondary">
                            {partial ? 'このページに ' : ''}
                            {counted ? counted.count : group.rows.length} 件 ・ 打刻あり{' '}
                            {counted ? counted.with_arrival : group.arrived} 件
                          </span>
                        </td>
                      </tr>
                    ),
                    ...group.rows.map((row) => {
                      const arrival = jstHm(row.arrival_at);
                      const departure = jstHm(row.departure_at);
                      const arrivalRead = jstHm(row.arrival_read_at);
                      const departureRead = jstHm(row.departure_read_at);
                      const dim = row.state === 'none' || row.state === 'future';
                      return (
                        <tr
                          key={row.visit_id}
                          role="button"
                          tabIndex={0}
                          onClick={() => setSelected(row)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              setSelected(row);
                            }
                          }}
                          className={cn(
                            'cursor-pointer border-b border-border-default hover:bg-bg-muted',
                            dim ? 'text-text-muted' : 'text-text-primary',
                          )}
                          data-testid={`history-row-${row.visit_id}`}
                        >
                          <td className="tnum whitespace-nowrap px-4 py-2">
                            {formatHistoryDate(row.visit_date)}
                          </td>
                          <td
                            className={cn(
                              'whitespace-nowrap px-4 py-2 font-semibold',
                              dim ? 'text-text-secondary' : 'text-text-primary',
                            )}
                          >
                            {row.patient_name ?? '—'}
                          </td>
                          <td className="tnum whitespace-nowrap px-4 py-2">
                            {plannedRange(row) ?? '—'}
                          </td>
                          <td className="whitespace-nowrap px-4 py-2">
                            {row.actual_staff_name ? (
                              <>
                                {row.actual_staff_name}
                                {row.is_substitute && row.planned_staff_name && (
                                  <span className="block text-xs leading-tight text-text-secondary">
                                    予定: {row.planned_staff_name}
                                  </span>
                                )}
                              </>
                            ) : row.planned_staff_name ? (
                              <span className="text-text-muted">
                                （予定: {row.planned_staff_name}）
                              </span>
                            ) : (
                              '—'
                            )}
                          </td>
                          <td
                            className={cn(
                              'tnum whitespace-nowrap px-4 py-2 text-[15px]',
                              arrival ? 'font-bold' : 'text-text-muted',
                            )}
                          >
                            {arrival ?? '—'}
                            {arrival && row.arrival_adjusted && arrivalRead && (
                              <TimeSub testId={`history-arrival-read-${row.visit_id}`}>
                                {`読取 ${arrivalRead}`}
                              </TimeSub>
                            )}
                          </td>
                          <td
                            className={cn(
                              'tnum whitespace-nowrap px-4 py-2 text-[15px]',
                              departure ? 'font-bold' : 'text-text-muted',
                            )}
                          >
                            {departure ?? '—'}
                            {departure && row.departure_manual ? (
                              <TimeSub testId={`history-departure-read-${row.visit_id}`}>
                                手入力
                              </TimeSub>
                            ) : (
                              departure &&
                              row.departure_adjusted &&
                              departureRead && (
                                <TimeSub testId={`history-departure-read-${row.visit_id}`}>
                                  {`読取 ${departureRead}`}
                                </TimeSub>
                              )
                            )}
                          </td>
                          <td className="tnum whitespace-nowrap px-4 py-2">
                            {row.stay_minutes == null ? '—' : `${row.stay_minutes} 分`}
                          </td>
                          <td className="px-4 py-2">
                            <div className="flex flex-wrap gap-1">
                              {remarkBadges(row).map((b) => (
                                <Badge
                                  key={b.full ?? b.label}
                                  variant={REMARK_BADGE[b.tone].variant}
                                  className={cn(
                                    'whitespace-nowrap',
                                    REMARK_BADGE[b.tone].className,
                                  )}
                                  title={b.full}
                                >
                                  {b.label}
                                </Badge>
                              ))}
                            </div>
                          </td>
                        </tr>
                      );
                    }),
                  ];
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* ページング */}
        {!tooLong && total > PAGE_SIZE && (
          <div
            className="flex items-center justify-end gap-3 border-t border-border-default px-5 py-2.5"
            data-testid="history-pager"
          >
            <span className="text-xs text-text-muted">
              {page + 1} / {pageCount} ページ（全{total}件）
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              前へ
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={page + 1 >= pageCount}
              onClick={() => setPage((p) => p + 1)}
            >
              次へ
            </Button>
          </div>
        )}
      </div>

      <VisitHistoryDetailDialog
        row={selectedRow}
        onClose={() => {
          setSelected(null);
          setAdjusted(null);
        }}
        viewerIsAdmin={!staffScoped}
        onAdjusted={(visitId) => setAdjusted({ visitId, data: query.data })}
      />
      {printOpen && (
        <VisitHistoryPrintDialog filters={filters} onClose={() => setPrintOpen(false)} />
      )}
    </>
  );
}
