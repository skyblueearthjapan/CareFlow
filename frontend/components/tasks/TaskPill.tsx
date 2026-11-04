/** やることの状態の小さな札 (伝言・完了・期日・期日を過ぎた・何回目の訪問待ち・毎月)。 */
import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';
import type { PatientTaskRead } from '@/lib/schemas/patient-task';

import { taskDueState, taskMd } from './taskFormat';

export type PillTone =
  | 'msg'
  | 'ok'
  | 'over'
  | 'overStrong'
  | 'carry'
  | 'open'
  | 'mon'
  | 'due'
  | 'dueToday'
  | 'round1'
  | 'round2'
  | 'round3'
  | 'round4';

export function Pill({ tone, children }: { tone: PillTone; children: ReactNode }) {
  const cls = {
    msg: 'bg-info-bg text-info-strong',
    ok: 'bg-success-bg text-success',
    over: 'bg-error-bg text-error',
    // 期日を過ぎた (モックの .pill.over)。
    overStrong: 'bg-error text-white',
    carry: 'bg-warning-bg text-warning-strong',
    open: 'bg-brand-primary-50 text-brand-primary-hover',
    mon: 'bg-brand-primary-50 text-brand-primary-hover ring-1 ring-inset ring-brand-primary/30',
    due: 'bg-bg-base text-[#455a64] ring-1 ring-inset ring-[#b0bec5]',
    dueToday: 'bg-bg-base text-error ring-[1.5px] ring-inset ring-error',
    // 何回目の訪問待ち: 1 回目は灰色、2 回目以降は橙で、回数が多いほど濃い。
    round1: 'bg-bg-muted text-text-muted font-normal',
    round2: 'bg-[#fff4e0] text-[#b26a00]',
    round3: 'bg-[#ffd9a8] text-[#8a4b00]',
    round4: 'bg-[#e07800] text-white',
  }[tone];
  return (
    <span
      className={cn(
        'inline-block whitespace-nowrap rounded-full px-1.5 py-px text-[11px] font-bold',
        cls,
      )}
    >
      {children}
    </span>
  );
}

/**
 * 毎月の設定から作ったやることの印 (「毎月」と「12 月分」)。手で付けたものには何も出さない。
 * 先月の分と今月の分が同じ訪問に並ぶとき、どちらの月か見分けるため
 * (patient-tasks-recurring-design-2026-11-02 §3-2)。
 */
export function RecurringMark({ month }: { month: string | null | undefined }) {
  if (!month) return null;
  const m = Number(month.split('-')[1]);
  return (
    <span data-testid="recurring-mark">
      <Pill tone="mon">毎月・{m} 月分</Pill>
    </span>
  );
}

/** 「N 回目の訪問待ち」(patient-tasks-due-design §2-d)。 */
export function RoundPill({ round }: { round: number }) {
  const n = Math.max(1, round);
  const tone: PillTone = n >= 4 ? 'round4' : n === 3 ? 'round3' : n === 2 ? 'round2' : 'round1';
  return (
    <span data-testid="task-round" data-round={n}>
      <Pill tone={tone}>{n} 回目の訪問待ち</Pill>
    </span>
  );
}

/**
 * 訪問の詳細 (PC)・スマホで、未完了のやることに並べる札 (モック C)。
 * 並び = ［期日を過ぎた］伝言／期日／N 回目の訪問待ち／毎月。
 * 期日を過ぎたときだけ、赤い「期日を過ぎた」を先頭に出す (期日の札はその中に入れる)。
 * refDate = 期日を見る日 (訪問の日)。BE の overdue (今日から見て) が立っていても過ぎたにする。
 */
export function TaskOpenPills({
  task: t,
  refDate,
}: {
  task: Pick<
    PatientTaskRead,
    'is_message' | 'due_date' | 'overdue' | 'visit_round' | 'recurring_month'
  >;
  refDate: string;
}) {
  const ds = taskDueState(t.due_date, refDate);
  const over = !!t.due_date && (t.overdue || ds?.kind === 'over');
  return (
    <>
      {over && t.due_date && (
        <span data-testid="task-overdue">
          <Pill tone="overStrong">期日を過ぎた（{taskMd(t.due_date)} まで）</Pill>
        </span>
      )}
      {t.is_message && <Pill tone="msg">伝言</Pill>}
      {!over && ds && t.due_date && (
        <span data-testid="task-due">
          {ds.kind === 'today' ? (
            <Pill tone="dueToday">今日まで</Pill>
          ) : (
            <Pill tone="due">
              {taskMd(t.due_date)} まで・あと {ds.days} 日
            </Pill>
          )}
        </span>
      )}
      <RoundPill round={t.visit_round} />
      <RecurringMark month={t.recurring_month} />
    </>
  );
}
