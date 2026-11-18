'use client';

/**
 * useWeeklyApply — 週次反映ワークフロー (送る側) の状態・派生値・ハンドラを集約するフック。
 *
 * 旧 WeeklyApplyPanel の中身を「操作部 (WeeklyApplyControls)」と
 * 「カレンダー部 (WeeklyApplyCalendar)」の 2 箇所に描き分けるため、ロジックは移動のみ
 * (中身は書き換えない) でここへ抽出した。
 */
import { useMemo, useState } from 'react';
import { toast } from 'sonner';

import { ApiError } from '@/lib/api-client';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import type { CorrectionItem } from '@/lib/schemas/integration';
import {
  useCorrectionItems,
  useExpandStatus,
  useStartApply,
  useStartDiffLocal,
  useStartExpand,
  useWeekSchedule,
} from '@/lib/queries/integrations';

const WEEKDAY = ['日', '月', '火', '水', '木', '金', '土'];

function mondayOf(d: Date): Date {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const day = x.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  x.setDate(x.getDate() + diff);
  return x;
}
function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function fmtMonth(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}
/**
 * 差分の行の「日」(1-31) を週内の実日付へ解決する (BE の resolve_item_date と同じ規則)。
 * delete/edit/date_change は before、add は after の日を見る。週内で一意に決まらなければ null。
 */
export function resolveItemDate(
  item: Pick<CorrectionItem, 'action' | 'before' | 'after'>,
  weekStart: Date,
  side: 'auto' | 'after' = 'auto',
): Date | null {
  const useBefore = side === 'auto' && ['delete', 'edit', 'date_change'].includes(item.action);
  const src = useBefore ? item.before : item.after;
  const raw = String(src?.date ?? '').trim();
  if (!/^\d+$/.test(raw)) return null;
  const day = Number(raw);
  const matches: Date[] = [];
  for (let i = 0; i < 7; i += 1) {
    const d = new Date(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() + i);
    if (d.getDate() === day) matches.push(d);
  }
  return matches.length === 1 ? (matches[0] ?? null) : null;
}

/** 月をまたぐ週の、月ごとの送信の単位。 */
export interface ApplyMonthGroup {
  /** "YYYY-MM" */
  month: string;
  /** 例「11 月分（11/30）」「12 月分（12/1〜12/6）」 */
  label: string;
  /** まだ送っていない、送れる行の数 */
  pending: number;
  /** まだ送っていないが、送れない行の数 (過去日・月の境をまたぐ日の移動) */
  blocked: number;
  /** 送信済みの行の数 */
  sent: number;
  /** 月の境をまたぐ日の移動 (送らずにカイポケで手で動かす) の数 */
  boundaryMoves: number;
}

/** 週の 7 日を月ごとに分け、差分の行を数える。週が 1 つの月なら 1 要素。 */
export function buildMonthGroups(
  items: CorrectionItem[],
  weekStart: Date,
  today: Date = new Date(),
): ApplyMonthGroup[] {
  // BE の過去日ガードと同じく、今日以前の行は送らない。
  const todayKey = fmtDate(today);
  const days: Date[] = [];
  for (let i = 0; i < 7; i += 1) {
    days.push(new Date(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() + i));
  }
  const months = Array.from(new Set(days.map(fmtMonth)));
  return months.map((m) => {
    const inMonth = days.filter((d) => fmtMonth(d) === m);
    // 週の日から作った月なので、その月の日は必ず 1 日以上ある。
    const first = inMonth[0] as Date;
    const last = inMonth[inMonth.length - 1] as Date;
    const range =
      inMonth.length === 1
        ? `${first.getMonth() + 1}/${first.getDate()}`
        : `${first.getMonth() + 1}/${first.getDate()}〜${last.getMonth() + 1}/${last.getDate()}`;
    let pending = 0;
    let blocked = 0;
    let sent = 0;
    let boundaryMoves = 0;
    for (const it of items) {
      const d = resolveItemDate(it, weekStart);
      if (d === null || fmtMonth(d) !== m) continue;
      if (!it.include) {
        sent += 1;
        continue;
      }
      let crosses = false;
      if (it.action === 'date_change') {
        const a = resolveItemDate(it, weekStart, 'after');
        crosses = a !== null && fmtMonth(a) !== m;
        if (crosses) boundaryMoves += 1;
      }
      if (crosses || fmtDate(d) <= todayKey) blocked += 1;
      else pending += 1;
    }
    return {
      month: m,
      label: `${first.getMonth() + 1} 月分（${range}）`,
      pending,
      blocked,
      sent,
      boundaryMoves,
    };
  });
}

export function nextWeekMonday(): Date {
  const m = mondayOf(new Date());
  m.setDate(m.getDate() + 7);
  return m;
}

