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
  useApplyEventsInbound,
  useApplyInbound,
  useCorrectionItems,
  useEventsInboundPreview,
  useInboundEligibility,
  useReplaceInbound,
  useStartDiffInbound,
} from '@/lib/queries/integrations';
import type {
  ApplyInboundResult,
  EventsInboundApplyResult,
  EventsInboundPreview,
  ReplaceInboundResult,
} from '@/lib/schemas/integration';

// ──────────────────────────── 定数 ────────────────────────────

export const WEEKDAYS = ['月', '火', '水', '木', '金', '土'] as const;

/**
 * 大量キャンセル警告のしきい値 (2026-07-26 ゲート改訂とセットの安全弁)。
 * キャンセル候補がこの件数以上 = 「カイポケにその週が入力されていない」疑いが濃厚
 * (取り込むとらく助の予定が大量に消える)。警告を出し、曜日の自動選択も止める。
 */
export const MASS_CANCEL_THRESHOLD = 10;

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

/**
 * 取り込みモード (2026-07-26 PO確定):
 *  - diff    = 差分取り込み。訪問の行を残したまま中身を直す (打刻等の実績を守る)。
 *              実績が付いた週の毎週の追いかけはこちら。
 *  - replace = 置換取り込み。週を白紙化してカイポケの内容で丸ごと書き直す。
 *              一度も同期していない週の初回整列・未打刻週のズレ一括解消用。
 *              実績のある週は BE 側ガードで 422 になる。
 */
