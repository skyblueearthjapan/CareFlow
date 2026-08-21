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
  useSmartInboundPreview,
  useUpdateCorrectionItem,
} from '@/lib/queries/integrations';
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

  const eventsPreviewMut = useEventsInboundPreview();
  const applyEventsMut = useApplyEventsInbound();
  const smartPreviewMut = useSmartInboundPreview();
  const applyInboundMut = useApplyInbound();
  const updateItemMut = useUpdateCorrectionItem();
  const bulkItemsMut = useBulkUpdateItems();

  const itemsQuery = useCorrectionItems(visitsPlan?.sheetId ?? undefined, { limit: 500 });
  const visitItems = React.useMemo(
    () => itemsQuery.data?.items ?? [],
    [itemsQuery.data],
  );

  // ─── 取得 (RPA 単一スロットのためイベント→訪問の直列) ───
  const runFetch = React.useCallback(async () => {
    setError(null);
    setEventsPlan(null);
    setVisitsPlan(null);
    setAppliedEventIds(new Set());
    setAppliedItemIds(new Set());
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
        toast.error(`一部の取込に失敗しました (成功 ${res.added + res.updated + res.deleted} / 失敗 ${res.failed})`);
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
      toast.error(
        `取込に失敗しました: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setBusyKey(null);
    }
  };

  // ─── 適用: 訪問差分 1 件 (include 排他 → 当日 apply) ───
  const applyVisitItem = async (item: CorrectionItem) => {
    const sheetId = visitsPlan?.sheetId;
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
      const otherIds = visitItems.filter((it) => it.id !== item.id).map((it) => it.id);
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
      toast.error(
        `取込に失敗しました: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setBusyKey(null);
    }
  };

  const staffName = (staffId: string): string => staffMap.get(staffId)?.name ?? '（不明）';

  const pendingVisitItems = visitItems.filter((it) => !appliedItemIds.has(it.id));
  const stale =
    fetchedAt != null && Date.now() - fetchedAt.getTime() > 15 * 60_000; // §7-3 鮮度

  return (
    <section
      className="rounded-lg border border-border-default bg-bg-base"
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
            カイポケ側: {format(fetchedAt, 'HH:mm')} 時点{stale ? '（古い可能性 — 再突合を推奨）' : ''}
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
          <Button type="button" size="sm" variant="ghost" onClick={onClose} aria-label="突合を閉じる">
            <X className="h-4 w-4" aria-hidden />
          </Button>
        </div>
      </div>

      <div className="space-y-3 px-3 py-2">
        {rpaRunning && phase === 'idle' ? (
          <p className="text-[12px] text-warning-strong">
            カイポケ連携が実行中のため待機しています（RPA は同時に 1 つだけ）。
          </p>
        ) : null}
        {phase === 'idle' ? (
          <p className="text-[12px] text-text-muted">
            「カイポケと突合」を押すと、カイポケの当週（イベント→訪問の順・2〜3分）を取得し、
            らく助との差分をここと盤面上（破線ゴースト）に表示します。取り込みは差分 1 件ずつ選べます。
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

            {/* ── 訪問差分 (打刻あり日 = 1 件ずつ取込可) ── */}
            {visitsPlan ? (
              <div>
                <h4 className="mb-1 text-[12px] font-bold text-text-primary">
                  訪問差分（実績のある日）{pendingVisitItems.length} 件
                </h4>
                {visitsPlan.sheetId == null || pendingVisitItems.length === 0 ? (
                  <p className="text-[11px] text-text-muted" data-testid="reconcile-visits-empty">
                    ✅ 実績のある日の訪問はカイポケと一致しています
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
                          <span className="font-bold">{itemField(it, 'user_name') || '（患者不明）'}</span>
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

                {/* ── 置換対象日 (打刻なし日 = 丸ごと差し替えのみ) ── */}
                {visitsPlan.replaceDays.length > 0 ? (
                  <p className="mt-1.5 text-[11px] text-text-muted" data-testid="reconcile-replace-days">
                    🗓 実績のない日（
                    {visitsPlan.replaceDays.map((d) => fmtMd(d)).join('・')}
                    ）は日単位の丸ごと差し替えになります — 取り込む場合は連携ページの
                    「カイポケから取り込む」をご利用ください
                    {visitsPlan.replace ? `（差し替え予定 ${visitsPlan.replace.inserted} 件）` : ''}。
                  </p>
                ) : null}
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </section>
  );
}
