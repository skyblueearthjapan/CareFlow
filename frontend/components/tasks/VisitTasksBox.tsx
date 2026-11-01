'use client';

/**
 * 訪問モニターの詳細に出す「事務所からのやること」の枠 (管理者だけ・設計 §2-6 / §3)。
 *
 * その訪問に出るやること (患者 + 日付で持ち、出す訪問は BE が決める) を並べ、
 * 完了にする／完了を戻す／取り消す ができる。下の「＋ やることを付ける」で
 * 項目 (いくつでも)・伝言・日付 (既定はその訪問の日) を選んで付ける。
 */
import { useState } from 'react';
import { ClipboardList, Plus } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/sonner';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import {
  useCancelTask,
  useCompleteTask,
  useCreateTasks,
  useReopenTask,
  useTaskItemTypes,
  useVisitTasks,
} from '@/lib/queries/tasks';
import type { PatientTaskRead } from '@/lib/schemas/patient-task';

import { TaskItemChips, canSubmitTask, toggleId } from './TaskItemChips';
import { Pill } from './TaskPill';
import {
  taskActionErrorMessage,
  taskDoneLabel,
  taskMd,
  taskTitle,
  visitBriefLabel,
} from './taskFormat';

interface VisitTasksBoxProps {
  visitId: string;
  /** その訪問の日 (YYYY-MM-DD)。付けるときの日付の既定。 */
  visitDate: string;
  patientId: string;
  patientName: string | null;
}

export function VisitTasksBox({ visitId, visitDate, patientId, patientName }: VisitTasksBoxProps) {
  const tasksQuery = useVisitTasks(visitId);
  const tasks = tasksQuery.data ?? [];
  const complete = useCompleteTask();
  const reopen = useReopenTask();
  const cancel = useCancelTask();
  const busy = complete.isPending || reopen.isPending || cancel.isPending;

  const onError = (fallback: string) => (err: unknown) =>
    toast.error(fallback, { description: taskActionErrorMessage(err) });

  return (
    <div
      className="mb-3 rounded-lg border border-border-default bg-bg-base p-3"
      data-testid="visit-tasks-box"
    >
      <h4 className="m-0 mb-2 flex items-center gap-1.5 text-[13px] font-bold text-text-primary">
        <ClipboardList className="h-4 w-4 text-brand-primary" aria-hidden="true" />
        事務所からのやること
      </h4>

      {tasksQuery.isLoading ? (
        <p className="text-xs text-text-muted">読み込み中…</p>
      ) : tasksQuery.isError ? (
        <p className="text-xs text-error">やることを読み込めませんでした。</p>
      ) : tasks.length === 0 ? (
        <p className="text-xs text-text-muted" data-testid="visit-tasks-empty">
          この訪問へのやることは、まだありません。
        </p>
      ) : (
        <ul className="m-0 list-none space-y-2 p-0">
          {tasks.map((t) => (
            <TaskRow
              key={t.id}
              task={t}
              busy={busy}
              onComplete={() =>
                complete.mutate(
                  { taskId: t.id, visitId },
                  {
                    onSuccess: () => toast.success('完了にしました'),
                    onError: onError('完了にできませんでした'),
                  },
                )
              }
              onReopen={() =>
                reopen.mutate(t.id, {
                  onSuccess: () => toast.success('完了を戻しました'),
                  onError: onError('完了を戻せませんでした'),
                })
              }
              onCancel={() => {
                if (!window.confirm(`「${taskTitle(t)}」を取り消しますか？`)) return;
                cancel.mutate(t.id, {
                  onSuccess: () => toast.success('取り消しました'),
                  onError: onError('取り消せませんでした'),
                });
              }}
            />
          ))}
        </ul>
      )}

      <AddTaskInline
        // 訪問を選び直したら入力を空にする。
        key={visitId}
        visitDate={visitDate}
        patientId={patientId}
        patientName={patientName}
      />
    </div>
  );
}

