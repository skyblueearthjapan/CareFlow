'use client';

/**
 * KaipokeReconcilePanel — カイポケ突合ビュー (週空間 C1・weekly-space-design.md §7-3)。
 *
 * 「取込か送信かを先に選ばせず、まず突き合わせて差分を見せ、差分ごとに
 * 方向を選ぶ」の第一段 (C1 = ⇩取込方向)。職員スケジュールタブに常駐し、
 * [🔄 カイポケと突合] でカイポケ側の当週データを取得して差分を一覧する。
 *
 * データ源 (既存資産のみ・新BEなし):
 *  - イベント: events-inbound-preview (start→statusポーリング・部分適用YES)
 *  - 訪問:     smart-inbound-preview (同期 ~45-90s) + correction-items
 *              打刻あり日 = 差分項目を include フラグ経由で 1 件ずつ適用可
 *              打刻なし日 = 丸ごと置換対象 (項目選択の口が無い) → 案内表示
 *  - RPA 単一スロット: useKaipokeLive の running で起動をガード
 *    (取得はイベント→訪問の直列実行)
 *
 * 盤面へのゴースト表示 (🟣カイポケのみ/🟡変更/🔵らく助のみ) は
 * onEventMarkersChange で親 (CourseDayTablePanel) に渡し、StaffWeekBoard が描く。
 */
import * as React from 'react';
import { addDays, format, parseISO } from 'date-fns';
import { Loader2, RefreshCw, X } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
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
  useStartDiffLocal,
  useUpdateCorrectionItem,
} from '@/lib/queries/integrations';
import type { MasterReconcile } from '@/lib/schemas/integration';
import type {
  CorrectionItem,
  EventsInboundChange,
  EventsInboundPreview,
  SmartInboundPreview,
} from '@/lib/schemas/integration';
import type { StaffRead } from '@/lib/schemas/staff';

/** 盤面セルに描くゴーストマーカー (`${staffId}:${weekday}` で引く)。 */
export interface ReconcileMarker {
  /** add=🟣カイポケのみ / update=🟡変更あり / delete=🔵らく助のみ(カイポケ側に無し) */
  action: 'add' | 'update' | 'delete';
  externalId: string;
  title: string;
  start: string;
  end: string;
  beforeStart?: string | null;
  beforeEnd?: string | null;
}

export type ReconcileMarkersByCell = Map<string, ReconcileMarker[]>;

const WD_JA = '月火水木金土日';

function weekdayOfIso(dateIso: string): number {
  return (parseISO(dateIso).getDay() + 6) % 7;
}

function fmtMd(dateIso: string): string {
  const d = parseISO(dateIso);
  return `${format(d, 'M/d')}(${WD_JA[weekdayOfIso(dateIso)]})`;
}

/** 差分項目 (before/after) から表示用フィールドを取り出す (useInbound と同じ整理)。 */
function itemField(item: CorrectionItem, key: string): string {
  const rec = (item.after ?? item.before ?? {}) as Record<string, unknown>;
  const v = rec[key];
  return typeof v === 'string' ? v : v == null ? '' : String(v);
}

/** 項目の「日」(1-31) を当週の実日付 (YYYY-MM-DD) へ解決する。 */
function itemDateIso(item: CorrectionItem, weekStartIso: string): string | null {
  const dayRaw = itemField(item, 'date');
  const day = Number.parseInt(dayRaw, 10);
  if (!Number.isFinite(day)) return null;
  const start = parseISO(weekStartIso);
  for (let i = 0; i < 7; i++) {
    const d = addDays(start, i);
    if (d.getDate() === day) return format(d, 'yyyy-MM-dd');
  }
  return null;
}

const ITEM_ACTION_JA: Record<string, string> = {
  add: '追加',
  delete: '取消',
  edit: '変更',
  date_change: '日付変更',
};

const EVENT_ACTION_JA: Record<ReconcileMarker['action'], string> = {
  add: '🟣 カイポケのみ → 取込で追加',
  update: '🟡 内容が違う → 取込で更新',
  delete: '🔵 らく助のみ（カイポケ側に無し）→ 取込で削除',
};

type Phase = 'idle' | 'events' | 'visits' | 'ready' | 'error';