export type InboundMode = 'diff' | 'replace';

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
  // イベント (個別業務) 取り込み — 訪問と同じ❶❸ボタンで直列実行する (1ボタン統合)。
  const [eventsPlan, setEventsPlan] = useState<EventsInboundPreview | null>(null);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [eventsDryRunResult, setEventsDryRunResult] = useState<EventsInboundApplyResult | null>(
    null,
  );
  // 取り込みモード (diff=差分 / replace=置換)。既定は安全側の差分。
  const [mode, setModeState] = useState<InboundMode>('diff');
  const [replacePlan, setReplacePlan] = useState<ReplaceInboundResult | null>(null);

  const thisElig = useInboundEligibility(fmtDate(thisMonday));
  const nextElig = useInboundEligibility(fmtDate(nextMonday));
  const currentElig = selectedWeek === 'this' ? thisElig : nextElig;
  const eligible = currentElig.data?.eligible ?? false;

  const diffInbound = useStartDiffInbound();
  const applyInbound = useApplyInbound();
  const eventsPreview = useEventsInboundPreview();
  const applyEvents = useApplyEventsInbound();
  const replaceInbound = useReplaceInbound();
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

  // 大量キャンセル警告: キャンセル候補が閾値以上なら人の確認を強制する
  // (曜日の自動選択もしない)。カイポケ未入力週の誤取り込み = 週全滅事故の安全弁。
  const massCancelWarning = (summary?.delete ?? 0) >= MASS_CANCEL_THRESHOLD;

  // 差分シートが切り替わったら自動選択フラグをリセット。
  useEffect(() => {
    autoSelectedRef.current = false;
  }, [sheetId]);

  // アイテム読み込み完了後、差分がある曜日をすべて自動選択。
  // ただし大量キャンセル警告中は自動選択しない (人が曜日を選ぶまで進めない)。
  useEffect(() => {
    if (sheetId && !autoSelectedRef.current && daysWithDiff.size > 0 && !massCancelWarning) {
      autoSelectedRef.current = true;
      setSelectedDays(new Set(daysWithDiff));
    }
  }, [sheetId, daysWithDiff, massCancelWarning]);

  const resetDiff = () => {
    setSheetId(null);
    setSummary(null);
    setSelectedDays(new Set());
    setDryRunResult(null);
    setEventsPlan(null);
    setEventsError(null);
    setEventsDryRunResult(null);
    setReplacePlan(null);
    autoSelectedRef.current = false;
  };

  const setMode = (next: InboundMode) => {
    setModeState(next);
    resetDiff();
  };

  const handleWeekChange = (week: WeekOption) => {
    setSelectedWeek(week);
    resetDiff();
  };

  const runDiff = async () => {
    resetDiff();
    // ❶ 訪問 → イベント を直列で取得 (RPA は単一スロットのため並列不可)。
    // 片方が失敗しても他方は続行し、失敗側は Alert / eventsError で明示する。
    let visitsOk = false;
    if (mode === 'replace') {
      // 置換モード: dry-run で「白紙化 n件 / 挿入 n件 / 対象外」の計画を出す。
      try {
        const plan = await replaceInbound.mutateAsync({ weekStart: weekStartStr, dryRun: true });
        setReplacePlan(plan);
        visitsOk = true;
      } catch {
        // エラーは Alert で表示。イベント取得は続行する。
      }
    } else {
      try {
        const res = await diffInbound.mutateAsync({ month, weekStart: weekStartStr });
        setSheetId(res.sheetId);
        setSummary(res.summary as Record<string, number>);
        visitsOk = true;
      } catch {
        // エラーは Alert で表示。イベント取得は続行する。
      }
    }
    try {
      const plan = await eventsPreview.mutateAsync({ weekStart: weekStartStr });
      setEventsPlan(plan);
      setEventsError(null);
    } catch (e) {
      setEventsError(e instanceof Error ? e.message : 'イベント取得に失敗しました');
      if (visitsOk) {
        toast.warning('イベント（個別業務）の取得に失敗しました。訪問の差分のみ表示しています。');
      }
    }
  };

  const runApply = async (dryRun: boolean) => {
    const hasVisitTarget =
      mode === 'replace' ? replacePlan !== null : sheetId !== null && selectedDays.size > 0;
    const hasEventTarget = (eventsPlan?.changes.length ?? 0) > 0;
    if (!hasVisitTarget && !hasEventTarget) return;
    setConfirm(false);
    const parts: string[] = [];
    let failed = false;

    // 置換モード: 週を白紙化してカイポケで書き直す (❶が dry-run なので実適用のみ)
    if (mode === 'replace' && replacePlan !== null && !dryRun) {
      try {
        const res = await replaceInbound.mutateAsync({ weekStart: weekStartStr, dryRun: false });
        parts.push(
          `訪問(置換): 削除 ${res.wiped} / 挿入 ${res.inserted}` +
            (res.skipped.length > 0 ? ` / 対象外 ${res.skipped.length}` : ''),
        );
      } catch (e) {
        failed = true;
        toast.error(e instanceof Error ? e.message : '置換取り込みに失敗しました');
      }
    }

    // 訪問 (差分フロー・曜日チップ選択に従う)
    if (mode === 'diff' && hasVisitTarget && sheetId) {
      const days = Array.from(selectedDays);
      try {
        const res = await applyInbound.mutateAsync({ sheetId, dryRun, days });
        if (dryRun) {
          setDryRunResult(res);
        } else {
          parts.push(
            `訪問: キャンセル ${res.cancelled} / 更新 ${res.updated} / 追加 ${res.added} / スキップ ${res.skipped}`,
          );
        }
      } catch (e) {
        failed = true;
        toast.error(e instanceof Error ? e.message : '訪問の取り込みに失敗しました');
      }
    }

    // イベント (週丸ごと・プレビューの changes をエコーバック)
    if (hasEventTarget && eventsPlan) {
      try {
        const res = await applyEvents.mutateAsync({
          weekStart: weekStartStr,
          dryRun,
          changes: eventsPlan.changes,
        });
        if (dryRun) {
          setEventsDryRunResult(res);
        } else {
          parts.push(
            `イベント: 追加 ${res.added} / 更新 ${res.updated} / 削除 ${res.deleted}` +
              (res.failed > 0 ? ` / 失敗 ${res.failed}` : ''),
          );
        }
      } catch (e) {
        failed = true;
        toast.error(e instanceof Error ? e.message : 'イベントの取り込みに失敗しました');
      }
    }

    if (!dryRun) {
      if (parts.length > 0) {
        toast.success(`取り込み完了 — ${parts.join(' ｜ ')}`);
      }
      if (!failed) {
        resetDiff();
      }
    }
  };

  const hasSelectedDays = selectedDays.size > 0;
  const selectedDayLabels = weekDays
    .filter((d) => selectedDays.has(d))
    .map((d, i) => WEEKDAYS[weekDays.indexOf(d)] ?? WEEKDAYS[i])
    .join('・');
  const hasEventChanges = (eventsPlan?.changes.length ?? 0) > 0;
  const fetching = diffInbound.isPending || eventsPreview.isPending || replaceInbound.isPending;

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
    massCancelWarning,
    // 取り込みモード (diff / replace)
    mode,
    setMode,
    replaceInbound,
    replacePlan,
    // イベント (個別業務) 取り込み
    eventsPreview,
    applyEvents,
    eventsPlan,
    eventsError,
    eventsDryRunResult,
    hasEventChanges,
    fetching,
  };
}

export type InboundVm = ReturnType<typeof useInbound>;
