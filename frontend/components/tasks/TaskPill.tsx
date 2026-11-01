/** やることの状態の小さな札 (伝言・完了・期日を過ぎた・持ち越し・未完了)。 */
import type { ReactNode } from 'react';

export function Pill({
  tone,
  children,
}: {
  tone: 'msg' | 'ok' | 'over' | 'carry' | 'open';
  children: ReactNode;
}) {
  const cls = {
    msg: 'bg-info-bg text-info-strong',
    ok: 'bg-success-bg text-success',
    over: 'bg-error-bg text-error',
    carry: 'bg-warning-bg text-warning-strong',
    open: 'bg-brand-primary-50 text-brand-primary-hover',
  }[tone];
  return (
    <span className={`inline-block rounded-full px-1.5 py-px text-[11px] font-bold ${cls}`}>
      {children}
    </span>
  );
}
