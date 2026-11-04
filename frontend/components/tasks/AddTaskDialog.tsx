'use client';

/**
 * やることのページの「＋ やることを付ける」(管理者・設計 §2-6)。
 *
 * 患者様・日付 (この日から出す)・項目 (いくつでも)・伝言・期日 (任意・
 * patient-tasks-due-design-2026-11-04 §2・§6-1) を選んで付ける。期日が日付より前なら付けられない。
 * 付けた結果の next_visit で「出す訪問」を見せる (無ければ「訪問が入ったら出ます」)。
 * 文字と操作の大きさはモーダルの可読性の基準 (add-visit-anywhere-design §3-5) に合わせる。
 */
import { useRef, useState } from 'react';

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
import { PatientCombobox } from '@/components/master/PatientCombobox';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { cn } from '@/lib/utils';
import { useCreateTasks, useTaskItemTypes } from '@/lib/queries/tasks';
import type { PatientTaskCreateResult } from '@/lib/schemas/patient-task';

import { TaskItemChips, canSubmitTask, toggleId } from './TaskItemChips';
import { dueQuickPicks, taskMd, taskMdWd, visitBriefLabel } from './taskFormat';

const YMD = /^\d{4}-\d{2}-\d{2}$/;

interface AddTaskDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 日付の既定 (今日・YYYY-MM-DD)。 */
  defaultDate: string;
}

