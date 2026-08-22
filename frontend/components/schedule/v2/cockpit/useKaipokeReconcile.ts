'use client';

/**
 * useKaipokeReconcile — 🔄突合ロジックの hook 化 (週空間 Phase E・運転席)。
 *
 * 既存 `KaipokeReconcilePanel.tsx` の取得/適用ロジックを**コピーして**抽出したもの
 * (契約書 §4: 既存パネルは触らない)。SyncBar から使う。
 *
 * 取得は RPA 単一スロットのため直列:
 *   ① events-inbound-preview (イベント・1〜2分)
 *   ② smart-inbound-preview  (訪問・約1分)
 *   ③ diff-inbound           (全曜日の取込差分・約1分) — 実績のない日も 1 件ずつ
 *      取り込めるようにするため自動で続ける (失敗しても ①② の結果は残す)。
 * 差分はイベント/訪問をまとめて `diffs` (CockpitDiff) として返す。
 *
 * ⇩取込  … イベント=apply-events-inbound / 訪問=include 排他 + apply-inbound
 * ⇧上書き … 訪問のみ (契約書 §2-5: inbound シートを反転 → 既存 apply)。
 *           イベントの上書きは別経路 (events-outbound) のためここでは扱わない。
 *
 * 失敗の扱い: 例外は握り潰さず `error` + toast に出し、**適用できなかった項目は
 * 適用済みに入れない** (res.failed>0 / outcome==='failed' を見る)。
 */
import * as React from 'react';
import { toast } from 'sonner';

import {
  useApplyEventsInbound,
  useApplyInbound,
  useBulkUpdateItems,
  useCorrectionItems,
  useEventsInboundPreview,
  useKaipokeLive,
  useMasterReconcile,
  useSmartInboundPreview,
  useStartApply,
  useStartDiffInbound,
  useUpdateCorrectionItem,
} from '@/lib/queries/integrations';
import { useReverseSheet } from '@/lib/queries/cockpit';
import type {
  EventsInboundChange,
  EventsInboundPreview,
  MasterReconcile,
  SmartInboundPreview,
} from '@/lib/schemas/integration';
import type { CockpitCorrectionItem } from '@/lib/schemas/v2/cockpit';
import {
  correctionItemToMarker,
  eventChangeToMarker,
  itemField,
  resolveDayInWeek,
  type CockpitMarker,
} from './reconcileMarkers';

export type ReconcilePhase = 'idle' | 'events' | 'visits' | 'ready' | 'error';

/** 突合で出た差分 1 件 (イベント/訪問共通の見た目 + 適用に必要な素データ)。 */
export interface CockpitDiff {
  id: string;
  kind: 'visit' | 'event';
  marker: CockpitMarker;
  change?: EventsInboundChange;
  item?: CockpitCorrectionItem;
}

export interface UseKaipokeReconcileArgs {
  /** 対象週の月曜 (YYYY-MM-DD)。 */
  weekStartIso: string;
  canEdit: boolean;
  /** 氏名 → staff_id (訪問差分の CSV には氏名しか無い)。 */
  staffIdByName?: Map<string, string>;
  /** RPA が空いていれば自動で 1 回だけ突合を開始する (既存パネルと同じ)。 */
  autoStart?: boolean;
  /** 突合完了 (phase='ready') の通知。●未送信の再計算などに使う。 */
  onReady?: () => void;
}

const STALE_MS = 15 * 60_000; // §7-3 鮮度

const msg = (err: unknown): string => (err instanceof Error ? err.message : String(err));

