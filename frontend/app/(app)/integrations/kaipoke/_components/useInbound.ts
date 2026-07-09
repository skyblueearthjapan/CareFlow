'use client';

/**
 * useInbound — カイポケ → CareFlow 取り込み (逆方向同期) の状態・派生値・ハンドラを集約するフック。
 *
 * 旧 InboundPanel の中身を「操作部 (InboundControls)」と「カレンダー部 (InboundCalendar)」に
 * 描き分けるため、ロジックは移動のみ (中身は書き換えない) でここへ抽出した。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

import {
  useApplyInbound,
  useCorrectionItems,
  useInboundEligibility,
  useStartDiffInbound,
} from '@/lib/queries/integrations';
import type { ApplyInboundResult } from '@/lib/schemas/integration';

// ──────────────────────────── 定数 ────────────────────────────

export const WEEKDAYS = ['月', '火', '水', '木', '金', '土'] as const;

// ──────────────────────────── ユーティリティ ────────────────────────────

function mondayOf(d: Date): Date {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const day = x.getDay();
  x.setDate(x.getDate() + (day === 0 ? -6 : 1 - day));
  return x;
}

function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function fmtMonth(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

export function fmtWeekLabel(mon: Date): string {
  const sat = new Date(mon);
  sat.setDate(sat.getDate() + 5);
  return `${mon.getMonth() + 1}/${mon.getDate()}（月）〜 ${sat.getMonth() + 1}/${sat.getDate()}（土）`;
}

export function field(obj: unknown, key: string): string {
  if (obj && typeof obj === 'object' && key in obj) {
    const v = (obj as Record<string, unknown>)[key];
    return v == null ? '' : String(v);
  }
  return '';
}

// ──────────────────────────── フック ────────────────────────────

type WeekOption = 'this' | 'next';

export function useInbound({
  busy,
  credentialsConfigured = true,
}: {
  busy: boolean;
  credentialsConfigured?: boolean;
}) {
  const thisMonday = useMemo(() => mondayOf(new Date()), []);
  const nextMonday = useMemo(() => {
    const m = mondayOf(new Date());
    m.setDate(m.getDate() + 7);
    return m;
  }, []);

  const [selectedWeek, setSelectedWeek] = useState<WeekOption>('this');
  const weekStart = selectedWeek === 'this' ? thisMonday : nextMonday;
  const weekStartStr = fmtDate(weekStart);
  const month = fmtMonth(weekStart);

  const [sheetId, setSheetId] = useState<string | null>(null);
  const [summary, setSummary] = useState<Record<string, number> | null>(null);
  const [selectedDays, setSelectedDays] = useState<Set<string>>(new Set());
  const [dryRunResult, setDryRunResult] = useState<ApplyInboundResult | null>(null);
  const [confirm, setConfirm] = useState(false);
  const autoSelectedRef = useRef(false);

  const thisElig = useInboundEligibility(fmtDate(thisMonday));
  const nextElig = useInboundEligibility(fmtDate(nextMonday));
  const currentElig = selectedWeek === 'this' ? thisElig : nextElig;
  const eligible = currentElig.data?.eligible ?? false;

  const diffInbound = useStartDiffInbound();
  const applyInbound = useApplyInbound();
  const itemsQuery = useCorrectionItems(sheetId ?? undefined, { limit: 500 });
  const items = useMemo(() => itemsQuery.data?.items ?? [], [itemsQuery.data?.items]);

  // 月〜土の YYYY-MM-DD リスト。
  const weekDays = useMemo(
    () =>
      Array.from({ length: 6 }, (_, i) => {
        const d = new Date(weekStart);
        d.setDate(d.getDate() + i);
        return fmtDate(d);
      }),
    [weekStart],
  );

  // 差分アイテムが存在する曜日の日付セット。
  const daysWithDiff = useMemo(() => {
    const set = new Set<string>();
    for (const item of items) {
      const dayStr = field(item.after, 'date') || field(item.before, 'date');
      const dayNum = Number.parseInt(dayStr, 10);
      if (!Number.isFinite(dayNum)) continue;
      for (const dateStr of weekDays) {
        if (new Date(dateStr).getDate() === dayNum) {
          set.add(dateStr);
          break;
        }
      }
    }
    return set;
  }, [items, weekDays]);

  // 差分シートが切り替わったら自動選択フラグをリセット。
  useEffect(() => {
    autoSelectedRef.current = false;
  }, [sheetId]);

  // アイテム読み込み完了後、差分がある曜日をすべて自動選択。
  useEffect(() => {
    if (sheetId && !autoSelectedRef.current && daysWithDiff.size > 0) {
      autoSelectedRef.current = true;
      setSelectedDays(new Set(daysWithDiff));
    }
  }, [sheetId, daysWithDiff]);

  const resetDiff = () => {
    setSheetId(null);
    setSummary(null);
    setSelectedDays(new Set());
    setDryRunResult(null);
    autoSelectedRef.current = false;
  };

  const handleWeekChange = (week: WeekOption) => {
    setSelectedWeek(week);
    resetDiff();
  };

  const runDiff = async () => {
    resetDiff();
    try {
      const res = await diffInbound.mutateAsync({ month, weekStart: weekStartStr });
      setSheetId(res.sheetId);
      setSummary(res.summary as Record<string, number>);
    } catch {
      // エラーは Alert で表示。
    }
  };

  const runApply = async (dryRun: boolean) => {
    if (!sheetId) return;
    setConfirm(false);
    const days = Array.from(selectedDays);
    try {
      const res = await applyInbound.mutateAsync({ sheetId, dryRun, days });
      if (dryRun) {
        setDryRunResult(res);
      } else {
        toast.success(
          `取り込み完了: キャンセル ${res.cancelled}件 / 更新 ${res.updated}件 / 追加 ${res.added}件 / スキップ ${res.skipped}件`,
        );
        resetDiff();
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '実行に失敗しました');
    }
  };

  const hasSelectedDays = selectedDays.size > 0;
  const selectedDayLabels = weekDays
    .filter((d) => selectedDays.has(d))
    .map((d, i) => WEEKDAYS[weekDays.indexOf(d)] ?? WEEKDAYS[i])
    .join('・');

  return {
    busy,
    credentialsConfigured,
    thisMonday,
    nextMonday,
    selectedWeek,
    weekStart,
    thisElig,
    nextElig,
    currentElig,
    eligible,
    diffInbound,
    applyInbound,
    itemsQuery,
    items,
    weekDays,
    daysWithDiff,
    sheetId,
    summary,
    selectedDays,
    setSelectedDays,
    dryRunResult,
    setDryRunResult,
    confirm,
    setConfirm,
    handleWeekChange,
    runDiff,
    runApply,
    hasSelectedDays,
    selectedDayLabels,
  };
}

export type InboundVm = ReturnType<typeof useInbound>;
