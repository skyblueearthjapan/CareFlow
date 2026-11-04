'use client';

/**
 * /tasks — 事務所からのやること (管理者だけ・patient-tasks-design-2026-11-01 §2-6 / §3)。
 *
 * 未完了・期日を過ぎた・2 回目以降の訪問待ち・完了の件数、絞り込み (状態・看護師)、表 (日付・
 * 患者様・やること／伝言・期日・出す訪問・状態・操作)、右上の「＋ やることを付ける」、
 * 下に「項目の設定」。期日と何回目の訪問待ちは patient-tasks-due-design-2026-11-04。
 * 期日の欄を押すと日付を選び直せる (管理者・完了／取消の行は押せない・§6-3)。
 * 出す訪問は BE が見るたびに決める (患者 + 日付以降の最初の訪問)。
 * 「預かる」の項目は、最初の訪問で済まなければその訪問の主担当が預かる (預かり中・青の札・
 * patient-tasks-hold-first-visitor-design-2026-11-04)。出す訪問の欄に預かっている人と日数を出す。
 *
 * 上のタブで「やることの一覧」と「毎月のやること」(繰り返しの設定・
 * patient-tasks-recurring-design-2026-11-02 §3-1) を切り替える。
 */
import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { Plus } from 'lucide-react';

import { RakusukeTitle } from '@/components/brand/Rakusuke';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { FilterChip } from '@/components/ui/filter-chip';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import { AddTaskDialog } from '@/components/tasks/AddTaskDialog';
import { RecurringTasksPanel } from '@/components/tasks/RecurringTasksPanel';
import { TaskItemTypesSettings } from '@/components/tasks/TaskItemTypesSettings';
import { Pill, RecurringMark, RoundPill } from '@/components/tasks/TaskPill';
import {
  taskActionErrorMessage,
  taskDoneLabel,
  heldSinceLabel,
  taskDueState,
  taskMdWd,
  taskTitle,
  visitBriefLabel,
} from '@/components/tasks/taskFormat';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { isAdminRole } from '@/lib/rbac';
import { cn } from '@/lib/utils';
import { useStaffList } from '@/lib/queries/staff';
import {
  useCancelTask,
  useCompleteTask,
  useReopenTask,
  useSetTaskDue,
  useTasks,
} from '@/lib/queries/tasks';
import type { PatientTaskRead, TaskListStatus } from '@/lib/schemas/patient-task';

/** 完了の件数と一覧は、完了した日が直近この日数の分だけ (完了は増え続けるため)。 */
const DONE_LOOKBACK_DAYS = 30;

function todayJst(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo' }).format(new Date());
}

function addDays(ymd: string, days: number): string {
  const [y, m, d] = ymd.split('-').map(Number);
  const dt = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1));
  dt.setUTCDate(dt.getUTCDate() + days);
  return dt.toISOString().slice(0, 10);
}

const STATUS_FILTERS: { key: TaskListStatus; label: string }[] = [
  { key: 'open', label: '未完了' },
  { key: 'overdue', label: '期日を過ぎた' },
  { key: 'waiting2plus', label: '2 回目以降' },
  { key: 'held', label: '預かり中' },
  { key: 'done', label: '完了' },
  { key: 'all', label: 'すべて' },
];

