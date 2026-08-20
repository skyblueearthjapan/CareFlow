'use client';

/**
 * ImportEventsFromKaipokeDialog — カイポケからイベントのみ取り込む (職員スケジュールタブ)。
 *
 * 連携ページの「イベントのみ取込」(Phase 1) と同じ BE (events-inbound preview/apply) を
 * スケジュール画面から直接使えるようにした対称ボタン (「カイポケへ送る」の逆方向)。
 * 訪問の予定には一切触れない。開いたら自動で取得を開始する (約1分)。
 */
import * as React from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/components/ui/sonner';
import { EventVisitConflictNotice } from './EventVisitConflictNotice';
import {
  useApplyEventsInbound,
  useEventsInboundPreview,
} from '@/lib/queries/integrations';
import type { EventsInboundPreview } from '@/lib/schemas/integration';

const WEEKDAYS_JP = ['日', '月', '火', '水', '木', '金', '土'] as const;

function fmtJp(iso: string): string {
  const d = new Date(iso + 'T00:00:00');
  return `${d.getMonth() + 1}/${d.getDate()}（${WEEKDAYS_JP[d.getDay()]}）`;
}

const ACTION_META: Record<string, { label: string; cls: string }> = {
  add: { label: '追加', cls: 'bg-info-bg text-info' },
  update: { label: '変更', cls: 'bg-warning-bg text-warning-strong' },
  delete: { label: '削除', cls: 'bg-error-bg text-error' },
};

interface ImportEventsFromKaipokeDialogProps {
  open: boolean;
  onClose: () => void;
  /** 対象週の月曜 (YYYY-MM-DD)。 */
  weekStartIso: string;
}

export function ImportEventsFromKaipokeDialog({
  open,
  onClose,
  weekStartIso,
}: ImportEventsFromKaipokeDialogProps) {
  const preview = useEventsInboundPreview();
  const apply = useApplyEventsInbound();
  const [plan, setPlan] = React.useState<EventsInboundPreview | null>(null);
  const [fetchError, setFetchError] = React.useState<string | null>(null);
  const [applied, setApplied] = React.useState(false);

  // 開いたら自動で取得開始 (連携ページの❶と同じ start→ポーリング・約1分)
  React.useEffect(() => {
    if (!open) return;
    setPlan(null);
    setFetchError(null);
    setApplied(false);
    preview
      .mutateAsync({ weekStart: weekStartIso })
      .then(setPlan)
      .catch((e) => setFetchError(e instanceof Error ? e.message : '取得に失敗しました'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, weekStartIso]);

  const hasChanges = (plan?.changes.length ?? 0) > 0;

  async function handleApply() {
    if (!plan || !hasChanges || apply.isPending) return;
    try {
      const res = await apply.mutateAsync({
        weekStart: weekStartIso,
        dryRun: false,
        changes: plan.changes,
      });
      setApplied(true);
      toast.success(
        `イベントを取り込みました — 追加 ${res.added} / 変更 ${res.updated} / 削除 ${res.deleted}` +
          (res.failed > 0 ? ` / 失敗 ${res.failed}` : ''),
      );
    } catch (e) {
      toast.error('取り込みに失敗しました', {
        description: e instanceof Error ? e.message : String(e),
      });
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && (preview.isPending || apply.isPending)) return;
        if (!next) onClose();
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>カイポケからイベントを取り込む</DialogTitle>
          <DialogDescription>
            この週のカイポケの個別業務（イベント）だけを取り込みます。
            <b>訪問の予定には一切触れません</b>。カイポケ側が正として週全体を揃えます。
          </DialogDescription>
        </DialogHeader>

        {preview.isPending && (
          <Alert>
            <AlertTitle>カイポケから取得しています…</AlertTitle>
            <AlertDescription>約1分かかります。このままお待ちください。</AlertDescription>
          </Alert>
        )}
        {fetchError && (
          <Alert variant="destructive">
            <AlertTitle>取得に失敗しました</AlertTitle>
            <AlertDescription>{fetchError}</AlertDescription>
          </Alert>
        )}

        {plan && !applied && (
          <div className="space-y-2" data-testid="import-events-plan">
            <p className="text-sm text-text-secondary">
              取得 {plan.fetchedTotal} 件 — 追加 <b>{plan.adds}</b> / 変更 <b>{plan.updates}</b> /
              削除 <b>{plan.deletes}</b>
              {plan.sundaySkipped > 0 && (
                <span className="ml-1 text-xs text-text-muted">
                  （日曜 {plan.sundaySkipped} 件はスキップ）
                </span>
              )}
            </p>
            {!hasChanges && (
              <p className="text-sm text-text-muted">
                差分はありません — らく助のイベントはカイポケと同期済みです。
              </p>
            )}
            {hasChanges && (
              <ul className="max-h-64 space-y-1 overflow-y-auto pr-1">
                {plan.changes.map((c) => {
                  const meta = ACTION_META[c.action] ?? ACTION_META.add!;
                  return (
                    <li key={`${c.action}-${c.externalId}`} className="flex items-center gap-2 text-xs">
                      <span
                        className={`shrink-0 rounded px-1.5 py-0.5 font-medium ${meta.cls}`}
                      >
                        {meta.label}
                      </span>
                      <span className="tnum text-text-muted">
                        {fmtJp(c.date)} {c.start === c.end ? '📝' : `${c.start}〜${c.end}`}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-text-primary">
                        {c.title || '(無題)'}
                      </span>
                      <span className="shrink-0 text-text-muted">{c.staffName}</span>
                    </li>
                  );
                })}
              </ul>
            )}
            <EventVisitConflictNotice conflicts={plan.conflicts} />
            {plan.unmatched.length > 0 && (
              <p className="text-xs text-warning-strong">
                ⚠ らく助に未登録の職員の予定 {plan.unmatched.reduce((a, u) => a + u.count, 0)}
                件は取り込み対象外です（
                {plan.unmatched.map((u) => u.staffName).join('・')}）
              </p>
            )}
          </div>
        )}

        {applied && (
          <div className="space-y-2">
            <p className="text-sm text-text-primary" data-testid="import-events-done">
              取り込みが完了しました。盤面のイベント帯に反映されています。
            </p>
            {plan && <EventVisitConflictNotice conflicts={plan.conflicts} />}
          </div>
        )}

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={onClose}
            disabled={preview.isPending || apply.isPending}
          >
            {applied ? '閉じる' : 'やめる'}
          </Button>
          {!applied && (
            <Button
              type="button"
              onClick={() => void handleApply()}
              disabled={!plan || !hasChanges || apply.isPending || preview.isPending}
              data-testid="import-events-apply"
            >
              {apply.isPending ? '取り込み中…' : `取り込む（${plan?.changes.length ?? 0}件）`}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
