'use client';

/** 数の物差し（いまの型 → 下書き）。小さいほどよい。良くなる緑・増える赤・同じは灰色。 */
import { METRIC_ITEMS, tone } from '@/lib/templateWs/helpers';
import type { DraftView, Metrics } from '@/lib/templateWs/types';

const TONE_CLASS = {
  good: 'text-success',
  bad: 'text-error',
  same: 'text-text-secondary',
} as const;

export function MetricsHeader({ view }: { view: DraftView }) {
  const totals = view.totals as { master?: Metrics; draft?: Metrics };
  const master = totals.master;
  const draft = totals.draft;
  if (!master || !draft) return null;
  return (
    <div
      className="flex flex-wrap items-stretch gap-2"
      data-testid="template-metrics"
      aria-label={`数の物差し（${view.compare.name} → この下書き）`}
    >
      {METRIC_ITEMS.map(({ key, label }) => {
        const b = master[key] as number;
        const a = draft[key] as number;
        const t = tone(b, a);
        return (
          <div
            key={key}
            className="min-w-[8.5rem] rounded border border-border-default bg-bg-base px-3 py-1.5"
            data-testid={`metric-${key}`}
            data-tone={t}
          >
            <div className="text-[11px] text-text-muted">{label}</div>
            <div className={`tnum text-sm font-semibold ${TONE_CLASS[t]}`}>
              {b} <span aria-hidden>→</span> {a}
            </div>
          </div>
        );
      })}
      <div className="self-center text-[11px] text-text-muted">
        左は{view.compare.name}・右はこの下書き（小さいほどよい）
      </div>
    </div>
  );
}
