'use client';

/**
 * useInbound — カイポケ → らく助 取り込み (逆方向同期) の状態・派生値・ハンドラを集約するフック。
 *
 * 2026-07-26 smart-inbound (PO確定・handoff §6-b): 差分/置換のモード選択を廃止し、
 * システムが日単位で自動判別する (打刻あり日=差分・なし日=置換)。作業者の操作は
 * ❶取得 → ❷統合プレビュー確認 → ❸取り込む、の3ステップのみ。
 * イベント (個別業務) は従来どおり週全体を upsert (❶❸に相乗り)。
 */
import { useMemo, useState } from 'react';
import { toast } from 'sonner';

import {
  useApplyEventsInbound,
  useApplySmartInbound,
  useCorrectionItems,
  useEventsInboundPreview,
  useInboundEligibility,
  useSmartInboundPreview,
} from '@/lib/queries/integrations';
import type { EventsInboundPreview, SmartInboundPreview } from '@/lib/schemas/integration';

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

export function fmtWeekLabel(mon: Date): string {
  const sat = new Date(mon);
  sat.setDate(sat.getDate() + 5);
  return `${mon.getMonth() + 1}/${mon.getDate()}（月）〜 ${sat.getMonth() + 1}/${sat.getDate()}（土）`;
}

/** 'YYYY-MM-DD' → 'M/d（曜）' 表示。 */
export function fmtDayLabel(iso: string): string {
  const d = new Date(iso + 'T00:00:00');
  const wd = (d.getDay() + 6) % 7;
  return `${d.getMonth() + 1}/${d.getDate()}（${WEEKDAYS[wd] ?? '?'}）`;
}

/** 週オフセット (0=今週) の相対ラベル。過去は「n週前」。 */
export function fmtRelativeWeek(offset: number): string {
  if (offset === 0) return '今週';
  if (offset === 1) return '来週';
  if (offset === -1) return '先週';
  return `${-offset}週前`;
}

export function field(obj: unknown, key: string): string {
  if (obj && typeof obj === 'object' && key in obj) {
    const v = (obj as Record<string, unknown>)[key];
    return v == null ? '' : String(v);
  }
  return '';
}

// ──────────────────────────── フック ────────────────────────────

/**
 * 未来方向の上限 = 来週まで (PO確定 2026-07-27)。④反映の運用単位が来週分までで、
 * BEゲートも未来週は実apply記録がある週しか開かない — それ以上進めるUIにしても
 * 常に押せないだけなので来週で止める。過去方向は無制限 (カイポケ=請求データで残る)。
 */
export const MAX_FUTURE_WEEKS = 1;

