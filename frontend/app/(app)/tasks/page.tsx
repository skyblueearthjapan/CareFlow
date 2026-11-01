'use client';

/**
 * /tasks — 事務所からのやること (管理者だけ・patient-tasks-design-2026-11-01 §2-6 / §3)。
 *
 * 未完了・期日を過ぎた・完了の件数、絞り込み (状態・看護師)、表 (日付・患者様・
 * やること／伝言・出す訪問・状態・操作)、右上の「＋ やることを付ける」、下に「項目の設定」。
 * 出す訪問は BE が見るたびに決める (患者 + 日付以降の最初の訪問)。
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
import { TaskItemTypesSettings } from '@/components/tasks/TaskItemTypesSettings';
import { Pill } from '@/components/tasks/TaskPill';
import {
  taskActionErrorMessage,
  taskDoneLabel,
  taskMdWd,
  taskTitle,
  visitBriefLabel,
} from '@/components/tasks/taskFormat';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { isAdminRole } from '@/lib/rbac';
import { cn } from '@/lib/utils';
import { useStaffList } from '@/lib/queries/staff';
import { useCancelTask, useCompleteTask, useReopenTask, useTasks } from '@/lib/queries/tasks';
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
  const rows = rowsQuery.data ?? [];
  const stats = {
    open: openAll.data?.length ?? 0,
    overdue: (openAll.data ?? []).filter((t) => t.overdue).length,
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
  const busy = complete.isPending || reopen.isPending || cancel.isPending;
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

      <div className="flex flex-wrap gap-3">
        <Stat label="未完了" value={stats.open} testId="tasks-stat-open" />
        <Stat label="期日を過ぎた" value={stats.overdue} tone="over" testId="tasks-stat-overdue" />
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
            <table className="w-full min-w-[760px] text-sm" data-testid="tasks-table">
              <thead className="border-b border-border-default bg-bg-muted text-left text-xs text-text-secondary">
                <tr>
                  <th className="px-3 py-2 font-medium">日付</th>
                  <th className="px-3 py-2 font-medium">患者様</th>
                  <th className="px-3 py-2 font-medium">やること・伝言</th>
                  <th className="px-3 py-2 font-medium">出す訪問</th>
                  <th className="px-3 py-2 font-medium">状態</th>
                  <th className="px-3 py-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-8 text-center text-text-muted">
                      該当するやることはありません。
                    </td>
                  </tr>
                ) : (
                  rows.map((t) => (
                    <TaskTableRow
                      key={t.id}
                      task={t}
                      busy={busy}
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

      <details className="rounded-lg border border-border-default bg-bg-base p-4">
        <summary className="cursor-pointer text-base font-bold text-text-primary">
          項目の設定（事務所で足す・名前を変える・使わない・並び順）
        </summary>
        <div className="mt-3">
          <TaskItemTypesSettings />
        </div>
      </details>

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
  tone?: 'over' | 'ok';
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
              : 'text-text-primary',
        )}
      >
        {value}
      </div>
    </div>
  );
}

function TaskTableRow({
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
  const shownVisit = done ? t.done_visit : t.next_visit;
  return (
    <tr
      className={cn('border-b border-border-default align-top last:border-0', done && 'opacity-70')}
      data-testid={`tasks-row-${t.id}`}
    >
      <td className="whitespace-nowrap px-3 py-2">{taskMdWd(t.start_date)}</td>
      <td className="whitespace-nowrap px-3 py-2">{t.patient_name ?? '—'}</td>
      <td className="px-3 py-2">
        {t.is_message ? <Pill tone="msg">伝言</Pill> : <b>{taskTitle(t)}</b>}
        {t.memo && <div className="mt-0.5 whitespace-pre-wrap text-text-secondary">{t.memo}</div>}
        {t.created_by_name && (
          <div className="mt-0.5 text-xs text-text-muted">付けた人 {t.created_by_name}</div>
        )}
      </td>
      <td className="whitespace-nowrap px-3 py-2">
        {shownVisit ? (
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
        ) : t.overdue ? (
          <Pill tone="over">期日を過ぎた</Pill>
        ) : (
          <Pill tone="open">未完了</Pill>
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
