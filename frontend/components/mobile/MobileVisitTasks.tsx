'use client';

/**
 * スマホの訪問詳細に出す「事務所からのお願い」と「事務所からの伝言」
 * (patient-tasks-design-2026-11-01 §3)。
 *
 * お願い = 項目のあるやること。四角を押すと完了、もう一度押すと戻せる (自分で
 * 完了にしたものだけ・BE の can_reopen)。伝言 = 項目の無いやること。「読みました」で完了。
 * 見られるのは担当の訪問だけ (BE が絞る・見えない訪問は 404 なので何も出さない)。
 *
 * `readOnly` (先の日の訪問・mobile-future-days) ではお願いの完了・戻すは押せない
 * (今日の訪問だけで押す)。伝言の「読みました」は先の日でも押せる (PO 2026-11-01)。
 */
import { ClipboardList, MessageSquare } from 'lucide-react';

import { toast } from '@/components/ui/sonner';
import { cn } from '@/lib/utils';
import { useCompleteTask, useReopenTask, useVisitTasks } from '@/lib/queries/tasks';
import type { PatientTaskRead } from '@/lib/schemas/patient-task';
import { RecurringMark } from '@/components/tasks/TaskPill';
import { taskActionErrorMessage, taskDoneLabel, taskMd } from '@/components/tasks/taskFormat';

export function MobileVisitTasks({
  visitId,
  readOnly = false,
}: {
  visitId: string;
  /** 先の日の訪問: お願いの完了を押せない (伝言の「読みました」は押せる)。 */
  readOnly?: boolean;
}) {
  const { data, isError } = useVisitTasks(visitId);
  const complete = useCompleteTask();
  const reopen = useReopenTask();
  const busy = complete.isPending || reopen.isPending;

  if (isError || !data || data.length === 0) return null;
  const requests = data.filter((t) => !t.is_message);
  const messages = data.filter((t) => t.is_message);

  const doComplete = (t: PatientTaskRead, okText: string, failText: string) =>
    complete.mutate(
      { taskId: t.id, visitId },
      {
        onSuccess: () => toast.success(okText),
        onError: (err) => toast.error(failText, { description: taskActionErrorMessage(err) }),
      },
    );

  const toggle = (t: PatientTaskRead) => {
    if (busy || readOnly) return;
    if (t.status !== 'done') {
      doComplete(t, '完了にしました', '完了にできませんでした');
      return;
    }
    if (!t.can_reopen) {
      toast.info('ほかの人が完了にしたものは戻せません');
      return;
    }
    reopen.mutate(t.id, {
      onSuccess: () => toast.success('完了を戻しました'),
      onError: (err) =>
        toast.error('戻せませんでした', { description: taskActionErrorMessage(err) }),
    });
  };

  return (
    <>
      {requests.length > 0 && (
        <section
          className="space-y-2 rounded-lg border border-border-default bg-bg-base p-4"
          data-testid="mobile-visit-requests"
        >
          <h2 className="m-0 flex items-center gap-1.5 text-base font-bold text-text-primary">
            <ClipboardList className="h-5 w-5 text-brand-primary" aria-hidden="true" />
            事務所からのお願い
          </h2>
          <ul className="m-0 list-none space-y-2 p-0">
            {requests.map((t) => {
              const done = t.status === 'done';
              return (
                <li key={t.id}>
                  <button
                    type="button"
                    role="checkbox"
                    aria-checked={done}
                    // ほかの人が完了にしたものは戻せない (押すと案内だけ出す)。
                    aria-disabled={done && !t.can_reopen ? true : undefined}
                    disabled={busy || readOnly}
                    onClick={() => toggle(t)}
                    className={cn(
                      'flex min-h-12 w-full items-start gap-3 rounded-md border px-3 py-2.5 text-left',
                      done
                        ? 'border-success bg-success-bg'
                        : 'border-border-default bg-bg-base active:bg-bg-muted',
                      readOnly && 'cursor-not-allowed opacity-70',
                    )}
                    data-testid={`mobile-task-${t.id}`}
                  >
                    <span
                      className={cn(
                        'mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded border-2 text-sm font-bold',
                        done
                          ? 'border-success bg-success text-white'
                          : 'border-brand-primary bg-bg-base',
                      )}
                      aria-hidden="true"
                    >
                      {done ? '✓' : ''}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span
                        className={cn(
                          'block text-[15px] font-bold text-text-primary',
                          done && 'text-text-secondary line-through',
                        )}
                      >
                        {t.item_type_name ?? '（項目なし）'}{' '}
                        <RecurringMark month={t.recurring_month} />
                      </span>
                      {t.memo && (
                        <span className="mt-0.5 block whitespace-pre-wrap text-sm text-text-primary">
                          {t.memo}
                        </span>
                      )}
                      <span
                        className={cn(
                          'mt-0.5 block text-xs',
                          !done && t.carried_over ? 'font-bold text-error' : 'text-text-muted',
                        )}
                      >
                        {done
                          ? `完了 ${taskDoneLabel(t)}`
                          : t.carried_over
                            ? `${taskMd(t.start_date)} からの持ち越し`
                            : `${taskMd(t.start_date)} から`}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          <p className="m-0 text-xs text-text-muted" data-testid="mobile-visit-requests-hint">
            {readOnly
              ? '先の日の訪問です。完了は当日の訪問で押せます。'
              : '四角を押すと完了。もう一度押すと戻せます。'}
          </p>
        </section>
      )}

      {messages.length > 0 && (
        <section
          className="space-y-2 rounded-lg border border-border-default bg-bg-base p-4"
          data-testid="mobile-visit-messages"
        >
          <h2 className="m-0 flex items-center gap-1.5 text-base font-bold text-text-primary">
            <MessageSquare className="h-5 w-5 text-brand-primary" aria-hidden="true" />
            事務所からの伝言
          </h2>
          {messages.map((t) => (
            <div
              key={t.id}
              className="rounded-md border border-border-default bg-bg-muted px-3 py-2.5"
              data-testid={`mobile-message-${t.id}`}
            >
              <p className="m-0 whitespace-pre-wrap text-[15px] text-text-primary">{t.memo}</p>
              <p className="m-0 mt-1 text-xs text-text-muted">
                {[t.created_by_name, taskMd(t.start_date)].filter(Boolean).join('・')}{' '}
                <RecurringMark month={t.recurring_month} />
              </p>
              <div className="mt-2">
                {t.status === 'done' ? (
                  <span className="inline-block rounded-full bg-success-bg px-2 py-0.5 text-xs font-bold text-success">
                    読みました {taskDoneLabel(t)}
                  </span>
                ) : (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => doComplete(t, '読みました', '読みましたにできませんでした')}
                    className="inline-flex min-h-11 items-center rounded-md bg-brand-primary px-4 text-sm font-bold text-white active:bg-brand-primary-hover disabled:opacity-50"
                    data-testid={`mobile-message-read-${t.id}`}
                  >
                    読みました
                  </button>
                )}
              </div>
            </div>
          ))}
        </section>
      )}
    </>
  );
}