export interface KaipokeReconcilePanelProps {
  /** 対象週の月曜 (YYYY-MM-DD)。 */
  weekStartIso: string;
  canEdit: boolean;
  staffMap: Map<string, StaffRead>;
  onClose: () => void;
  /** 盤面ゴーストの供給。null = 突合終了 (マーカー消去)。 */
  onEventMarkersChange: (markers: ReconcileMarkersByCell | null) => void;
}

export function KaipokeReconcilePanel({
  weekStartIso,
  canEdit,
  staffMap,
  onClose,
  onEventMarkersChange,
}: KaipokeReconcilePanelProps) {
  const [phase, setPhase] = React.useState<Phase>('idle');
  const [error, setError] = React.useState<string | null>(null);
  const [eventsPlan, setEventsPlan] = React.useState<EventsInboundPreview | null>(null);
  const [visitsPlan, setVisitsPlan] = React.useState<SmartInboundPreview | null>(null);
  const [fetchedAt, setFetchedAt] = React.useState<Date | null>(null);
  const [appliedEventIds, setAppliedEventIds] = React.useState<Set<string>>(new Set());
  const [appliedItemIds, setAppliedItemIds] = React.useState<Set<string>>(new Set());
  const [busyKey, setBusyKey] = React.useState<string | null>(null);

  const live = useKaipokeLive();
  const rpaRunning = live.data?.running === true;
  const rootRef = React.useRef<HTMLElement | null>(null);

  const eventsPreviewMut = useEventsInboundPreview();
  const applyEventsMut = useApplyEventsInbound();
  const smartPreviewMut = useSmartInboundPreview();
  const applyInboundMut = useApplyInbound();
  const updateItemMut = useUpdateCorrectionItem();
  const bulkItemsMut = useBulkUpdateItems();

  // ⇩ 取込差分シート (#7): 実績のない日も 1 件ずつ取り込めるようにする inbound
  // シート。計算済みならこちらを訪問差分一覧の供給源にする (smart の sheet は
  // 実績のある日の差分のみのため)。
  const [inSheetId, setInSheetId] = React.useState<string | null>(null);
  const [inFetching, setInFetching] = React.useState(false);
  const effectiveVisitSheetId = inSheetId ?? visitsPlan?.sheetId ?? null;
  const itemsQuery = useCorrectionItems(effectiveVisitSheetId ?? undefined, { limit: 500 });
  const visitItems = React.useMemo(() => itemsQuery.data?.items ?? [], [itemsQuery.data]);

  // ─── ⇧ 送信方向 (らく助→カイポケ・訪問) — C2 (既存 diff-local + apply の結線) ───
  // カイポケ書込 RPA は自動反映パイプライン (auto_apply・2026-07 本番稼働) を再利用。
  const diffLocalMut = useStartDiffLocal();
  const startApplyMut = useStartApply();
  const [outSheetId, setOutSheetId] = React.useState<string | null>(null);
  const [outFetching, setOutFetching] = React.useState(false);
  const [outFetchedAt, setOutFetchedAt] = React.useState<Date | null>(null);
  const [sentItemIds, setSentItemIds] = React.useState<Set<string>>(new Set());
  const [pendingSendKey, setPendingSendKey] = React.useState<string | null>(null);
  const outItemsQuery = useCorrectionItems(outSheetId ?? undefined, { limit: 500 });
  const outItems = React.useMemo(() => outItemsQuery.data?.items ?? [], [outItemsQuery.data]);

  // ⇩ 取込差分 (実績のない日も 1 件ずつ取り込むための inbound シート・#7)。
  const diffInboundMut = useStartDiffInbound();

  const fetchInboundDiff = async () => {
    setInFetching(true);
    try {
      const res = await diffInboundMut.mutateAsync({
        month: weekStartIso.slice(0, 7),
        weekStart: weekStartIso,
      });
      setInSheetId(res.sheetId);
      setAppliedItemIds(new Set());
      const total = res.summary.total ?? 0;
      toast.success(`取込差分を計算しました（${total} 件）`);
    } catch (err) {
      toast.error(
        `取込差分の計算に失敗しました: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setInFetching(false);
    }
  };

  const fetchOutboundDiff = async () => {
    setOutFetching(true);
    setOutSheetId(null);
    setSentItemIds(new Set());
    try {
      const weekEndIso = format(addDays(parseISO(weekStartIso), 6), 'yyyy-MM-dd');
      const res = await diffLocalMut.mutateAsync({
        month: weekStartIso.slice(0, 7),
        weekStart: weekStartIso,
        weekEnd: weekEndIso,
      });
      setOutSheetId(res.sheetId);
      setOutFetchedAt(new Date());
      const total = res.summary.total ?? 0;
      toast.success(`送信差分を計算しました（${total} 件）`);
    } catch (err) {
      toast.error(
        `送信差分の計算に失敗しました: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setOutFetching(false);
    }
  };

  /** 送信 (カイポケの利用者別スケジュールを実変更)。ids=null は全残件。 */
  const sendOutboundItems = async (targetIds: string[] | null, key: string) => {
    if (!outSheetId) return;
    const remaining = outItems.filter((it) => !sentItemIds.has(it.id));
    const targets = targetIds ? remaining.filter((it) => targetIds.includes(it.id)) : remaining;
    if (targets.length === 0) return;
    setBusyKey(key);
    try {
      // 部分適用 (itemIds): include 操作は不要・シートもロックされない
      // = 同じ計算結果から続けて 1 件ずつ送れる (旧: 1件送ると 409)。
      await startApplyMut.mutateAsync({
        sheetId: outSheetId,
        dryRun: false,
        itemIds: targets.map((t) => t.id),
      });
      setSentItemIds((prev) => {
        const next = new Set(prev);
        for (const t of targets) next.add(t.id);
        return next;
      });
      toast.success(
        `カイポケへの送信ジョブを開始しました（${targets.length} 件・1件あたり約30〜60秒）。` +
          '進行はライブモニターで確認できます。完了後は再突合をおすすめします',
      );
    } catch (err) {
      toast.error(`送信の開始に失敗しました: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyKey(null);
      setPendingSendKey(null);
    }
  };

  // ─── 👥 マスタ相互突合 (Phase M・PO発案 2026-08-21) ───
  const masterReconcileMut = useMasterReconcile();
  const [masterResult, setMasterResult] = React.useState<MasterReconcile | null>(null);
  const [masterFetching, setMasterFetching] = React.useState(false);

  const runMasterReconcile = async () => {
    setMasterFetching(true);
    try {
      const res = await masterReconcileMut.mutateAsync({ month: weekStartIso.slice(0, 7) });
      setMasterResult(res);
    } catch (err) {
      toast.error(`マスタ突合に失敗しました: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setMasterFetching(false);
    }
  };

  /** 2 段クリック確認 (外部システムへの実書込のため)。 */
  const handleSendClick = (targetIds: string[] | null, key: string) => {
    if (pendingSendKey === key) {
      void sendOutboundItems(targetIds, key);
      return;
    }
    setPendingSendKey(key);
    window.setTimeout(() => {
      setPendingSendKey((cur) => (cur === key ? null : cur));
    }, 5000);
  };

  // ─── 取得 (RPA 単一スロットのためイベント→訪問の直列) ───
  const runFetch = React.useCallback(async () => {
    setError(null);
    setEventsPlan(null);
    setVisitsPlan(null);
    setAppliedEventIds(new Set());
    setAppliedItemIds(new Set());
    // 再突合時は送信/取込差分もリセット (PO指摘 2026-08-21: 古い「取消127件」
    // リストが残り続けて誤解を招いた)。
    setOutSheetId(null);
    setOutFetchedAt(null);
    setSentItemIds(new Set());
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
    } catch (err) {
      setError(err instanceof Error ? err.message : '突合の取得に失敗しました');
      setPhase('error');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [weekStartIso]);

  // ─── 盤面ゴーストの供給 (適用済みは除外・アンマウントで消去) ───
  const remainingChanges = React.useMemo(
    () => (eventsPlan?.changes ?? []).filter((c) => !appliedEventIds.has(c.externalId)),
    [eventsPlan, appliedEventIds],
  );
  React.useEffect(() => {
    if (!eventsPlan) {
      onEventMarkersChange(null);
      return;
    }
    const m: ReconcileMarkersByCell = new Map();
    for (const c of remainingChanges) {
      const key = `${c.staffId}:${weekdayOfIso(c.date)}`;
      const arr = m.get(key) ?? [];
      arr.push({
        action: c.action,
        externalId: c.externalId,
        title: c.title,
        start: c.start,
        end: c.end,
        beforeStart: c.beforeStart ?? null,
        beforeEnd: c.beforeEnd ?? null,
      });
      m.set(key, arr);
    }
    onEventMarkersChange(m);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [remainingChanges, eventsPlan]);
  React.useEffect(() => {
    return () => onEventMarkersChange(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ─── 開いた瞬間の分かりやすさ (PO指摘 2026-08-21: 押しても何が起きたか不明) ───
  // ①パネル位置へスクロール ②RPAが空いていれば自動で突合を開始する
  // (live スナップショットの初回取得を最大3秒待ってから判定・1回だけ)。
  const autoStartedRef = React.useRef(false);
  React.useEffect(() => {
    // jsdom (テスト) には scrollIntoView が無いため optional call。
    rootRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  }, []);
  React.useEffect(() => {
    if (autoStartedRef.current) return;
    if (!canEdit || phase !== 'idle') return;
    if (live.data === undefined) return; // running 判定がまだ
    if (live.data.running === true) return; // RPA空き待ち — 空いたら本effectが再発火して開始
    autoStartedRef.current = true;
    void runFetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canEdit, phase, live.data]);

  // ─── 適用: イベント 1 件 / 全件 ───
  const applyEventChanges = async (changes: EventsInboundChange[], label: string) => {
    if (changes.length === 0) return;
    setBusyKey(label);
    try {
      const res = await applyEventsMut.mutateAsync({
        weekStart: weekStartIso,
        dryRun: false,
        changes,
      });
      if (res.failed > 0) {
        toast.error(
          `一部の取込に失敗しました (成功 ${res.added + res.updated + res.deleted} / 失敗 ${res.failed})`,
        );
      } else {
        toast.success(
          `イベントを取り込みました（追加${res.added}・更新${res.updated}・削除${res.deleted}）`,
        );
      }
      setAppliedEventIds((prev) => {
        const next = new Set(prev);
        for (const c of changes) next.add(c.externalId);
        return next;
      });
    } catch (err) {
      toast.error(`取込に失敗しました: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  // ─── 適用: 訪問差分 1 件 (include 排他 → 当日 apply) ───
  const applyVisitItem = async (item: CorrectionItem) => {
    const sheetId = effectiveVisitSheetId;
    if (!sheetId) return;
    const dateIso = itemDateIso(item, weekStartIso);
    if (!dateIso) {
      toast.error('この差分の日付を特定できませんでした');
      return;
    }
    setBusyKey(item.id);
    try {
      // include をこの 1 件だけに絞ってから適用する (apply-inbound は
      // include=true の項目 × days のみを反映する — integrations.py:1801)。
      // 適用済み項目は再度 include を触らない (レビュー指摘: 無駄なAPI往復の削減)。
      const otherIds = visitItems
        .filter((it) => it.id !== item.id && !appliedItemIds.has(it.id))
        .map((it) => it.id);
      if (otherIds.length > 0) {
        await bulkItemsMut.mutateAsync({ sheetId, ids: otherIds, patch: { include: false } });
      }
      await updateItemMut.mutateAsync({ id: item.id, patch: { include: true } });
      const res = await applyInboundMut.mutateAsync({
        sheetId,
        dryRun: false,
        days: [dateIso],
      });
      if (res.failed > 0) {
        toast.error('取込に失敗した項目があります。連携ページで詳細をご確認ください');
      } else {
        toast.success(
          `${itemField(item, 'user_name') || '訪問'} の差分を取り込みました（${fmtMd(dateIso)}）`,
        );
      }
      setAppliedItemIds((prev) => new Set(prev).add(item.id));
    } catch (err) {
      toast.error(`取込に失敗しました: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  const staffName = (staffId: string): string => staffMap.get(staffId)?.name ?? '（不明）';

  const pendingVisitItems = visitItems.filter((it) => !appliedItemIds.has(it.id));
  const stale = fetchedAt != null && Date.now() - fetchedAt.getTime() > 15 * 60_000; // §7-3 鮮度

  return (
    <section
      ref={rootRef}
      className="rounded-lg border-2 border-brand-primary/40 bg-bg-base shadow-sm"
      data-testid="kaipoke-reconcile-panel"
      aria-label="カイポケ突合（差分の一覧と取込）"
    >
      {/* ヘッダ */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border-subtle px-3 py-2">
        <h3 className="text-sm font-bold text-text-primary">🔄 カイポケ突合</h3>
        {phase === 'ready' && fetchedAt ? (
          <span
            className={`text-[11px] ${stale ? 'font-bold text-warning-strong' : 'text-text-muted'}`}
            data-testid="reconcile-fetched-at"
          >
            カイポケ側: {format(fetchedAt, 'HH:mm')} 時点
            {stale ? '（古い可能性 — 再突合を推奨）' : ''}
          </span>
        ) : null}
        <div className="ml-auto flex items-center gap-1.5">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!canEdit || phase === 'events' || phase === 'visits' || rpaRunning}
            onClick={() => void runFetch()}
            data-testid="reconcile-fetch-button"
            title={
              rpaRunning
                ? 'カイポケ連携が実行中です（単一スロット）。完了後に実行してください'
                : 'カイポケの当週データを取得して突き合わせます（2〜3分）'
            }
          >
            {phase === 'events' || phase === 'visits' ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="mr-1 h-4 w-4" aria-hidden />
            )}
            {phase === 'idle' || phase === 'error' ? 'カイポケと突合' : '再突合'}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={onClose}
            aria-label="突合を閉じる"
          >
            <X className="h-4 w-4" aria-hidden />
          </Button>
        </div>
      </div>

      <div className="space-y-3 px-3 py-2">
        {rpaRunning && phase === 'idle' ? (
          <p className="text-[12px] text-warning-strong">
            カイポケ連携が実行中のため空き待ちです（RPA は同時に 1 つだけ）。空き次第、
            自動で突合を開始します。
          </p>
        ) : null}
        {!rpaRunning && phase === 'idle' ? (
          <p className="text-[12px] text-text-secondary">
            <Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" aria-hidden />
            まもなく突合を開始します — カイポケの当週（イベント→訪問の順・2〜3分）を取得し、
            らく助との差分をここと盤面上（破線ゴースト）に表示します。
          </p>
        ) : null}
        {phase === 'events' ? (
          <p className="text-[12px] text-text-secondary" data-testid="reconcile-progress">
            ①/② イベント（個別業務）を取得中…（1〜2分）
          </p>
        ) : null}
        {phase === 'visits' ? (
          <p className="text-[12px] text-text-secondary" data-testid="reconcile-progress">
            ②/② 訪問スケジュールを取得中…（約1分）
          </p>
        ) : null}
        {phase === 'error' ? (
          <p className="text-[12px] font-medium text-red-600" data-testid="reconcile-error">
            {error}
          </p>
        ) : null}

        {phase === 'ready' && eventsPlan ? (
          <>
            {/* ── イベント差分 ── */}
            <div>
              <div className="mb-1 flex items-center gap-2">
                <h4 className="text-[12px] font-bold text-text-primary">
                  イベント差分 {remainingChanges.length} 件
                </h4>
                {remainingChanges.length > 0 ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-6 px-2 text-[11px]"
                    disabled={!canEdit || busyKey !== null}
                    onClick={() => void applyEventChanges(remainingChanges, '__all_events__')}
                    data-testid="reconcile-apply-all-events"
                  >
                    {busyKey === '__all_events__' ? (
                      <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
                    ) : null}
                    ⇩ 全件取り込む
                  </Button>
                ) : null}
              </div>
              {remainingChanges.length === 0 ? (
                <p className="text-[11px] text-text-muted" data-testid="reconcile-events-empty">
                  ✅ イベントはカイポケと一致しています
                </p>
              ) : (
                <ul className="space-y-1" data-testid="reconcile-events-list">
                  {remainingChanges.map((c) => (
                    <li
                      key={c.externalId}
                      className="flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded border border-border-subtle px-2 py-1 text-[11px]"
                      data-testid={`reconcile-event-${c.externalId}`}
                    >
                      <span className="font-medium">{EVENT_ACTION_JA[c.action]}</span>
                      <span className="font-bold">{c.staffName || staffName(c.staffId)}</span>
                      <span>{fmtMd(c.date)}</span>
                      <span className="tnum">
                        {c.action === 'update' && c.beforeStart
                          ? `${c.beforeStart}〜${c.beforeEnd} → ${c.start}〜${c.end}`
                          : `${c.start}〜${c.end}`}
                      </span>
                      <span className="min-w-0 flex-1 truncate">{c.title}</span>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="h-6 px-2 text-[11px]"
                        disabled={!canEdit || busyKey !== null}
                        onClick={() => void applyEventChanges([c], c.externalId)}
                        data-testid={`reconcile-apply-event-${c.externalId}`}
                      >
                        {busyKey === c.externalId ? (
                          <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
                        ) : null}
                        ⇩ 取り込む
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              {eventsPlan.conflicts.length > 0 ? (
                <p className="mt-1 text-[11px] text-warning-strong">
                  ⚠ 取込イベントと訪問の時間が重なる箇所が {eventsPlan.conflicts.length} 件あります
                  （取込はブロックしません — 担当の見直しをご検討ください）
                </p>
              ) : null}
            </div>

            {/* ── 訪問差分 (⇩ 1 件ずつ取込可) ── */}
            {visitsPlan ? (
              <div>
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <h4 className="text-[12px] font-bold text-text-primary">
                    訪問差分{inSheetId ? '（全曜日）' : '（実績のある日）'}
                    {pendingVisitItems.length} 件
                  </h4>
                  {/* #7: 実績のない日も 1 件ずつ取り込めるようにする取込差分の計算。 */}
                  {inSheetId == null ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-6 px-2 text-[11px]"
                      disabled={!canEdit || inFetching || busyKey !== null || rpaRunning}
                      onClick={() => void fetchInboundDiff()}
                      data-testid="reconcile-inbound-diff-button"
                      title="実績のない日も含めた全曜日の取込差分を計算します（約1分）"
                    >
                      {inFetching ? (
                        <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
                      ) : null}
                      ⇩ 取込差分を計算（全曜日・1件ずつ）
                    </Button>
                  ) : null}
                </div>
                {effectiveVisitSheetId == null || pendingVisitItems.length === 0 ? (
                  <p className="text-[11px] text-text-muted" data-testid="reconcile-visits-empty">
                    ✅{' '}
                    {inSheetId
                      ? '訪問はカイポケと一致しています'
                      : '実績のある日の訪問はカイポケと一致しています'}
                  </p>
                ) : (
                  <ul className="space-y-1" data-testid="reconcile-visits-list">
                    {pendingVisitItems.map((it) => {
                      const dateIso = itemDateIso(it, weekStartIso);
                      return (
                        <li
                          key={it.id}
                          className="flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded border border-border-subtle px-2 py-1 text-[11px]"
                          data-testid={`reconcile-visit-${it.id}`}
                        >
                          <span className="font-medium">
                            🟡 {ITEM_ACTION_JA[it.action] ?? it.action}
                          </span>
                          <span className="font-bold">
                            {itemField(it, 'user_name') || '（患者不明）'}
                          </span>
                          <span>{dateIso ? fmtMd(dateIso) : `${itemField(it, 'date')}日`}</span>
                          <span className="tnum">{itemField(it, 'start_time')}</span>
                          <span className="min-w-0 flex-1 truncate text-text-muted">
                            {itemField(it, 'staff1')}
                          </span>
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            className="h-6 px-2 text-[11px]"
                            disabled={!canEdit || busyKey !== null || !dateIso}
                            onClick={() => void applyVisitItem(it)}
                            data-testid={`reconcile-apply-visit-${it.id}`}
                          >
                            {busyKey === it.id ? (
                              <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
                            ) : null}
                            ⇩ 取り込む
                          </Button>
                        </li>
                      );
                    })}
                  </ul>
                )}

                {/* ── 置換対象日の案内 (取込差分を計算すれば 1 件ずつ取込可) ── */}
                {inSheetId == null && visitsPlan.replaceDays.length > 0 ? (
                  <p
                    className="mt-1.5 text-[11px] text-text-muted"
                    data-testid="reconcile-replace-days"
                  >
                    🗓 実績のない日（
                    {visitsPlan.replaceDays.map((d) => fmtMd(d)).join('・')}
                    ）の差分は上の「⇩ 取込差分を計算」で 1 件ずつ取り込めます。日単位の
                    丸ごと差し替えは連携ページの「カイポケから取り込む」
                    {visitsPlan.replace ? `（差し替え予定 ${visitsPlan.replace.inserted} 件）` : ''}
                    。
                  </p>
                ) : null}
              </div>
            ) : null}

            {/* ── ⇧ 送信方向 (らく助→カイポケ・訪問) — C2 ── */}
            <div className="border-t border-border-subtle pt-2">
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <h4 className="text-[12px] font-bold text-text-primary">
                  ⇧ らく助 → カイポケ（訪問の送信）
                </h4>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-6 px-2 text-[11px]"
                  disabled={!canEdit || outFetching || busyKey !== null || rpaRunning}
                  onClick={() => void fetchOutboundDiff()}
                  data-testid="reconcile-outbound-diff-button"
                  title="らく助の今週の盤面とカイポケを比べ、カイポケへ送るべき差分を計算します（約1分）"
                >
                  {outFetching ? (
                    <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
                  ) : null}
                  送信差分を計算
                </Button>
              </div>
              {outSheetId == null && !outFetching ? (
                <p className="text-[11px] text-text-muted">
                  盤面で組み替えた結果（担当・曜日の変更など）をカイポケの利用者別スケジュールへ
                  反映できます。まず「送信差分を計算」を押してください。
                </p>
              ) : null}
              {outSheetId != null
                ? (() => {
                    // include=false = 送信済み (サーバ側の再送ガードと同じ判定・
                    // リロード後もここで消える)。
                    const pendingAll = outItems.filter(
                      (it) => !sentItemIds.has(it.id) && it.include !== false,
                    );
                    // 過去日ガード (2026-08-21 実機テストの教訓): 過去日〜当日は
                    // カイポケ側に実績が付いていることがあり送信対象外 (BEも422で拒否)。
                    // 「今日」は BE と同じ Asia/Tokyo 基準 (レビューM2: ブラウザTZ差対策)。
                    const todayIso = new Intl.DateTimeFormat('sv-SE', {
                      timeZone: 'Asia/Tokyo',
                    }).format(new Date());
                    const isPast = (it: CorrectionItem) => {
                      const d = itemDateIso(it, weekStartIso);
                      return d != null && d <= todayIso;
                    };
                    const pastCount = pendingAll.filter(isPast).length;
                    const pendingOut = pendingAll.filter((it) => !isPast(it));
                    return (
                      <>
                        {outFetchedAt ? (
                          <p
                            className="mb-1 text-[10px] text-text-muted"
                            data-testid="reconcile-outbound-fetched-at"
                          >
                            計算: {format(outFetchedAt, 'HH:mm')} 時点
                            {pastCount > 0
                              ? ` ・ 過去日〜本日の ${pastCount} 件は実績保護のため送信対象外`
                              : ''}
                          </p>
                        ) : null}
                        {pendingOut.length === 0 ? (
                          <p
                            className="text-[11px] text-text-muted"
                            data-testid="reconcile-outbound-empty"
                          >
                            ✅ カイポケへ送るべき差分はありません
                            {pastCount > 0 ? '（未来日分）' : ''}
                          </p>
                        ) : (
                          <>
                            <div className="mb-1">
                              <Button
                                type="button"
                                size="sm"
                                variant={pendingSendKey === '__all_out__' ? 'default' : 'outline'}
                                className="h-6 px-2 text-[11px]"
                                disabled={!canEdit || busyKey !== null || rpaRunning}
                                onClick={() =>
                                  handleSendClick(
                                    pendingOut.map((it) => it.id),
                                    '__all_out__',
                                  )
                                }
                                data-testid="reconcile-send-all-outbound"
                              >
                                {busyKey === '__all_out__' ? (
                                  <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
                                ) : null}
                                {pendingSendKey === '__all_out__'
                                  ? `本当に送信しますか？（${pendingOut.length}件・もう一度押すと実行）`
                                  : `⇧ 全件送信（${pendingOut.length}件）`}
                              </Button>
                            </div>
                            <ul className="space-y-1" data-testid="reconcile-outbound-list">
                              {pendingOut.map((it) => (
                                <li
                                  key={it.id}
                                  className="flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded border border-border-subtle px-2 py-1 text-[11px]"
                                  data-testid={`reconcile-outbound-${it.id}`}
                                >
                                  <span className="font-medium">
                                    🔵 {ITEM_ACTION_JA[it.action] ?? it.action}
                                  </span>
                                  <span className="font-bold">
                                    {itemField(it, 'user_name') || '（患者不明）'}
                                  </span>
                                  <span>
                                    {(() => {
                                      const d = itemDateIso(it, weekStartIso);
                                      return d ? fmtMd(d) : `${itemField(it, 'date')}日`;
                                    })()}
                                  </span>
                                  <span className="tnum">{itemField(it, 'start_time')}</span>
                                  <span className="min-w-0 flex-1 truncate text-text-muted">
                                    {itemField(it, 'staff1')}
                                  </span>
                                  {/* 担当未割当の送信はカイポケで '-' 登録になる (隠さず表示)。 */}
                                  {it.action !== 'delete' &&
                                  ['', '-'].includes(
                                    String(
                                      (it.after as Record<string, unknown> | null)?.staff1 ?? '',
                                    ),
                                  ) ? (
                                    <span
                                      className="rounded bg-amber-100 px-1 py-px text-[10px] font-medium text-amber-800"
                                      data-testid={`reconcile-outbound-unassigned-${it.id}`}
                                    >
                                      担当なし（-で送信）
                                    </span>
                                  ) : null}
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant={pendingSendKey === it.id ? 'default' : 'outline'}
                                    className="h-6 px-2 text-[11px]"
                                    disabled={!canEdit || busyKey !== null || rpaRunning}
                                    onClick={() => handleSendClick([it.id], it.id)}
                                    data-testid={`reconcile-send-outbound-${it.id}`}
                                  >
                                    {busyKey === it.id ? (
                                      <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
                                    ) : null}
                                    {pendingSendKey === it.id ? '本当に送信？' : '⇧ 送信'}
                                  </Button>
                                </li>
                              ))}
                            </ul>
                            <p className="mt-1 text-[10px] text-text-muted">
                              送信はカイポケの利用者別スケジュールを直接変更します（1件あたり約30〜60秒・
                              単一スロットのため送信中は取込できません）。
                            </p>
                          </>
                        )}
                      </>
                    );
                  })()
                : null}
            </div>

            {/* ── 👥 マスタ相互突合 (Phase M): 名簿と表記ズレの診断 ── */}
            <div className="border-t border-border-subtle pt-2">
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <h4 className="text-[12px] font-bold text-text-primary">
                  👥 マスタ突合（名簿チェック）
                </h4>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-6 px-2 text-[11px]"
                  disabled={!canEdit || masterFetching || busyKey !== null || rpaRunning}
                  onClick={() => void runMasterReconcile()}
                  data-testid="reconcile-master-button"
                  title="カイポケの当月スケジュールに現れる氏名と、らく助の患者/スタッフマスタを突き合わせます（約1分）"
                >
                  {masterFetching ? (
                    <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
                  ) : null}
                  実行（約1分）
                </Button>
              </div>
              {masterResult == null && !masterFetching ? (
                <p className="text-[11px] text-text-muted">
                  患者・スタッフの登録の有無と表記ズレ（スペース・異体字）を診断します。
                  取込・送信がうまくマッチしないときの原因調査に。
                </p>
              ) : null}
              {masterResult ? (
                <div className="space-y-1.5" data-testid="reconcile-master-result">
                  {(
                    [
                      { label: '患者', g: masterResult.patients },
                      { label: 'スタッフ', g: masterResult.staff },
                    ] as const
                  ).map(({ label, g }) => (
                    <div key={label} className="rounded border border-border-subtle px-2 py-1.5">
                      <p className="text-[11px] font-bold text-text-primary">
                        {label}: 一致 {g.matched} ・ 表記ズレ {g.notationDiff.length} ・
                        カイポケのみ {g.kaipokeOnly.length} ・ らく助のみ {g.rakusukeOnly.length}
                      </p>
                      {g.notationDiff.length > 0 ? (
                        <p className="mt-0.5 text-[11px] text-amber-800">
                          🟡 表記ズレ（同期は自動吸収済み・マスタ修正推奨）:{' '}
                          {g.notationDiff
                            .map((d) => `カイポケ「${d.kaipoke}」⇔らく助「${d.rakusuke}」`)
                            .join(' / ')}
                        </p>
                      ) : null}
                      {g.kaipokeOnly.length > 0 ? (
                        <p className="mt-0.5 text-[11px] text-violet-800">
                          🟣 カイポケのみ（らく助未登録）: {g.kaipokeOnly.join('、')}
                        </p>
                      ) : null}
                      {g.rakusukeOnly.length > 0 ? (
                        <p className="mt-0.5 text-[11px] text-sky-800">
                          🔵 らく助のみ（当月のカイポケスケジュールに未出現）:{' '}
                          {g.rakusukeOnly.join('、')}
                        </p>
                      ) : null}
                    </div>
                  ))}
                  <p className="text-[10px] text-text-muted">
                    ※ カイポケ側は「当月のスケジュールに現れた氏名」で判定します
                    （予定の無い方は判定できません）。
                  </p>
                </div>
              ) : null}
            </div>
          </>
        ) : null}
      </div>
    </section>
  );
}
