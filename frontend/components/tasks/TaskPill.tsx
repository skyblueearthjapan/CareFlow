/** やることの状態の小さな札 (伝言・完了・期日を過ぎた・持ち越し・未完了・毎月)。 */
import type { ReactNode } from 'react';

export function Pill({
  tone,
  children,
}: {
  tone: 'msg' | 'ok' | 'over' | 'carry' | 'open' | 'mon';
  children: ReactNode;
}) {
  const cls = {
    msg: 'bg-info-bg text-info-strong',
    ok: 'bg-success-bg text-success',
    over: 'bg-error-bg text-error',
    carry: 'bg-warning-bg text-warning-strong',
    open: 'bg-brand-primary-50 text-brand-primary-hover',
    mon: 'bg-brand-primary-50 text-brand-primary-hover ring-1 ring-inset ring-brand-primary/30',
  }[tone];
  return (
    <span className={`inline-block rounded-full px-1.5 py-px text-[11px] font-bold ${cls}`}>
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