export function useInbound({
  busy,
  credentialsConfigured = true,
}: {
  busy: boolean;
  credentialsConfigured?: boolean;
}) {
  const thisMonday = useMemo(() => mondayOf(new Date()), []);

  // 0=今週・負=過去週 (無制限)・正=未来週 (MAX_FUTURE_WEEKS まで)
  const [weekOffset, setWeekOffset] = useState(0);
  const weekStart = useMemo(() => {
    const m = new Date(thisMonday);
    m.setDate(m.getDate() + weekOffset * 7);
    return m;
  }, [thisMonday, weekOffset]);
  const weekStartStr = fmtDate(weekStart);

  // smart 統合プレビュー (差分シートID・置換計画・日別分類)
  const [smartPlan, setSmartPlan] = useState<SmartInboundPreview | null>(null);
  // イベント (個別業務) — smart と同じ❶❸ボタンに相乗り
  const [eventsPlan, setEventsPlan] = useState<EventsInboundPreview | null>(null);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);

  const currentElig = useInboundEligibility(weekStartStr);
  const eligible = currentElig.data?.eligible ?? false;

  const smartPreview = useSmartInboundPreview();
  const applySmart = useApplySmartInbound();
  const eventsPreview = useEventsInboundPreview();
  const applyEvents = useApplyEventsInbound();

  // 取り込みプレビュータブ (InboundCalendar) 用の差分アイテム
  const sheetId = smartPlan?.sheetId ?? null;
  const itemsQuery = useCorrectionItems(sheetId ?? undefined, { limit: 500 });
  const items = useMemo(() => itemsQuery.data?.items ?? [], [itemsQuery.data?.items]);

  const resetPlan = () => {
    setSmartPlan(null);
    setEventsPlan(null);
    setEventsError(null);
  };

  const canGoNext = weekOffset < MAX_FUTURE_WEEKS;

  const changeWeek = (delta: number) => {
    setWeekOffset((o) => Math.min(MAX_FUTURE_WEEKS, o + delta));
    resetPlan();
  };

  const goToThisWeek = () => {
    setWeekOffset(0);
    resetPlan();
  };

  const runDiff = async () => {
    resetPlan();
    // ❶ 訪問(smart) → イベント を直列で取得 (RPA は単一スロットのため並列不可)。
    // 片方が失敗しても他方は続行し、失敗側は Alert / eventsError で明示する。
    let visitsOk = false;
    try {
      const plan = await smartPreview.mutateAsync({ weekStart: weekStartStr });
      setSmartPlan(plan);
      visitsOk = true;
    } catch {
      // エラーは Alert で表示。イベント取得は続行する。
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

  const runApply = async () => {
    const hasVisitTarget = smartPlan !== null;
    const hasEventTarget = (eventsPlan?.changes.length ?? 0) > 0;
    if (!hasVisitTarget && !hasEventTarget) return;
    setConfirm(false);
    const parts: string[] = [];
    let failed = false;

    // 訪問 (smart: 差分=実績日・置換=クリーン日を単一トランザクションで)
    if (hasVisitTarget) {
      try {
        const res = await applySmart.mutateAsync({
          weekStart: weekStartStr,
          sheetId: smartPlan?.sheetId ?? null,
          dryRun: false,
        });
        if (res.diff) {
          parts.push(
            `実績日(差分): キャンセル ${res.diff.cancelled} / 更新 ${res.diff.updated} / 追加 ${res.diff.added}` +
              (res.diff.failed > 0 ? ` / 失敗 ${res.diff.failed}` : ''),
          );
        }
        if (res.replace) {
          parts.push(
            `置換: 削除 ${res.replace.wiped} / 挿入 ${res.replace.inserted}` +
              (res.replace.skipped.length > 0 ? ` / 対象外 ${res.replace.skipped.length}` : ''),
          );
        }
      } catch (e) {
        failed = true;
        toast.error(e instanceof Error ? e.message : '取り込みに失敗しました');
      }
    }

    // イベント (週丸ごと・プレビューの changes をエコーバック)
    if (hasEventTarget && eventsPlan) {
      try {
        const res = await applyEvents.mutateAsync({
          weekStart: weekStartStr,
          dryRun: false,
          changes: eventsPlan.changes,
        });
        parts.push(
          `イベント: 追加 ${res.added} / 更新 ${res.updated} / 削除 ${res.deleted}` +
            (res.failed > 0 ? ` / 失敗 ${res.failed}` : ''),
        );
      } catch (e) {
        failed = true;
        toast.error(e instanceof Error ? e.message : 'イベントの取り込みに失敗しました');
      }
    }

    if (parts.length > 0) {
      toast.success(`取り込み完了 — ${parts.join(' ｜ ')}`);
    }
    if (!failed) {
      resetPlan();
    }
  };

  const hasEventChanges = (eventsPlan?.changes.length ?? 0) > 0;
  const fetching = smartPreview.isPending || eventsPreview.isPending;
  const applying = applySmart.isPending || applyEvents.isPending;
  const canApply = smartPlan !== null || hasEventChanges;

  return {
    busy,
    credentialsConfigured,
    weekOffset,
    weekStart,
    currentElig,
    eligible,
    canGoNext,
    changeWeek,
    goToThisWeek,
    // smart 取り込み
    smartPreview,
    applySmart,
    smartPlan,
    sheetId,
    itemsQuery,
    items,
    // イベント (個別業務)
    eventsPreview,
    applyEvents,
    eventsPlan,
    eventsError,
    hasEventChanges,
    // 操作
    confirm,
    setConfirm,
    runDiff,
    runApply,
    fetching,
    applying,
    canApply,
  };
}

export type InboundVm = ReturnType<typeof useInbound>;
