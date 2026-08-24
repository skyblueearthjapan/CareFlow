'use client';

/**
 * 「研修日 / イベント」カード (staff-event-history-design.md §2 Phase 1〜3 /
 * docs/mockups/event-history-filter-mock.html)。
 *
 * スタッフ詳細ページとスタッフ編集ページの **両方** がこの 1 本を使う
 * (PO 2026-08-25: 編集画面にも同じ絞り込みとひな形化を)。以前は詳細ページに
 * インラインで持ち、編集ページは絞り込み無しの旧一覧 (EventsCardInline) を
 * 別に持っていたため、機能差が出ていた。
 *
 *   - 期間タブ (既定=今週) + 検索 + 種類チップ = `EventsFilterBar`
 *   - 行アクション: ☆ ひな形にする (1 クリック) / 編集 / ⋯ (📌 毎週固定にする)
 *   - 絞り込みは **BE パラメータ** で行う (limit 200 の窓を FE で削らない)
 */
import { useMemo, useState } from 'react';
import { MoreHorizontal, Pencil, Plus } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Skeleton } from '@/components/ui/skeleton';
import { useStaffEvents } from '@/lib/queries/staff-events';
import type { EventRead } from '@/lib/schemas/staff-events';

import {
  EventTemplateFormDialog,
  SHARED_TEMPLATE_SCOPE,
  type EventTemplateFormInitial,
} from '../../_components/EventTemplatesCard';
import { toEventDefaultWeekday } from '../../_components/WeekdayPicker';
import { EventAddDialog } from './EventAddDialog';
import { EventDefaultAddDialog } from './EventDefaultsCard';
import { EventEditDialog } from './EventEditDialog';
import {
  DEFAULT_EVENTS_FILTER,
  EventsFilterBar,
  eventPeriodRange,
  todayIso,
  toStaffEventFilters,
  type EventsFilterState,
} from './EventsFilterBar';

/**
 * 出所バッジ (staff-event-history-design.md §2 Phase 1)。
 * カイポケ取込 = 青系 / 固定イベント = 緑系 / 研修 = 橙系。手動イベントは
 * 従来どおりの無彩色バッジ。
 */