export function useWeeklyApply({
  busy,
  credentialsConfigured = true,
}: {
  busy: boolean;
  credentialsConfigured?: boolean;
}) {
  const [weekStart, setWeekStart] = useState<Date>(() => nextWeekMonday());
  const [sheetId, setSheetId] = useState<string | null>(null);
  const [summary, setSummary] = useState<Record<string, number> | null>(null);
  const [confirm, setConfirm] = useState<null | 'dry' | 'real' | 'expand' | 'reexpand'>(null);
  const [showDiffDetail, setShowDiffDetail] = useState(false);
  // 月をまたぐ週で、確認ダイアログが対象にしている月 ("YYYY-MM")。null = 週全体。
  const [confirmMonth, setConfirmMonth] = useState<string | null>(null);

  const weekEnd = useMemo(() => {
    const e = new Date(weekStart);
    e.setDate(e.getDate() + 6);
    return e;
  }, [weekStart]);
  const month = fmtMonth(weekStart);
  const endMonth = fmtMonth(weekEnd);

  const expandStatus = useExpandStatus(month);
  // 月をまたぐ週では、2 つ目の月の展開も確かめる (同じ月なら同じ問い合わせ)。
  const endExpandStatus = useExpandStatus(endMonth);
  const expand = useStartExpand();
  const diffLocal = useStartDiffLocal();
  const apply = useStartApply();
  const schedule = useWeekSchedule(fmtDate(weekStart), fmtDate(weekEnd));
  const itemsQuery = useCorrectionItems(sheetId ?? undefined, { limit: 500 });

  const scheduleRows = schedule.data?.rows ?? [];
  const items = itemsQuery.data?.items ?? [];
  const total = summary?.total ?? 0;
  const unresolved = summary?.unresolved_patient ?? 0;
  const isExpanded = expandStatus.data?.expanded ?? false;
  const spansMonths = endMonth !== month;
  const itemsData = itemsQuery.data?.items;
  const monthGroups = useMemo(
    () => buildMonthGroups(itemsData ?? [], weekStart),
    [itemsData, weekStart],
  );
  /** その月の展開が済んでいるか (分からないときは true = 警告を出さない)。 */
  const monthExpanded = (m: string): boolean =>
    m === month
      ? (expandStatus.data?.expanded ?? true)
      : m === endMonth
        ? (endExpandStatus.data?.expanded ?? true)
        : true;

  const label = `${weekStart.getMonth() + 1}/${weekStart.getDate()}（${WEEKDAY[weekStart.getDay()]}）〜 ${weekEnd.getMonth() + 1}/${weekEnd.getDate()}（${WEEKDAY[weekEnd.getDay()]}）`;

  const resetDiff = () => {
    setSheetId(null);
    setSummary(null);
    setShowDiffDetail(false);
  };
  const shiftWeek = (delta: number) => {
    const n = new Date(weekStart);
    n.setDate(n.getDate() + delta * 7);
    setWeekStart(n);
    resetDiff();
  };

  const runExpand = async () => {
    setConfirm(null);
    try {
      await expand.mutateAsync({ month });
      toast.success(
        'スケジュール展開を開始しました。ライブモニターで進捗を確認してください（約15〜20分）。',
      );
      void expandStatus.refetch();
    } catch (e) {
      toast.error(apiErrorMessage(e, '展開の開始に失敗しました'));
    }
  };

  const runDiff = async () => {
    resetDiff();
    try {
      const res = await diffLocal.mutateAsync({
        month,
        weekStart: fmtDate(weekStart),
        weekEnd: fmtDate(weekEnd),
      });
      setSheetId(res.sheetId);
      setSummary(res.summary ?? {});
    } catch {
      // 下の Alert で表示。
    }
  };

  const runApply = async (dryRun: boolean, applyMonth: string | null = null) => {
    if (!sheetId) return;
    setConfirm(null);
    setConfirmMonth(null);
    const target = applyMonth ? monthGroups.find((g) => g.month === applyMonth) : undefined;
    const prefix = target ? `${target.label}の` : '';
    try {
      await apply.mutateAsync(
        applyMonth ? { sheetId, dryRun, month: applyMonth } : { sheetId, dryRun },
      );
      toast.success(
        dryRun
          ? `${prefix}dry-run を開始しました。ライブモニターで操作を確認できます（書込なし）。`
          : `${prefix}本番反映を開始しました。ライブモニターで進捗を確認してください。`,
      );
      // 送った行は送信済み (include=false) になる。月ごとのボタンの件数を読み直す。
      if (!dryRun) void itemsQuery.refetch();
    } catch (e) {
      const detail =
        e instanceof ApiError && typeof (e.body as { detail?: unknown } | null)?.detail === 'string'
          ? (e.body as { detail: string }).detail
          : '';
      if (
        e instanceof ApiError &&
        e.status === 409 &&
        (detail === 'kaipoke busy' ||
          detail === 'apply already in progress' ||
          /running|実行中/.test(detail))
      ) {
        toast.error(
          'カイポケで前の送信（または読み込み）がまだ動いています。ライブモニターで終わったのを確かめてから、もう一度押してください。',
        );
      } else {
        toast.error(apiErrorMessage(e, '実行に失敗しました'));
      }
    }
  };

  // 差分が「追加ばかり」= カイポケがこの週空 → 展開忘れの可能性を警告。
  const looksUnexpanded =
    sheetId != null && total > 0 && (summary?.add ?? 0) === total && !isExpanded;

  return {
    busy,
    credentialsConfigured,
    weekStart,
    weekEnd,
    month,
    label,
    sheetId,
    summary,
    confirm,
    setConfirm,
    showDiffDetail,
    setShowDiffDetail,
    confirmMonth,
    setConfirmMonth,
    spansMonths,
    monthGroups,
    monthExpanded,
    setWeekStart,
    expandStatus,
    expand,
    diffLocal,
    apply,
    schedule,
    itemsQuery,
    scheduleRows,
    items,
    total,
    unresolved,
    isExpanded,
    looksUnexpanded,
    resetDiff,
    shiftWeek,
    runExpand,
    runDiff,
    runApply,
  };
}

export type WeeklyApplyVm = ReturnType<typeof useWeeklyApply>;