function TaskRow({
  task: t,
  busy,
  onComplete,
  onReopen,
  onCancel,
}: {
  task: PatientTaskRead;
  busy: boolean;
  onComplete: () => void;
  onReopen: () => void;
  onCancel: () => void;
}) {
  const done = t.status === 'done';
  return (
    <li
      className="flex items-start gap-2 rounded-md border border-border-default p-2"
      data-testid={`visit-task-${t.id}`}
      data-status={t.status}
    >
      <div className="min-w-0 flex-1 text-xs text-text-primary">
        <div className="flex flex-wrap items-center gap-1">
          {t.is_message && <Pill tone="msg">伝言</Pill>}
          {done ? (
            <Pill tone="ok">完了 {taskDoneLabel(t)}</Pill>
          ) : (
            <>
              {t.carried_over && <Pill tone="carry">持ち越し</Pill>}
              {t.overdue && <Pill tone="over">期日 {taskMd(t.start_date)} を過ぎています</Pill>}
            </>
          )}
          {!t.is_message && <b className="text-[13px]">{taskTitle(t)}</b>}
        </div>
        {t.memo && <p className="m-0 mt-0.5 whitespace-pre-wrap">{t.memo}</p>}
        <p className="m-0 mt-0.5 text-[11px] text-text-muted">
          日付 {taskMd(t.start_date)}
          {t.created_by_name ? `・付けた人 ${t.created_by_name}` : ''}
        </p>
      </div>
      <div className="flex shrink-0 flex-col gap-1">
        {done ? (
          t.can_reopen && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={onReopen}
              className="h-8 px-2 text-xs"
            >
              完了を戻す
            </Button>
          )
        ) : (
          <>
            <Button
              type="button"
              size="sm"
              disabled={busy}
              onClick={onComplete}
              className="h-8 px-2 text-xs"
            >
              完了にする
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={onCancel}
              className="h-8 px-2 text-xs text-text-secondary"
            >
              取り消す
            </Button>
          </>
        )}
      </div>
    </li>
  );
}

function AddTaskInline({
  visitDate,
  patientId,
  patientName,
}: {
  visitDate: string;
  patientId: string;
  patientName: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [types, setTypes] = useState<string[]>([]);
  const [memo, setMemo] = useState('');
  const [date, setDate] = useState(visitDate);
  const itemTypes = useTaskItemTypes({ enabled: open });
  const create = useCreateTasks();
  const ok = canSubmitTask(types, memo) && /^\d{4}-\d{2}-\d{2}$/.test(date);

  if (!open) {
    return (
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setOpen(true)}
        className="mt-2 h-9 w-full gap-1 border-brand-primary-light text-[13px] font-bold text-brand-primary-hover hover:bg-brand-primary-50"
        data-testid="visit-tasks-add-open"
      >
        <Plus className="h-4 w-4" aria-hidden="true" />
        やることを付ける
      </Button>
    );
  }

  const submit = () => {
    if (!ok) return;
    create.mutate(
      {
        patient_id: patientId,
        start_date: date,
        item_type_ids: types,
        memo: memo.trim() || null,
      },
      {
        onSuccess: (res) => {
          toast.success(`${patientName ?? '患者'} 様に ${res.tasks.length} 件を付けました`, {
            description: res.next_visit
              ? `出す訪問：${visitBriefLabel(res.next_visit)}`
              : '訪問が入ったら出ます',
          });
          setTypes([]);
          setMemo('');
          setOpen(false);
        },
        onError: (err) =>
          toast.error('やることを付けられませんでした', { description: apiErrorMessage(err) }),
      },
    );
  };

  return (
    <div
      className="mt-3 space-y-2 border-t border-border-default pt-3"
      data-testid="visit-tasks-add"
    >
      <p className="m-0 text-[13px] font-bold text-text-primary">＋ やることを付ける</p>
      <p className="m-0 text-xs text-text-secondary">項目を選ぶ（いくつでも）</p>
      <TaskItemChips
        items={(itemTypes.data ?? []).filter((x) => x.is_active)}
        selected={types}
        onToggle={(id) => setTypes((a) => toggleId(a, id))}
        disabled={create.isPending}
      />
      <label className="block text-xs text-text-secondary">
        伝言（任意）
        <textarea
          value={memo}
          onChange={(e) => setMemo(e.target.value)}
          rows={2}
          maxLength={2000}
          placeholder="例：デスクの上に資料を置いています。持っていってください。"
          className="mt-1 block w-full resize-y rounded-md border border-border-default bg-bg-base px-2 py-1.5 text-sm text-text-primary"
          data-testid="visit-tasks-memo"
        />
      </label>
      <label className="block text-xs text-text-secondary">
        日付（この日の訪問に出します。終わらなければ次の訪問へ持ち越し）
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className="mt-1 block h-9 rounded-md border border-border-default bg-bg-base px-2 text-sm text-text-primary"
          data-testid="visit-tasks-date"
        />
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          disabled={!ok || create.isPending}
          onClick={submit}
          className="h-9 px-4 text-[13px] font-bold"
          data-testid="visit-tasks-add-submit"
        >
          付ける
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setOpen(false)}
          className="h-9 px-3 text-[13px]"
        >
          閉じる
        </Button>
        <span className="text-[11px] text-text-muted">
          付けると、その日の担当のスマホとベルに出ます
        </span>
      </div>
    </div>
  );
}