export function useKaipokeReconcile({
  weekStartIso,
  canEdit,
  staffIdByName,
  autoStart = false,
  onReady,
}: UseKaipokeReconcileArgs) {
  const [phase, setPhase] = React.useState<ReconcilePhase>('idle');
  const [error, setError] = React.useState<string | null>(null);
  const [eventsPlan, setEventsPlan] = React.useState<EventsInboundPreview | null>(null);
  const [visitsPlan, setVisitsPlan] = React.useState<SmartInboundPreview | null>(null);
  const [fetchedAt, setFetchedAt] = React.useState<Date | null>(null);
  const [appliedEventIds, setAppliedEventIds] = React.useState<Set<string>>(new Set());
  const [appliedItemIds, setAppliedItemIds] = React.useState<Set<string>>(new Set());
  const [inSheetId, setInSheetId] = React.useState<string | null>(null);
  const [busyKey, setBusyKey] = React.useState<string | null>(null);

  const live = useKaipokeLive();
  const rpaRunning = live.data?.running === true;

  const eventsPreviewMut = useEventsInboundPreview();
  const applyEventsMut = useApplyEventsInbound();
  const smartPreviewMut = useSmartInboundPreview();
  const applyInboundMut = useApplyInbound();
  const updateItemMut = useUpdateCorrectionItem();
  const bulkItemsMut = useBulkUpdateItems();
  const diffInboundMut = useStartDiffInbound();
  const startApplyMut = useStartApply();
  const reverseSheetMut = useReverseSheet();
  const masterReconcileMut = useMasterReconcile();

  const effectiveVisitSheetId = inSheetId ?? visitsPlan?.sheetId ?? null;
  const itemsQuery = useCorrectionItems(effectiveVisitSheetId ?? undefined, { limit: 500 });
  const rawVisitItems = React.useMemo(
    () => (itemsQuery.data?.items ?? []) as CockpitCorrectionItem[],
    [itemsQuery.data],
  );

  const onReadyRef = React.useRef(onReady);
  onReadyRef.current = onReady;

  /** 実績のない日も 1 件ずつ取り込むための inbound シート (既存 #7)。 */
  const fetchInboundDiff = React.useCallback(
    async (silent = false) => {
      setBusyKey('__in_diff__');
      try {
        const res = await diffInboundMut.mutateAsync({
          month: weekStartIso.slice(0, 7),
          weekStart: weekStartIso,
        });
        setInSheetId(res.sheetId);
        setAppliedItemIds(new Set());
        if (!silent) toast.success(`取込差分を計算しました（${res.summary.total ?? 0} 件）`);
        return res.sheetId;
      } catch (err) {
        // 全曜日差分は「あると嬉しい」もの。失敗しても突合結果そのものは残す。
        setError(`取込差分（全曜日）の計算に失敗しました: ${msg(err)}`);
        toast.error(`取込差分の計算に失敗しました: ${msg(err)}`);
        return null;
      } finally {
        setBusyKey(null);
      }
    },
    // mutation は毎レンダー新しい参照になるため依存に入れない (既存パネルと同じ)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [weekStartIso],
  );

  // ─── 取得 (イベント → 訪問 → 全曜日差分の直列) ───
  const runFetch = React.useCallback(async () => {
    setError(null);
    setEventsPlan(null);
    setVisitsPlan(null);
    setAppliedEventIds(new Set());
    setAppliedItemIds(new Set());
    setInSheetId(null);
    try {
      setPhase('events');
      const ev = await eventsPreviewMut.mutateAsync({ weekStart: weekStartIso });
      setEventsPlan(ev);
      setPhase('visits');
      const vp = await smartPreviewMut.mutateAsync({ weekStart: weekStartIso });
      setVisitsPlan(vp);
      setFetchedAt(new Date());
      setPhase('ready');
      // 実績のない日も 1 件ずつ取り込めるよう、全曜日の取込差分まで自動で続ける。
      await fetchInboundDiff(true);
      onReadyRef.current?.();
    } catch (err) {
      setError(msg(err) || '突合の取得に失敗しました');
      toast.error(`突合の取得に失敗しました: ${msg(err)}`);
      setPhase('error');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [weekStartIso, fetchInboundDiff]);

  const autoStartedRef = React.useRef(false);
  React.useEffect(() => {
    if (!autoStart || autoStartedRef.current) return;
    if (!canEdit || phase !== 'idle') return;
    if (live.data === undefined) return; // running 判定がまだ
    if (live.data.running === true) return; // RPA 空き待ち
    autoStartedRef.current = true;
    void runFetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart, canEdit, phase, live.data]);

  // ─── 差分の統合リスト ───
  const remainingChanges = React.useMemo(
    () => (eventsPlan?.changes ?? []).filter((c) => !appliedEventIds.has(c.externalId)),
    [eventsPlan, appliedEventIds],
  );
  const pendingVisitItems = React.useMemo(
    () => rawVisitItems.filter((it) => !appliedItemIds.has(it.id)),
    [rawVisitItems, appliedItemIds],
  );

  const diffs = React.useMemo<CockpitDiff[]>(() => {
    const out: CockpitDiff[] = [];
    for (const c of remainingChanges) {
      out.push({ id: c.externalId, kind: 'event', marker: eventChangeToMarker(c), change: c });
    }
    for (const it of pendingVisitItems) {
      const marker = correctionItemToMarker(it, { weekStartIso, staffIdByName });
      if (marker) out.push({ id: it.id, kind: 'visit', marker, item: it });
    }
    return out;
  }, [remainingChanges, pendingVisitItems, weekStartIso, staffIdByName]);

  // ─── ⇩ 取込 ───
  const applyEventChanges = async (changes: EventsInboundChange[], key: string) => {
    if (changes.length === 0) return;
    setBusyKey(key);
    try {
      const res = await applyEventsMut.mutateAsync({
        weekStart: weekStartIso,
        dryRun: false,
        changes,
      });
      const ok = res.added + res.updated + res.deleted;
      // 失敗した項目は「適用済み」にしない (リストに残して再試行できるように)。
      const failedIds = new Set(
        res.results.filter((r) => r.outcome === 'failed').map((r) => r.externalId),
      );
      const doneIds = changes
        .map((c) => c.externalId)
        .filter((id) => !failedIds.has(id))
        // 旧 BE (results 空) で failed>0 のときは、どれが失敗か分からないので
        // 何も適用済みにせず、次の再突合で判定させる。
        .filter(() => !(res.failed > 0 && res.results.length === 0));
      if (res.failed > 0) {
        const m = `一部の取込に失敗しました（成功 ${ok} / 失敗 ${res.failed}）`;
        setError(m);
        toast.error(m);
      } else {
        setError(null);
        toast.success(
          `イベントを取り込みました（追加${res.added}・更新${res.updated}・削除${res.deleted}）`,
        );
      }
      if (doneIds.length > 0) {
        setAppliedEventIds((prev) => {
          const next = new Set(prev);
          for (const id of doneIds) next.add(id);
          return next;
        });
      }
    } catch (err) {
      setError(`取込に失敗しました: ${msg(err)}`);
      toast.error(`取込に失敗しました: ${msg(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  /** include をこの範囲に絞ってから apply-inbound (既存パネルと同じ排他)。 */
  const applyVisitItems = async (items: CockpitCorrectionItem[], key: string) => {
    const sheetId = effectiveVisitSheetId;
    if (!sheetId || items.length === 0) return;
    const targetIds = items.map((it) => it.id);
    const days = [
      ...new Set(
        items
          .map((it) => it.date_iso ?? resolveDayInWeek(itemField(it, 'date'), weekStartIso))
          .filter((d): d is string => Boolean(d)),
      ),
    ];
    if (days.length === 0) {
      const m = 'この差分の日付を特定できませんでした';
      setError(m);
      toast.error(m);
      return;
    }
    setBusyKey(key);
    try {
      const otherIds = rawVisitItems
        .filter((it) => !targetIds.includes(it.id) && !appliedItemIds.has(it.id))
        .map((it) => it.id);
      if (otherIds.length > 0) {
        await bulkItemsMut.mutateAsync({ sheetId, ids: otherIds, patch: { include: false } });
      }
      if (targetIds.length === 1) {
        await updateItemMut.mutateAsync({ id: targetIds[0]!, patch: { include: true } });
      } else {
        await bulkItemsMut.mutateAsync({ sheetId, ids: targetIds, patch: { include: true } });
      }
      const res = await applyInboundMut.mutateAsync({ sheetId, dryRun: false, days });
      const ok = res.cancelled + res.updated + res.added;
      const failedIds = new Set(
        res.results.filter((r) => r.outcome === 'failed').map((r) => r.itemId),
      );
      const doneIds = targetIds
        .filter((id) => !failedIds.has(id))
        .filter(() => !(res.failed > 0 && res.results.length === 0));
      if (res.failed > 0) {
        const m = `一部の取込に失敗しました（成功 ${ok} / 失敗 ${res.failed}）`;
        setError(m);
        toast.error(m);
      } else {
        setError(null);
        toast.success(`カイポケの差分を取り込みました（${targetIds.length} 件）`);
      }
      if (doneIds.length > 0) {
        setAppliedItemIds((prev) => {
          const next = new Set(prev);
          for (const id of doneIds) next.add(id);
          return next;
        });
      }
    } catch (err) {
      setError(`取込に失敗しました: ${msg(err)}`);
      toast.error(`取込に失敗しました: ${msg(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  /** 差分 1 件を ⇩ 取り込む。 */
  const applyDiff = async (diff: CockpitDiff) => {
    if (diff.kind === 'event' && diff.change) {
      await applyEventChanges([diff.change], diff.id);
      return;
    }
    if (diff.item) await applyVisitItems([diff.item], diff.id);
  };

  /** 差分を全件 ⇩ 取り込む。 */
  const applyAllDiffs = async () => {
    if (remainingChanges.length > 0) await applyEventChanges(remainingChanges, '__all_in__');
    if (pendingVisitItems.length > 0) await applyVisitItems(pendingVisitItems, '__all_in__');
  };

  // ─── ⇧ 上書き (らく助が正・訪問のみ) ───
  const overwriteVisitItems = async (items: CockpitCorrectionItem[], key: string) => {
    const sheetId = effectiveVisitSheetId;
    if (!sheetId || items.length === 0) return;
    const targetIds = items.map((it) => it.id);
    setBusyKey(key);
    try {
      const otherIds = rawVisitItems
        .filter((it) => !targetIds.includes(it.id) && !appliedItemIds.has(it.id))
        .map((it) => it.id);
      if (otherIds.length > 0) {
        await bulkItemsMut.mutateAsync({ sheetId, ids: otherIds, patch: { include: false } });
      }
      await bulkItemsMut.mutateAsync({ sheetId, ids: targetIds, patch: { include: true } });
      const reversed = await reverseSheetMut.mutateAsync({ sheet_id: sheetId });
      await startApplyMut.mutateAsync({ sheetId: reversed.sheet_id, dryRun: false });
      setAppliedItemIds((prev) => {
        const next = new Set(prev);
        for (const id of targetIds) next.add(id);
        return next;
      });
      setError(null);
      toast.success(
        `らく助を正としてカイポケへ上書き送信を始めました（${reversed.item_count} 件・` +
          '1件あたり約30〜60秒）。完了後は再突合をおすすめします',
      );
    } catch (err) {
      setError(`上書き送信に失敗しました: ${msg(err)}`);
      toast.error(`上書き送信に失敗しました: ${msg(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  /** 差分 1 件を ⇧ 上書き送信 (訪問のみ)。 */
  const overwriteDiff = async (diff: CockpitDiff) => {
    if (diff.kind !== 'visit' || !diff.item) return;
    await overwriteVisitItems([diff.item], diff.id);
  };

  /** 訪問差分を全件 ⇧ 上書き送信。 */
  const overwriteAllDiffs = async () => {
    if (pendingVisitItems.length === 0) return;
    await overwriteVisitItems(pendingVisitItems, '__all_over__');
  };

  // ─── 👥 マスタ相互突合 (Phase M・移植元と同じ read-only 診断) ───
  const [masterResult, setMasterResult] = React.useState<MasterReconcile | null>(null);
  const runMasterReconcile = async () => {
    setBusyKey('__master__');
    try {
      const res = await masterReconcileMut.mutateAsync({ month: weekStartIso.slice(0, 7) });
      setMasterResult(res);
      setError(null);
    } catch (err) {
      setError(`マスタ突合に失敗しました: ${msg(err)}`);
      toast.error(`マスタ突合に失敗しました: ${msg(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  const stale = fetchedAt != null && Date.now() - fetchedAt.getTime() > STALE_MS;

  return {
    phase,
    error,
    fetchedAt,
    stale,
    rpaRunning,
    busyKey,
    diffs,
    eventChanges: remainingChanges,
    visitItems: pendingVisitItems,
    visitSheetId: effectiveVisitSheetId,
    /** 全曜日の取込差分シート (null = 実績のある日の差分のみ)。 */
    inSheetId,
    /** 置換対象日の案内に使う (実績のない日)。 */
    visitsPlan,
    masterResult,
    runFetch,
    fetchInboundDiff,
    runMasterReconcile,
    applyDiff,
    applyAllDiffs,
    overwriteDiff,
    overwriteAllDiffs,
  };
}

export type KaipokeReconcile = ReturnType<typeof useKaipokeReconcile>;
