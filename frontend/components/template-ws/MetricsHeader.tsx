'use client';

/**
 * 数の物差し（いまの型 → 下書き）。小さいほどよい。良くなる緑・増える赤・同じは灰色。
 * 盤面に幅を回すため 1 行に並べる。全拠点の下書きは、合計を出し、拠点ごとの数は指を置くと出る。
 */
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
  const byOffice = view.totals_by_office ?? [];
  return (
    <div
      className="flex flex-wrap items-center gap-1.5"
      data-testid="template-metrics"
      aria-label={`数の物差し（${view.compare.name} → この下書き）`}
    >
      {METRIC_ITEMS.map(({ key, label }) => {
        const b = master[key] as number;
        const a = draft[key] as number;
        const t = tone(b, a);
        const parts = byOffice.map((o) => `${o.label} ${o.master[key] ?? 0}→${o.draft[key] ?? 0}`).join('・');
        return (
          <div
            key={key}
            className="flex items-baseline gap-1.5 rounded-md border border-border-default bg-bg-base px-2.5 py-1"
            data-testid={`metric-${key}`}
            data-tone={t}
            title={parts ? `拠点ごと: ${parts}` : undefined}
          >
            <span className="text-xs text-text-muted">{label}</span>
            <span className={`tnum text-sm font-semibold ${TONE_CLASS[t]}`}>
              {b} <span aria-hidden>→</span> {a}
            </span>
          </div>
        );
      })}
      <span className="text-xs text-text-muted">
        左は{view.compare.name}・右はこの下書き（小さいほどよい）
        {byOffice.length > 1 ? '・指を置くと拠点ごとの数' : ''}
      </span>
    </div>
  );
}