export function AddTaskDialog({ open, onOpenChange, defaultDate }: AddTaskDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl" data-testid="add-task-dialog">
        {/* 開くたびに入力を空から始める。 */}
        {open && <AddTaskForm defaultDate={defaultDate} onClose={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}

export function AddTaskForm({
  defaultDate,
  onClose,
}: {
  defaultDate: string;
  onClose: () => void;
}) {
  const [patientId, setPatientId] = useState('');
  const [date, setDate] = useState(defaultDate);
  const [types, setTypes] = useState<string[]>([]);
  const [memo, setMemo] = useState('');
  // 期日: dueOn = 「日付を決める」を選んだ。due = 選んだ日 (空 = まだ選んでいない)。
  const [dueOn, setDueOn] = useState(false);
  const [due, setDue] = useState('');
  const [result, setResult] = useState<PatientTaskCreateResult | null>(null);
  const itemTypes = useTaskItemTypes();
  const create = useCreateTasks();
  // 二度押しで同じやることを 2 回付けないための送信中の旗 (isPending は次の描画まで立たない)。
  const sendingRef = useRef(false);

  const dateOk = YMD.test(date);
  const dueOk = YMD.test(due);
  const noDue = dueOn && !dueOk;
  // 期日が日付より前 (PO 2026-11-04 §6-1: 赤い注意を出して付けられなくする・BE も 422)。
  const badDue = dueOn && dueOk && dateOk && due < date;
  const ok = !!patientId && dateOk && canSubmitTask(types, memo) && !noDue && !badDue;
  const missing = [
    !patientId ? '患者様' : null,
    !dateOk ? '日付' : null,
    !canSubmitTask(types, memo) ? '項目か伝言' : null,
    noDue ? '期日' : null,
  ].filter((x): x is string => x !== null);
  const pickDue = (d: string) => {
    setDueOn(true);
    setDue(d);
  };

  const submit = () => {
    if (!ok || create.isPending || sendingRef.current) return;
    sendingRef.current = true;
    create.mutate(
      {
        patient_id: patientId,
        start_date: date,
        item_type_ids: types,
        memo: memo.trim() || null,
        due_date: dueOn ? due : null,
      },
      {
        onSuccess: (res) => {
          setResult(res);
          toast.success(
            `${res.tasks[0]?.patient_name ?? '患者'} 様に ${res.tasks.length} 件を付けました`,
          );
        },
        onError: (err) =>
          toast.error('やることを付けられませんでした', { description: apiErrorMessage(err) }),
        onSettled: () => {
          sendingRef.current = false;
        },
      },
    );
  };

  if (result) {
    const name = result.tasks[0]?.patient_name ?? '患者';
    return (
      <div className="space-y-4" data-testid="add-task-result">
        <DialogHeader>
          <DialogTitle className="text-lg">
            {name} 様に {result.tasks.length} 件を付けました
          </DialogTitle>
          <DialogDescription className="text-sm">
            出す訪問の担当のスマホとベルに出ます。
          </DialogDescription>
        </DialogHeader>
        <div className="rounded-lg border border-border-default bg-bg-muted p-4 text-base">
          出す訪問：
          {result.next_visit ? (
            <b data-testid="add-task-next-visit">{visitBriefLabel(result.next_visit)}</b>
          ) : (
            <span className="font-bold text-warning-strong" data-testid="add-task-no-visit">
              訪問が入ったら出ます
            </span>
          )}
          <div className="mt-1 text-sm" data-testid="add-task-result-due">
            期日：
            {result.tasks[0]?.due_date ? `${taskMdWd(result.tasks[0].due_date)} まで` : 'なし'}
          </div>
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              setResult(null);
              setTypes([]);
              setMemo('');
            }}
          >
            続けて付ける
          </Button>
          <Button type="button" onClick={onClose}>
            閉じる
          </Button>
        </DialogFooter>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <DialogHeader>
        <DialogTitle className="text-lg">やることを付ける</DialogTitle>
        <DialogDescription className="text-sm">
          選んだ日から、その方の最初の訪問に出します。終わらなければ次の訪問へ持ち越します。
        </DialogDescription>
      </DialogHeader>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="space-y-1.5">
          <span className="block text-sm font-semibold text-text-primary">患者様</span>
          <PatientCombobox
            value={patientId}
            onChange={setPatientId}
            placeholder="患者様を選ぶ"
            className="h-10 w-full text-sm"
          />
        </div>
        <label className="block space-y-1.5">
          <span className="block text-sm font-semibold text-text-primary">
            日付（この日から出す）
          </span>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="block h-10 w-full rounded-md border border-border-default bg-bg-base px-3 text-sm text-text-primary"
            data-testid="add-task-date"
          />
        </label>
      </div>

      <div className="space-y-2">
        <span className="block text-sm font-semibold text-text-primary">項目（いくつでも）</span>
        <TaskItemChips
          items={(itemTypes.data ?? []).filter((x) => x.is_active)}
          selected={types}
          onToggle={(id) => setTypes((a) => toggleId(a, id))}
          disabled={create.isPending}
        />
      </div>

      <label className="block space-y-1.5">
        <span className="block text-sm font-semibold text-text-primary">伝言（任意）</span>
        <textarea
          value={memo}
          onChange={(e) => setMemo(e.target.value)}
          rows={3}
          maxLength={2000}
          placeholder="例：デスクの上に資料を置いています。持っていってください。"
          className="block w-full resize-y rounded-md border border-border-default bg-bg-base px-3 py-2 text-sm text-text-primary"
          data-testid="add-task-memo"
        />
      </label>

      <div className="space-y-1.5">
        <span className="block text-sm font-semibold text-text-primary">
          期日
          <span className="ml-1 font-normal text-text-muted">
            （いつまでに・空欄でも付けられます）
          </span>
        </span>
        <div
          className="space-y-2 rounded-lg border border-border-default bg-bg-muted p-3"
          data-testid="add-task-due"
        >
          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex overflow-hidden rounded-md border border-border-default">
              {(
                [
                  [false, 'なし'],
                  [true, '日付を決める'],
                ] as const
              ).map(([v, label]) => (
                <button
                  key={label}
                  type="button"
                  aria-pressed={dueOn === v}
                  onClick={() => setDueOn(v)}
                  className={cn(
                    'h-9 border-l border-border-default px-3 text-sm first:border-l-0',
                    dueOn === v
                      ? 'bg-brand-primary font-bold text-white'
                      : 'bg-bg-base text-text-primary',
                  )}
                  data-testid={v ? 'add-task-due-on' : 'add-task-due-none'}
                >
                  {label}
                </button>
              ))}
            </div>
            <input
              type="date"
              aria-label="期日"
              value={due}
              min={dateOk ? date : undefined}
              disabled={!dueOn}
              onChange={(e) => pickDue(e.target.value)}
              className="block h-9 w-44 rounded-md border border-border-default bg-bg-base px-3 text-sm text-text-primary disabled:bg-bg-muted disabled:text-text-muted"
              data-testid="add-task-due-date"
            />
          </div>
          {dateOk && (
            <div className="flex flex-wrap gap-1.5">
              {dueQuickPicks(date).map((q) => (
                <button
                  key={q.label}
                  type="button"
                  aria-pressed={dueOn && due === q.date}
                  onClick={() => pickDue(q.date)}
                  className={cn(
                    'rounded-full border px-3 py-1 text-xs',
                    dueOn && due === q.date
                      ? 'border-brand-primary bg-brand-primary-50 font-bold text-brand-primary-hover'
                      : 'border-dashed border-brand-primary bg-bg-base text-brand-primary-hover',
                  )}
                  data-testid={`add-task-due-quick-${q.label}`}
                >
                  {q.label}（{taskMdWd(q.date)}）
                </button>
              ))}
            </div>
          )}
          {badDue ? (
            <p
              className="m-0 rounded-md bg-error-bg px-3 py-2 text-xs font-bold text-error"
              role="alert"
              data-testid="add-task-due-before"
            >
              期日（{taskMdWd(due)}）が、日付（{taskMdWd(date)}
              ）より前です。日付と同じ日か、後の日を選んでください。
            </p>
          ) : noDue ? (
            <p className="m-0 rounded-md bg-warning-bg px-3 py-2 text-xs text-warning-strong">
              期日の日を選んでください。期日を付けないときは「なし」を押します。
            </p>
          ) : dueOn ? (
            <p className="m-0 text-xs text-text-muted">
              {taskMd(due)} を過ぎても済んでいなければ「期日を過ぎた」になります。
            </p>
          ) : (
            <p className="m-0 text-xs text-text-muted">
              期日なしのときは「期日を過ぎた」になりません。済むまで、次の訪問へ持ち越して「何回目の訪問待ち」を出します。
            </p>
          )}
        </div>
      </div>

      <DialogFooter className="items-center">
        {missing.length > 0 && (
          <span className="mr-auto text-sm text-text-muted" data-testid="add-task-missing">
            {missing.join('・')}を入れてください
          </span>
        )}
        <Button type="button" variant="outline" onClick={onClose}>
          閉じる
        </Button>
        <Button
          type="button"
          disabled={!ok || create.isPending}
          onClick={submit}
          data-testid="add-task-submit"
        >
          付ける
        </Button>
      </DialogFooter>
    </div>
  );
}