export default function TasksPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const isAdmin = isAdminRole(session?.user?.role);

  // ページも管理者だけ (BE も管理者だけ)。
  useEffect(() => {
    if (status === 'authenticated' && !isAdmin) router.replace('/dashboard');
  }, [status, isAdmin, router]);

  const today = todayJst();
  const doneFrom = addDays(today, -DONE_LOOKBACK_DAYS);
  const [statusFilter, setStatusFilter] = useState<TaskListStatus>('open');
  const [staffId, setStaffId] = useState('');
  const [addOpen, setAddOpen] = useState(false);
  const [tab, setTab] = useState<'list' | 'recurring'>('list');

  // 件数 (未完了はメニューのバッジと同じキーで共有)。
  const openAll = useTasks({ status: 'open' }, { enabled: isAdmin });
  const doneRecent = useTasks({ status: 'done', doneFrom }, { enabled: isAdmin });
  const rowsQuery = useTasks(
    {
      status: statusFilter,
      staffId: staffId || null,
      doneFrom: statusFilter === 'done' ? doneFrom : null,
    },
    { enabled: isAdmin },
  );
  const rows = useMemo(() => sortByUrgency(rowsQuery.data ?? [], today), [rowsQuery.data, today]);
  const stats = {
    open: openAll.data?.length ?? 0,
    overdue: (openAll.data ?? []).filter((t) => t.overdue).length,
    // 預かり中は「2 回目以降」に数えない (BE の waiting2plus と同じ)。
    waiting2plus: (openAll.data ?? []).filter((t) => !t.held_by && t.visit_round >= 2).length,
    held: (openAll.data ?? []).filter((t) => !!t.held_by).length,
    done: doneRecent.data?.length ?? 0,
  };

  const staffList = useStaffList({ limit: 500 });
  const staffOptions = useMemo(
    () =>
      (staffList.data ?? [])
        .filter((s) => !s.deleted_at && s.status !== 'retired')
        .map((s) => ({ id: s.id, name: s.name })),
    [staffList.data],
  );

  const complete = useCompleteTask();
  const reopen = useReopenTask();
  const cancel = useCancelTask();
  const setDue = useSetTaskDue();
  const busy = complete.isPending || reopen.isPending || cancel.isPending || setDue.isPending;
  const onError = (fallback: string) => (err: unknown) =>
    toast.error(fallback, { description: taskActionErrorMessage(err) });

  if (status === 'loading' || (status === 'authenticated' && !isAdmin)) return null;

  return (
    <section className="space-y-4" data-testid="tasks-page">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <RakusukeTitle
          pose="idea"
          title="タスク管理"
          subtitle="事務所から看護師へのやることです。その方の訪問のときにスマホに出し、済んだら完了になります。"
        />
        <Button type="button" onClick={() => setAddOpen(true)} data-testid="tasks-add-open">
          <Plus className="h-4 w-4" aria-hidden="true" />
          やることを付ける
        </Button>
      </header>

      <div className="flex gap-1 border-b border-border-default" role="tablist">
        {(
          [
            ['list', 'やることの一覧'],
            ['recurring', '毎月のやること'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={cn(
              '-mb-px border-b-2 px-4 py-2 text-sm font-bold',
              tab === key
                ? 'border-brand-primary text-brand-primary'
                : 'border-transparent text-text-secondary hover:text-text-primary',
            )}
            data-testid={`tasks-tab-${key}`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'recurring' ? (
        <RecurringTasksPanel />
      ) : (
        <>
          <div className="flex flex-wrap gap-3">
            <Stat label="未完了" value={stats.open} testId="tasks-stat-open" />
            <Stat
              label="期日を過ぎた"
              value={stats.overdue}
              tone="over"
              testId="tasks-stat-overdue"
            />
            <Stat
              label="2 回目以降の訪問待ち"
              value={stats.waiting2plus}
              tone="carry"
              testId="tasks-stat-waiting2plus"
            />
            <Stat label="預かり中" value={stats.held} tone="held" testId="tasks-stat-held" />
            <Stat
              label={`完了（直近 ${DONE_LOOKBACK_DAYS} 日）`}
              value={stats.done}
              tone="ok"
              testId="tasks-stat-done"
            />
          </div>

          <div className="overflow-hidden rounded-lg border border-border-default bg-bg-base shadow-outer-card">
            <div className="flex flex-wrap items-center gap-2 border-b border-border-default px-5 py-2.5">
              {STATUS_FILTERS.map((f) => (
                <FilterChip
                  key={f.key}
                  active={statusFilter === f.key}
                  onClick={() => setStatusFilter(f.key)}
                >
                  {f.label}
                </FilterChip>
              ))}
              <span className="h-5 w-px bg-border-default" />
              <select
                value={staffId}
                onChange={(e) => setStaffId(e.target.value)}
                aria-label="看護師で絞る"
                className="h-9 rounded-md border border-border-default bg-bg-base px-2 text-sm"
                data-testid="tasks-staff-filter"
              >
                <option value="">看護師：すべて</option>
                {staffOptions.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
              <span className="text-xs text-text-muted">{rows.length} 件</span>
            </div>

            {rowsQuery.isLoading ? (
              <div className="space-y-2 p-5">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            ) : rowsQuery.isError ? (
              <div className="p-5">
                <Alert variant="destructive">
                  <AlertTitle>やることを読み込めませんでした</AlertTitle>
                  <AlertDescription>{apiErrorMessage(rowsQuery.error)}</AlertDescription>
                </Alert>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[900px] text-sm" data-testid="tasks-table">
                  <thead className="border-b border-border-default bg-bg-muted text-left text-xs text-text-secondary">
                    <tr>
                      <th className="px-3 py-2 font-medium">日付</th>
                      <th className="px-3 py-2 font-medium">患者様</th>
                      <th className="px-3 py-2 font-medium">やること・伝言</th>
                      <th className="px-3 py-2 font-medium">期日</th>
                      <th className="px-3 py-2 font-medium">出す訪問</th>
                      <th className="px-3 py-2 font-medium">状態</th>
                      <th className="px-3 py-2 font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="px-3 py-8 text-center text-text-muted">
                          該当するやることはありません。
                        </td>
                      </tr>
                    ) : (
                      rows.map((t) => (
                        <TaskTableRow
                          key={t.id}
                          task={t}
                          today={today}
                          busy={busy}
                          onSetDue={(dueDate, onDone) =>
                            setDue.mutate(
                              { taskId: t.id, dueDate },
                              {
                                onSuccess: () => {
                                  toast.success(
                                    dueDate
                                      ? `期日を ${taskMdWd(dueDate)} にしました`
                                      : '期日をなしにしました',
                                  );
                                  onDone();
                                },
                                onError: onError('期日を変えられませんでした'),
                              },
                            )
                          }
                          onComplete={() =>
                            complete.mutate(
                              { taskId: t.id },
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
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          <p
            className="m-0 flex flex-wrap items-center gap-1.5 text-xs text-text-muted"
            data-testid="tasks-legend"
          >
            札：
            <RoundPill round={1} />
            まだ訪問が来ていない
            <RoundPill round={2} />
            <RoundPill round={3} />
            <RoundPill round={4} />
            前の訪問で済まなかった（数が多いほど濃い）
            <Pill tone="held">預かり中</Pill>
            最初に訪問した看護師が預かっている
            <Pill tone="overStrong">期日を過ぎた</Pill>
            期日がある分だけ
          </p>

          <details className="rounded-lg border border-border-default bg-bg-base p-4">
            <summary className="cursor-pointer text-base font-bold text-text-primary">
              項目の設定（事務所で足す・名前を変える・使わない・並び順・預かる）
            </summary>
            <div className="mt-3">
              <TaskItemTypesSettings />
            </div>
          </details>
        </>
      )}

      <AddTaskDialog open={addOpen} onOpenChange={setAddOpen} defaultDate={today} />
    </section>
  );
}

function Stat({
  label,
  value,
  tone,
  testId,
}: {
  label: string;
  value: number;
  tone?: 'over' | 'ok' | 'carry' | 'held';
  testId?: string;
}) {
  return (
    <div
      className={cn(
        'min-w-[140px] rounded-lg border px-4 py-3',
        tone === 'over' && value > 0
          ? 'border-border-error bg-error-bg'
          : 'border-border-default bg-bg-base',
      )}
      data-testid={testId}
    >
      <div className="text-xs text-text-muted">{label}</div>
      <div
        className={cn(
          'text-2xl font-bold tabular-nums',
          tone === 'over' && value > 0
            ? 'text-error'
            : tone === 'ok'
              ? 'text-success'
              : tone === 'carry' && value > 0
                ? 'text-[#e07800]'
                : tone === 'held' && value > 0
                  ? 'text-[#1565c0]'
                  : 'text-text-primary',
        )}
      >
        {value}
      </div>
    </div>
  );
}

/**
 * 急ぐ順 (モック A): 期日を過ぎた → 今日まで → 訪問の回数が多い順 → 期日の近い順 → 日付の順。
 * 完了・取消は後ろ (その中の並びは BE のまま)。
 */
function sortByUrgency(list: PatientTaskRead[], today: string): PatientTaskRead[] {
  const rank = (t: PatientTaskRead) => {
    if (t.status !== 'open') return 9;
    if (t.overdue) return 0;
    if (taskDueState(t.due_date, today)?.kind === 'today') return 1;
    // 預かり中も「前の訪問で済まなかった」分なので、2 回目以降と同じ段に並べる。
    return t.held_by || t.visit_round >= 2 ? 2 : 3;
  };
  return list.slice().sort((a, b) => {
    const r = rank(a) - rank(b);
    if (r !== 0 || rank(a) === 9) return r;
    return (
      b.visit_round - a.visit_round ||
      (a.due_date ?? '9999').localeCompare(b.due_date ?? '9999') ||
      a.start_date.localeCompare(b.start_date)
    );
  });
}

/** 期日の欄。管理者は押すと日付を選び直せる (未完了の行だけ・「なし」にも戻せる)。 */
function DueCell({
  task: t,
  today,
  busy,
  onSetDue,
}: {
  task: PatientTaskRead;
  today: string;
  busy: boolean;
  onSetDue: (dueDate: string | null, onDone: () => void) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(t.due_date ?? '');
  const editable = t.status === 'open';
  const ds = editable ? taskDueState(t.due_date, today) : null;

  if (editing) {
    const valid = /^\d{4}-\d{2}-\d{2}$/.test(value);
    const before = valid && value < t.start_date;
    return (
      <div className="space-y-1.5" data-testid={`tasks-due-edit-${t.id}`}>
        <input
          type="date"
          aria-label="期日"
          value={value}
          min={t.start_date}
          onChange={(e) => setValue(e.target.value)}
          className="block h-9 w-40 rounded-md border border-border-default bg-bg-base px-2 text-sm"
          data-testid={`tasks-due-input-${t.id}`}
        />
        {before && (
          <p className="m-0 text-xs font-bold text-error" role="alert">
            日付（{taskMdWd(t.start_date)}）より前です
          </p>
        )}
        <div className="flex flex-wrap gap-1">
          <Button
            type="button"
            size="sm"
            disabled={busy || !valid || before}
            onClick={() => onSetDue(value, () => setEditing(false))}
            data-testid={`tasks-due-save-${t.id}`}
          >
            決める
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy || !t.due_date}
            onClick={() => onSetDue(null, () => setEditing(false))}
            data-testid={`tasks-due-clear-${t.id}`}
          >
            なし
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(false)}>
            やめる
          </Button>
        </div>
      </div>
    );
  }

  const body = !t.due_date ? (
    <span className="text-xs text-text-muted">なし</span>
  ) : (
    <>
      <b className={cn(ds && ds.kind !== 'left' && 'text-error')}>{taskMdWd(t.due_date)} まで</b>
      {ds && (
        <div
          className={cn('text-xs', ds.kind === 'left' ? 'text-text-muted' : 'font-bold text-error')}
        >
          {ds.kind === 'over'
            ? `${ds.days} 日過ぎた`
            : ds.kind === 'today'
              ? '今日まで'
              : `あと ${ds.days} 日`}
        </div>
      )}
    </>
  );

  if (!editable) return <div data-testid={`tasks-due-${t.id}`}>{body}</div>;
  return (
    <button
      type="button"
      onClick={() => {
        setValue(t.due_date ?? '');
        setEditing(true);
      }}
      disabled={busy}
      title="押すと期日を選べます"
      className="rounded-md px-1 py-0.5 text-left hover:bg-bg-muted"
      data-testid={`tasks-due-${t.id}`}
    >
      {body}
    </button>
  );
}

function TaskTableRow({
  task: t,
  today,
  busy,
  onSetDue,
  onComplete,
  onReopen,
  onCancel,
}: {
  task: PatientTaskRead;
  today: string;
  busy: boolean;
  onSetDue: (dueDate: string | null, onDone: () => void) => void;
  onComplete: () => void;
  onReopen: () => void;
  onCancel: () => void;
}) {
  const done = t.status === 'done';
  const shownVisit = done ? t.done_visit : t.next_visit;
  return (
    <tr
      className={cn('border-b border-border-default align-top last:border-0', done && 'opacity-70')}
      data-testid={`tasks-row-${t.id}`}
    >
      <td className="whitespace-nowrap px-3 py-2">{taskMdWd(t.start_date)}</td>
      <td className="whitespace-nowrap px-3 py-2">{t.patient_name ?? '—'}</td>
      <td className="px-3 py-2">
        {t.is_message ? <Pill tone="msg">伝言</Pill> : <b>{taskTitle(t)}</b>}{' '}
        <RecurringMark month={t.recurring_month} />
        {t.memo && <div className="mt-0.5 whitespace-pre-wrap text-text-secondary">{t.memo}</div>}
        {t.created_by_name && (
          <div className="mt-0.5 text-xs text-text-muted">付けた人 {t.created_by_name}</div>
        )}
      </td>
      <td className="whitespace-nowrap px-3 py-2">
        <DueCell task={t} today={today} busy={busy} onSetDue={onSetDue} />
      </td>
      <td className="whitespace-nowrap px-3 py-2">
        {!done && t.held_by ? (
          <div data-testid={`tasks-held-${t.id}`}>
            <b>
              {t.held_by.staff_name ?? '（名前なし）'}さんが預かり中
              {!t.held_by.staff_active && (
                <span className="font-normal text-text-muted">（在籍なし）</span>
              )}
            </b>
            <div className="text-xs text-text-muted">
              {heldSinceLabel(t.held_by.visit_date, today)}
            </div>
          </div>
        ) : shownVisit ? (
          visitBriefLabel(shownVisit)
        ) : done ? (
          '—'
        ) : (
          <Pill tone="over">訪問が入ったら出ます</Pill>
        )}
      </td>
      <td className="whitespace-nowrap px-3 py-2">
        {done ? (
          <>
            <Pill tone="ok">完了</Pill>
            <div className="mt-0.5 text-xs text-text-muted">{taskDoneLabel(t)}</div>
          </>
        ) : (
          <div className="flex flex-wrap items-center gap-1">
            {t.overdue && <Pill tone="overStrong">期日を過ぎた</Pill>}
            {t.held_by ? (
              <span data-testid="task-held">
                <Pill tone="held">預かり中</Pill>
              </span>
            ) : (
              <RoundPill round={t.visit_round} />
            )}
          </div>
        )}
      </td>
      <td className="whitespace-nowrap px-3 py-2">
        <div className="flex gap-1.5">
          {done ? (
            t.can_reopen && (
              <Button type="button" variant="outline" size="sm" disabled={busy} onClick={onReopen}>
                戻す
              </Button>
            )
          ) : (
            <>
              <Button type="button" size="sm" disabled={busy} onClick={onComplete}>
                完了
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={onCancel}
                className="text-text-secondary"
              >
                取り消す
              </Button>
            </>
          )}
        </div>
      </td>
    </tr>
  );
}
