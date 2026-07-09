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

  const weekEnd = useMemo(() => {
    const e = new Date(weekStart);
    e.setDate(e.getDate() + 6);
    return e;
  }, [weekStart]);
  const month = fmtMonth(weekStart);

  const expandStatus = useExpandStatus(month);
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
      toast.error(e instanceof Error ? e.message : '展開の開始に失敗しました');
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

  const runApply = async (dryRun: boolean) => {
    if (!sheetId) return;
    setConfirm(null);
    try {
      await apply.mutateAsync({ sheetId, dryRun });
      toast.success(
        dryRun
          ? 'dry-run を開始しました。ライブモニターで操作を確認できます（書込なし）。'
          : '本番反映を開始しました。ライブモニターで進捗を確認してください。',
      );
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '実行に失敗しました');
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