export function EventBadge({ source, type }: { source: string; type: string }) {
  const [label, tone] =
    type === '研修'
      ? ['研修', 'border-warning-strong bg-warning-bg text-warning-strong']
      : source === 'kaipoke'
        ? ['カイポケ', 'border-info-strong bg-info-bg text-info-strong']
        : source === 'fixed'
          ? ['固定', 'border-success bg-success-bg text-success']
          : ['イベント', 'border-border-default bg-bg-muted text-text-secondary'];
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${tone}`}>
      {label}
    </span>
  );
}

/**
 * イベント行の「⋯」メニュー (docs/mockups/event-history-filter-mock.html)。
 * 📌 毎週固定にする… の入口 (☆ ひな形は行の直接ボタンへ昇格済み)。
 */
function EventRowMenu({ event, onPin }: { event: EventRead; onPin: (e: EventRead) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-8 w-8 shrink-0 p-0 text-text-muted"
          aria-label={`${event.title} の操作メニュー`}
          data-testid={`event-row-menu-${event.id}`}
        >
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-56 p-1">
        <button
          type="button"
          className="w-full rounded-md px-3 py-2 text-left text-sm hover:bg-bg-muted"
          onClick={() => {
            setOpen(false);
            onPin(event);
          }}
          data-testid={`event-row-pin-${event.id}`}
        >
          📌 毎週固定にする…
        </button>
      </PopoverContent>
    </Popover>
  );
}

/** イベント行 → ひな形ダイアログの初期値。 */
function templateInitialFrom(e: EventRead): EventTemplateFormInitial {
  return {
    title: e.title,
    event_type: e.type === '研修' ? 'training' : 'event',
    start_time: e.start_time,
    end_time: e.end_time,
    blocking: e.blocking,
    note: e.note ?? null,
  };
}

export interface EventsCardProps {
  staffId: string;
  canEdit: boolean;
}

export function EventsCard({ staffId, canEdit }: EventsCardProps) {
  const [filter, setFilter] = useState<EventsFilterState>(DEFAULT_EVENTS_FILTER);
  const [addOpen, setAddOpen] = useState(false);
  const [editing, setEditing] = useState<EventRead | null>(null);
  // 行の ☆ / ⋯ からの 2 つの入口。
  const [templateFrom, setTemplateFrom] = useState<EventRead | null>(null);
  const [pinFrom, setPinFrom] = useState<EventRead | null>(null);

  // `initial` はダイアログ側の初期化 effect の deps に入るため参照を固定する
  // (scopeChoice は描画時に読むだけなので固定不要)。
  const templateInitial = useMemo<EventTemplateFormInitial | undefined>(
    () => (templateFrom ? templateInitialFrom(templateFrom) : undefined),
    [templateFrom],
  );
  const pinInitial = useMemo(() => {
    if (!pinFrom) return undefined;
    // 「そのイベントの曜日」を既定に。日曜は固定イベントの対象外なので空選択。
    const [y, m, d] = pinFrom.date.split('-').map(Number);
    const weekday = toEventDefaultWeekday(new Date(y!, m! - 1, d!).getDay());
    return {
      title: pinFrom.title,
      start_time: pinFrom.start_time,
      end_time: pinFrom.end_time,
      blocking: pinFrom.blocking,
      weekdays: weekday === null ? [] : [weekday],
    };
  }, [pinFrom]);

  // 期間タブ → from/to + 並び順。日付は 1 レンダー内で固定 (跨日の揺らぎ回避)。
  const { range, order } = useMemo(() => {
    const period = eventPeriodRange(filter.tab, new Date());
    return { range: period.range, order: period.order };
  }, [filter.tab]);
  // 「今日」の強調はメモ化しない (開きっぱなしで日付が変わっても再レンダーで追随)。
  const today = todayIso(new Date());

  // 絞り込みは **BE パラメータ** で行う (limit 200 の窓を FE で削らない)。
  const { data, isLoading, isError, error } = useStaffEvents(
    staffId,
    range,
    toStaffEventFilters(filter, order),
  );
  // 「全M件から絞り込み」の M = 期間タブのみ適用した件数。未絞り込み時は
  // クエリキーが上と一致するので追加リクエストは発生しない。
  const totalQuery = useStaffEvents(staffId, range, { order });

  const rows = data ?? [];
  const total = totalQuery.data?.length ?? rows.length;

  return (
    <Card data-testid="staff-events-card">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>研修日 / イベント</CardTitle>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!canEdit}
          title={canEdit ? undefined : '編集権限がありません'}
          onClick={() => setAddOpen(true)}
        >
          <Plus className="h-4 w-4" />
          追加
        </Button>
      </CardHeader>
      <CardContent>
        <EventsFilterBar value={filter} onChange={setFilter} count={rows.length} total={total} />
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : isError ? (
          <Alert variant="destructive">
            <AlertTitle>取得に失敗しました</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        ) : rows.length === 0 ? (
          <p className="py-4 text-center text-sm text-text-muted">該当するイベントがありません</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {rows.map((e) => (
              <li
                key={e.id}
                className={`flex items-center justify-between gap-3 rounded border p-3 ${
                  e.date === today
                    ? 'border-brand-primary bg-brand-primary-50'
                    : 'border-border-default'
                }`}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <EventBadge source={e.source} type={e.type} />
                    <span
                      className={`font-medium text-text-primary ${
                        e.cancelled_at ? 'line-through opacity-60' : ''
                      }`}
                    >
                      {e.title}
                    </span>
                  </div>
                  <div
                    className={`tnum text-text-secondary ${e.cancelled_at ? 'line-through opacity-60' : ''}`}
                  >
                    {e.date}　{e.start_time} 〜 {e.end_time}
                  </div>
                  {e.note && <div className="text-xs text-text-muted">{e.note}</div>}
                </div>
                {/* ☆ ひな形にする — 1 クリックでひな形ダイアログへ (PO 2026-08-25) */}
                {canEdit && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="shrink-0"
                    title="この内容をひな形に保存"
                    onClick={() => setTemplateFrom(e)}
                    data-testid={`event-row-save-template-${e.id}`}
                  >
                    ☆ ひな形
                  </Button>
                )}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="shrink-0"
                  disabled={!canEdit}
                  title={canEdit ? undefined : '編集権限がありません'}
                  onClick={() => setEditing(e)}
                >
                  <Pencil className="h-4 w-4" />
                  編集
                </Button>
                {canEdit && <EventRowMenu event={e} onPin={setPinFrom} />}
              </li>
            ))}
          </ul>
        )}
      </CardContent>

      {canEdit && (
        <>
          <EventAddDialog staffId={staffId} open={addOpen} onOpenChange={setAddOpen} />
          <EventEditDialog
            staffId={staffId}
            event={editing}
            open={!!editing}
            onOpenChange={(next) => {
              if (!next) setEditing(null);
            }}
          />
          {/* ☆ ひな形にする — 保存先 (共通 / このスタッフの個人) を選べる。 */}
          <EventTemplateFormDialog
            open={templateFrom !== null}
            onOpenChange={(next) => {
              if (!next) setTemplateFrom(null);
            }}
            scope={SHARED_TEMPLATE_SCOPE}
            scopeChoice={{ staffId }}
            initial={templateInitial}
          />
          {/* 📌 毎週固定にする — タイトル・時刻・曜日を引き継ぐ。 */}
          <EventDefaultAddDialog
            staffId={staffId}
            open={pinFrom !== null}
            onOpenChange={(next) => {
              if (!next) setPinFrom(null);
            }}
            initial={pinInitial}
          />
        </>
      )}
    </Card>
  );
}
