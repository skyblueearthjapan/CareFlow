/** 「月の実績」の 6 段（モック monthly-actual-sync-mock.html の上の帯）。表示だけ。 */

import type { StepNo } from './monthlyActualFormat';

const STEPS: ReadonlyArray<{ no: StepNo; label: string }> = [
  { no: 1, label: '読み取る' },
  { no: 2, label: '突き合わせ' },
  { no: 3, label: '試しに流す' },
  { no: 4, label: '反映する' },
  { no: 5, label: '確かめる' },
  { no: 6, label: '一覧を出す' },
];

export function MonthlyActualSteps({ current }: { current: StepNo }) {
  return (
    <ol
      className="grid grid-cols-3 gap-1.5 md:grid-cols-6"
      aria-label="手順"
      data-testid="ma-steps"
    >
      {STEPS.map((s) => {
        const done = s.no < current;
        const cur = s.no === current;
        return (
          <li
            key={s.no}
            aria-current={cur ? 'step' : undefined}
            data-testid={`ma-step-${s.no}`}
            data-state={done ? 'done' : cur ? 'current' : 'todo'}
            className={[
              'min-w-0 rounded-lg border px-2.5 py-2',
              done
                ? 'border-success bg-success-bg'
                : cur
                  ? 'border-brand-primary bg-brand-primary-50'
                  : 'border-border-default bg-bg-base',
            ].join(' ')}
          >
            <small
              className={[
                'block text-[11px]',
                done ? 'text-success' : cur ? 'text-brand-primary-hover' : 'text-text-muted',
              ].join(' ')}
            >
              {done ? '済み' : `手順 ${s.no}`}
            </small>
            <b className="text-sm text-text-primary">{s.label}</b>
          </li>
        );
      })}
    </ol>
  );
}
