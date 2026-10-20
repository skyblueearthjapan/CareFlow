'use client';

/**
 * 「カイポケへ送る」のダイアログ (設計 §7-4-4)。
 *
 * 送る前の一覧 (職員・日付・件名・時刻・送れない理由) を出し、送れる休みだけをジョブで送る。
 * 送っている間は数秒おきに進み具合を読み、終わったら送れた・送れなかった・飛ばした件数を出す。
 * 照合がまだの月 (blocked_reason) は送れない。送ったあとに変わった休みは「カイポケで要対応」。
 */
import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

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
import { cn } from '@/lib/utils';
import { fmtDayJp, fmtMonthJp, LEAVE_LABEL } from '@/lib/leave-sheet';
import { invalidateAfterLeave, leaveErrorMessage } from '@/lib/queries/leave';
import { useOutboundJob, useOutboundPreview, useStartOutbound } from '@/lib/queries/leaveKaipoke';
import { isJobActive } from '@/lib/schemas/leaveKaipoke';

const OUTCOME_LABEL: Record<string, string> = {
  sent: '送りました',
  failed: '送れませんでした',
  skipped_changed: '送る前に変わったので飛ばしました',
};

export interface LeaveOutboundDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  month: string;
}

export function LeaveOutboundDialog({ open, onOpenChange, month }: LeaveOutboundDialogProps) {
  const qc = useQueryClient();
  const preview = useOutboundPreview(month, open);
  const start = useStartOutbound();
  const [jobId, setJobId] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const job = useOutboundJob(jobId);
  const status = job.data;
  const sending = start.isPending || (!!jobId && (!status || isJobActive(status.job)));

  // 終わったら 1 回だけ読み直す (送信の状態・月の表)
  const handled = useRef<string | null>(null);
  useEffect(() => {
    if (!jobId || !status || isJobActive(status.job) || handled.current === jobId) return;
    handled.current = jobId;
    void invalidateAfterLeave(qc);
  }, [jobId, status, qc]);

  const p = preview.data;
  const evening = warning ?? p?.evening_warning ?? null;
  const canSend = !!p && !p.blocked_reason && p.sendable_count > 0 && !sending && !jobId;

  const doStart = () => {
    start
      .mutateAsync(month)
      .then((r) => {
        setJobId(r.job_id);
        setWarning(r.evening_warning ?? null);
      })
      .catch((e: unknown) => toast.error(leaveErrorMessage(e, 'カイポケへ送れませんでした')));
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        // 送っている間も閉じてよい (ジョブは続く)。開き直したら新しい一覧から
        if (!o && !sending) setJobId(null);
        onOpenChange(o);
      }}
    >
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-[720px]">
        <DialogHeader>
          <DialogTitle>{fmtMonthJp(month)}の休みをカイポケへ送る</DialogTitle>
          <DialogDescription>
            送るのは「未送信」の休みだけです。照合前の休みと、送ったあとに変わった休みは送りません。
          </DialogDescription>
        </DialogHeader>

        {evening && (
          <p role="note" className="rounded-md bg-warning-bg px-3 py-2 text-sm text-warning-strong">
            {evening}
          </p>
        )}

        {preview.isError ? (
          <p className="text-sm text-error">
            {leaveErrorMessage(preview.error, '送る前の一覧を読み込めませんでした')}
          </p>
        ) : !p ? (
          <p className="text-sm text-text-muted">送る前の一覧を読み込んでいます</p>
        ) : (
          <div className="space-y-3 text-sm">
            {p.blocked_reason && (
              <p role="alert" className="rounded-md bg-warning-bg px-3 py-2 text-warning-strong">
                {p.blocked_reason}
              </p>
            )}
            <p className="text-text-secondary" data-testid="outbound-counts">
              送れる休み {p.sendable_count} 件・照合前 {p.baseline_count} 件・送信済み{' '}
              {p.sent_count} 件
            </p>
            {p.items.length === 0 ? (
              <p className="text-text-secondary">送る予定の休みはありません。</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm" aria-label="送る前の一覧">
                  <thead className="text-left text-xs text-text-muted">
                    <tr>
                      <th className="px-2 py-1 font-medium">職員</th>
                      <th className="px-2 py-1 font-medium">日付</th>
                      <th className="px-2 py-1 font-medium">種類</th>
                      <th className="px-2 py-1 font-medium">件名・時刻</th>
                      <th className="px-2 py-1 font-medium">送れるか</th>
                    </tr>
                  </thead>
                  <tbody>
                    {p.items.map((it) => (
                      <tr
                        key={`${it.staff_id}|${it.date}`}
                        data-testid="outbound-item"
                        className="border-t border-border-default"
                      >
                        <td className="whitespace-nowrap px-2 py-1">{it.staff_name}</td>
                        <td className="whitespace-nowrap px-2 py-1 tnum">{fmtDayJp(it.date)}</td>
                        <td className="whitespace-nowrap px-2 py-1">
                          {LEAVE_LABEL[it.leave_type]}
                        </td>
                        <td className="px-2 py-1">
                          {it.title}
                          {it.start || it.end ? ` ${it.start ?? ''}〜${it.end ?? ''}` : ''}
                        </td>
                        <td
                          className={cn('px-2 py-1', it.sendable ? 'text-success' : 'text-error')}
                        >
                          {it.sendable ? '送る' : (it.reason ?? '送れません')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {p.needs_action.length > 0 && (
              <div className="rounded-md bg-bg-muted px-3 py-2">
                <p className="font-bold text-text-primary">カイポケで要対応（送りません）</p>
                <ul className="list-disc pl-5">
                  {p.needs_action.map((n) => (
                    <li key={`${n.staff_id}|${n.date}`}>
                      {n.staff_name} {fmtDayJp(n.date)}：{n.message}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {(sending || status) && (
          <div role="status" data-testid="outbound-progress" className="space-y-1 text-sm">
            {sending ? (
              <p className="rounded-md bg-bg-muted px-3 py-2 text-text-secondary">
                カイポケへ送信中
                {status ? `（${status.job.done}/${status.job.total ?? '?'} 週）` : ''}
              </p>
            ) : status ? (
              <>
                <p
                  className={cn(
                    'rounded-md px-3 py-2',
                    status.failed_count > 0 || status.job.status === 'failed'
                      ? 'bg-warning-bg text-warning-strong'
                      : 'bg-success-bg text-success',
                  )}
                >
                  送れた {status.sent_count} 件・送れなかった {status.failed_count} 件・飛ばした{' '}
                  {status.skipped_count} 件{status.job.error ? `（${status.job.error}）` : ''}
                </p>
                <ul className="list-disc pl-5 text-text-secondary">
                  {status.items
                    .filter((it) => it.outcome !== 'sent')
                    .map((it) => (
                      <li key={`${it.staff_id}|${it.date}`}>
                        {it.staff_name ?? ''} {fmtDayJp(it.date)}：
                        {OUTCOME_LABEL[it.outcome] ?? it.outcome}
                        {it.reason ? `（${it.reason}）` : ''}
                      </li>
                    ))}
                </ul>
              </>
            ) : null}
          </div>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            閉じる
          </Button>
          <Button type="button" disabled={!canSend} onClick={doStart}>
            カイポケへ送る（{p?.sendable_count ?? 0} 件）
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
