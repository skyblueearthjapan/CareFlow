'use client';

/**
 * スマホの今日の画面の上に出す「預かっているやること N 件」の帯
 * (patient-tasks-hold-first-visitor-design-2026-11-04 §3・モックのスマホ)。
 *
 * 最初の訪問で済まなかった「預かる」項目のやることは、その訪問の主担当が預かり、ほかの訪問には
 * 出ない (同じ看護師の次の訪問にも出さない・§7-1)。ここだけに出し、訪問がない日でも完了にできる
 * (訪問を指さずに完了・完了の訪問は BE が最初の訪問にする)。0 件なら出さない。
 */
import { ClipboardList } from 'lucide-react';

import { toast } from '@/components/ui/sonner';
import { useCompleteTask, useHeldTasks } from '@/lib/queries/tasks';
import { taskActionErrorMessage, taskMd } from '@/components/tasks/taskFormat';

export function HeldTasksBand() {
  const { data } = useHeldTasks();
  const complete = useCompleteTask();
  // 補助情報なので、読めないときは何も出さない。
  const tasks = (data ?? []).filter((t) => t.status === 'open');
  if (tasks.length === 0) return null;

  return (
    <div
      className="rounded-md border border-[#b9d3f5] bg-[#e3eefc] px-3 py-2.5 text-sm text-text-primary"
      data-testid="held-tasks-band"
    >
      <p className="m-0 flex flex-wrap items-center gap-1.5">
        <ClipboardList className="h-4 w-4 shrink-0 text-[#1565c0]" aria-hidden="true" />
        <b className="text-[#1565c0]">預かっているやること {tasks.length} 件</b>
      </p>
      <p className="m-0 mt-0.5 text-xs text-text-secondary">
        前の訪問で済まなかった分です。訪問がない日でも完了にできます。
      </p>
      <ul className="m-0 mt-1.5 list-none space-y-1.5 p-0">
        {tasks.map((t) => (
          <li
            key={t.id}
            className="flex items-center gap-2 rounded-md bg-bg-base px-3 py-2"
            data-testid={`held-task-${t.id}`}
          >
            <div className="min-w-0 flex-1">
              <b className="block text-[15px] text-text-primary">
                {t.is_message ? (t.memo ?? '伝言') : (t.item_type_name ?? '（項目なし）')}
              </b>
              {!t.is_message && t.memo && (
                <span className="block whitespace-pre-wrap text-xs text-text-primary">
                  {t.memo}
                </span>
              )}
              <span className="block text-xs text-text-muted">
                {[
                  t.patient_name ? `${t.patient_name} 様` : null,
                  t.held_by ? `${taskMd(t.held_by.visit_date)} の訪問から` : null,
                ]
                  .filter(Boolean)
                  .join('・')}
              </span>
            </div>
            <button
              type="button"
              disabled={complete.isPending}
              onClick={() =>
                complete.mutate(
                  { taskId: t.id, visitId: null },
                  {
                    onSuccess: () => toast.success('完了にしました'),
                    onError: (err) =>
                      toast.error('完了にできませんでした', {
                        description: taskActionErrorMessage(err),
                      }),
                  },
                )
              }
              className="inline-flex min-h-11 shrink-0 items-center rounded-md bg-brand-primary px-4 text-sm font-bold text-white active:bg-brand-primary-hover disabled:opacity-50"
              data-testid={`held-task-complete-${t.id}`}
            >
              完了
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
