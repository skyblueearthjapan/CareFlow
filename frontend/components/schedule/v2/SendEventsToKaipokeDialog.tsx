'use client';

/**
 * SendEventsToKaipokeDialog — イベントをカイポケへ送る (Phase 3)。
 *
 * 正典 = docs/plans/kaipoke-event-two-way-design.md §3-①。
 * 職員スケジュールタブのツールバーから開く。当該週の manual イベントを
 * プレビュー → 選択 → RPA でカイポケの職員スケジュールへ登録する。
 * 送信成功行は BE で kaipoke 系へ昇格し、以後は取込と二重化しない。
 */
import * as React from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import {
  useEventsOutboundPreview,
  useSendEventsOutbound,
} from '@/lib/queries/integrations';
import type { EventsOutboundItem, EventsOutboundStatus } from '@/lib/schemas/integration';

const WEEKDAYS_JP = ['日', '月', '火', '水', '木', '金', '土'] as const;

function fmtJp(iso: string): string {
  const d = new Date(iso + 'T00:00:00');
  return `${d.getMonth() + 1}/${d.getDate()}（${WEEKDAYS_JP[d.getDay()]}）`;
}

interface SendEventsToKaipokeDialogProps {
  open: boolean;
  onClose: () => void;
  /** 対象週の月曜 (YYYY-MM-DD)。 */
  weekStartIso: string;
}

export function SendEventsToKaipokeDialog({
  open,
  onClose,
  weekStartIso,
}: SendEventsToKaipokeDialogProps) {
  const preview = useEventsOutboundPreview();
  const send = useSendEventsOutbound();
  const [items, setItems] = React.useState<EventsOutboundItem[] | null>(null);
  const [checked, setChecked] = React.useState<Set<string>>(new Set());
  const [done, setDone] = React.useState<EventsOutboundStatus | null>(null);

  // 開くたびにプレビューを取り直す (盤面の最新 manual イベントを反映)
  React.useEffect(() => {
    if (!open) return;
    setItems(null);
    setDone(null);
    setChecked(new Set());
    preview
      .mutateAsync({ weekStart: weekStartIso })
      .then((p) => {
        setItems(p.items);
        setChecked(new Set(p.items.filter((i) => i.sendable).map((i) => i.eventId)));
      })
      .catch(() => setItems([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, weekStartIso]);

  const sendable = (items ?? []).filter((i) => i.sendable);
  const unsendable = (items ?? []).filter((i) => !i.sendable);
  const selectedCount = sendable.filter((i) => checked.has(i.eventId)).length;

  const toggle = (id: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  async function handleSend() {
    if (selectedCount === 0 || send.isPending) return;
    try {
      const res = await send.mutateAsync({
        weekStart: weekStartIso,
        eventIds: sendable.filter((i) => checked.has(i.eventId)).map((i) => i.eventId),
      });
      setDone(res);
      const s = (res.summary ?? {}) as Record<string, number>;
      const okMsg = `カイポケへ送信しました — 登録 ${s.promoted ?? 0} / 既存と統合 ${s.deduped ?? 0}`;
      if ((s.failed ?? 0) > 0) {
        toast.warning(`${okMsg} / 失敗 ${s.failed} 件（下の結果をご確認ください）`);
      } else {
        toast.success(okMsg);
      }
    } catch (e) {
      toast.error('カイポケへの送信に失敗しました', {
        description: e instanceof Error ? e.message : String(e),
      });
    }
  }

  const summary = (done?.summary ?? null) as Record<string, number> | null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && send.isPending) return; // 送信中は閉じない
        if (!next) onClose();
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>イベントをカイポケへ送る</DialogTitle>
          <DialogDescription>
            この週のらく助のイベント（手動登録分）を、カイポケの職員スケジュール
            （個別業務）へ登録します。カイポケ側に同じ予定が既にある場合は
            自動でスキップして統合します。
          </DialogDescription>
        </DialogHeader>

        {items === null && (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        )}

        {items !== null && !done && (
          <div className="space-y-3">
            {sendable.length === 0 && (
              <p className="text-sm text-text-muted">
                この週に送信できるイベントはありません（カイポケ由来・送信済みのイベントは
                対象外です）。
              </p>
            )}
            {sendable.length > 0 && (
              <ul className="max-h-64 space-y-1.5 overflow-y-auto pr-1">
                {sendable.map((i) => (
                  <li key={i.eventId} className="flex items-center gap-2 text-sm">
                    <Checkbox
                      id={`send-ev-${i.eventId}`}
                      checked={checked.has(i.eventId)}
                      onCheckedChange={() => toggle(i.eventId)}
                      disabled={send.isPending}
                    />
                    <label
                      htmlFor={`send-ev-${i.eventId}`}
                      className="min-w-0 flex-1 cursor-pointer truncate"
                    >
                      <span className="tnum mr-1 text-text-muted">
                        {fmtJp(i.date)} {i.isMemo ? '📝' : `${i.start}〜${i.end}`}
                      </span>
                      <span className="font-medium text-text-primary">{i.title}</span>
                      <span className="ml-1 text-xs text-text-muted">（{i.staffName}）</span>
                    </label>
                  </li>
                ))}
              </ul>
            )}
            {unsendable.length > 0 && (
              <div className="rounded-md border border-border-default bg-bg-muted/40 p-2">
                <p className="mb-1 text-xs font-medium text-text-secondary">
                  送信できないイベント（{unsendable.length}）
                </p>
                <ul className="space-y-0.5">
                  {unsendable.map((i) => (
                    <li key={i.eventId} className="truncate text-xs text-text-muted">
                      {fmtJp(i.date)} {i.title}（{i.staffName}）— {i.reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {send.isPending && (
              <Alert>
                <AlertTitle>カイポケへ登録しています…</AlertTitle>
                <AlertDescription>
                  1件あたり約40秒かかります（{selectedCount}件）。このまましばらくお待ちください。
                </AlertDescription>
              </Alert>
            )}
          </div>
        )}

        {done && summary && (
          <div className="space-y-2" data-testid="send-events-result">
            <p className="text-sm text-text-primary">
              送信結果: 登録 <b>{summary.promoted ?? 0}</b> / 既存と統合{' '}
              <b>{summary.deduped ?? 0}</b> / 失敗{' '}
              <b className={summary.failed ? 'text-error' : ''}>{summary.failed ?? 0}</b>
            </p>
            {(summary.failed ?? 0) > 0 && (
              <p className="text-xs text-text-muted">
                失敗したイベントはらく助側に残っています。もう一度「カイポケへ送る」を
                実行すると失敗分だけ再送されます。
              </p>
            )}
          </div>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} disabled={send.isPending}>
            {done ? '閉じる' : 'やめる'}
          </Button>
          {!done && (
            <Button
              type="button"
              onClick={() => void handleSend()}
              disabled={selectedCount === 0 || send.isPending || items === null}
              data-testid="send-events-execute"
            >
              {send.isPending ? '送信中…' : `カイポケへ送る（${selectedCount}件）`}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
