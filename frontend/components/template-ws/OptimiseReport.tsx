'use client';

/**
 * 最適化の報告（帯の［くわしく］・編集の履歴の［くわしく］から開く）。
 * 中身はバックエンドの report.py が作る（らく助エージェントの `rk template explain` と同じ）:
 *  a. 変わった枠を種類ごとに（行を開くと 曜日・患者様・前→後。押すと盤面のその枠を光らせる）
 *  b. 曜日ごとの物差し（いまの型 → この下書き）
 *  c. 平易な説明（移動の合計が増えた理由を必ず）
 *  d. 気になる点
 */
import { useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, X } from 'lucide-react';

import { apiErrorMessage } from '@/lib/api/errorMessage';
import { useReport } from '@/lib/queries/templateWs';
import type { OptimiseReport as Report, ReportDay, ReportDayNumbers } from '@/lib/templateWs/types';

function arrow(a: number, b: number) {
  return `${a} → ${b}`;
}

function counts(xs: number[]) {
  return xs.length ? xs.join('・') : '—';
}

const ROWS: { label: string; cell: (m: ReportDayNumbers, a: ReportDayNumbers, d: ReportDay) => string }[] = [
  { label: '変えた枠', cell: (_m, _a, d) => String(d.changes) },
  { label: '間に合わない組', cell: (m, a) => arrow(m.conflicts, a.conflicts) },
  { label: '上限超え', cell: (m, a) => arrow(m.over_cap, a.over_cap) },
  { label: 'コースの無い枠', cell: (m, a) => arrow(m.no_course, a.no_course) },
  { label: '人のいないコース', cell: (m, a) => arrow(m.unstaffed, a.unstaffed) },
  { label: '移動の合計（分）', cell: (m, a) => arrow(m.travel_min, a.travel_min) },
  {
    label: '└ 道順に新しく入った訪問の分',
    cell: (_m, _a, d) => `${d.travel.routed > 0 ? '+' : ''}${d.travel.routed}`,
  },
  { label: '└ 並べ替えの分', cell: (_m, _a, d) => `${d.travel.reorder > 0 ? '+' : ''}${d.travel.reorder}` },
  { label: '一般職員の件数', cell: (m, a) => `${counts(m.regular_counts)} → ${counts(a.regular_counts)}` },
  { label: '管理者の訪問', cell: (m, a) => arrow(m.manager_visits, a.manager_visits) },
];

export function ReportBody({ report, onFlash }: { report: Report; onFlash: (key: string) => void }) {
  const [openKind, setOpenKind] = useState<string | null>(null);
  return (
    <div className="space-y-3 text-sm">
      <section aria-label="説明" data-testid="report-text" className="space-y-1">
        {report.text.map((t, i) => (
          <p key={i} className="leading-relaxed">
            {t}
          </p>
        ))}
      </section>

      {report.cautions.length ? (
        <section
          className="rounded-md border border-border-warning bg-warning-bg px-3 py-2"
          aria-label="気になる点"
          data-testid="report-cautions"
        >
          <h4 className="mb-1 flex items-center gap-1 font-semibold text-warning-strong">
            <AlertTriangle className="h-4 w-4" aria-hidden />
            気になる点
          </h4>
          <ul className="list-disc space-y-0.5 pl-5">
            {report.cautions.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <section aria-label="変わった枠（種類ごと）" data-testid="report-kinds">
        <h4 className="mb-1 font-semibold">変わった枠（種類ごと）</h4>
        {report.categories.length === 0 ? <p className="text-text-muted">変わった枠はありません。</p> : null}
        <ul className="divide-y divide-border-default rounded-md border border-border-default">
          {report.categories.map((c) => {
            const open = openKind === c.kind;
            return (
              <li key={c.kind}>
                <button
                  type="button"
                  className="flex w-full items-center gap-1.5 px-3 py-1.5 text-left hover:bg-bg-muted/60"
                  aria-expanded={open}
                  onClick={() => setOpenKind(open ? null : c.kind)}
                  data-testid={`report-kind-${c.kind}`}
                >
                  {open ? <ChevronDown className="h-4 w-4" aria-hidden /> : <ChevronRight className="h-4 w-4" aria-hidden />}
                  <span className="flex-1">{c.label}</span>
                  <span className="tnum font-semibold">{c.count} 件</span>
                </button>
                {open ? (
                  <table className="mb-2 ml-8 w-[calc(100%-2.5rem)] text-xs">
                    <thead>
                      <tr className="text-left text-text-muted">
                        <th className="py-0.5 font-normal">曜日</th>
                        <th className="font-normal">患者様</th>
                        <th className="font-normal">前</th>
                        <th className="font-normal">後</th>
                      </tr>
                    </thead>
                    <tbody>
                      {c.items.map((it) => (
                        <tr
                          key={it.key}
                          className="cursor-pointer border-t border-border-default/60 hover:bg-brand-primary-50"
                          onClick={() => onFlash(it.key)}
                          data-testid={`report-item-${it.key}`}
                          title="盤面のこの枠を光らせます"
                        >
                          <td className="py-1">{it.weekday_ja}</td>
                          <td>
                            {it.patient}
                            {it.office ? <span className="ml-1 text-text-muted">（{it.office}）</span> : null}
                            {it.red ? <span className="ml-1 text-error">赤ピン</span> : null}
                          </td>
                          <td className="tnum">
                            {it.from_course}
                            {it.from_start ? ` ${it.from_start}` : ''}
                          </td>
                          <td className="tnum">
                            {it.to_course}
                            {it.to_start ? ` ${it.to_start}` : ''}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : null}
              </li>
            );
          })}
        </ul>
      </section>

      {report.days.length ? (
        <section aria-label="曜日ごとの物差し" data-testid="report-days" className="overflow-x-auto">
          <h4 className="mb-1 font-semibold">曜日ごとの物差し（いまの型 → この下書き）</h4>
          <table className="w-full min-w-[36rem] text-xs">
            <thead>
              <tr className="border-b border-border-default text-left text-text-muted">
                <th className="py-1 pr-2 font-normal" />
                {report.days.map((d) => (
                  <th key={d.weekday} className="px-2 font-semibold text-text-primary">
                    {d.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="tnum">
              {ROWS.map((r) => (
                <tr key={r.label} className="border-b border-border-default/50">
                  <th className="whitespace-nowrap py-1 pr-2 text-left font-normal text-text-secondary">{r.label}</th>
                  {report.days.map((d) => (
                    <td key={d.weekday} className="whitespace-nowrap px-2">
                      {r.cell(d.master, d.after, d)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-1 text-xs text-text-muted">
            「移動の合計」の内訳は、この最適化の前 → 後の増減です。コースの無い枠・人のいないコースの枠・新しく置いた方は、前は道順に入っておらず移動に数えられていなかったので、道順に入れた分だけ増えます。
          </p>
        </section>
      ) : null}
    </div>
  );
}

export function OptimiseReportPanel({
  draftId,
  seq,
  onClose,
  onFlash,
}: {
  draftId: string;
  seq: number | null;
  onClose: () => void;
  onFlash: (key: string) => void;
}) {
  const q = useReport(draftId, seq, true);
  const r = q.data;
  return (
    <section
      className="rounded-lg border border-border-default bg-bg-base px-4 py-3 shadow-sm"
      aria-label="最適化の報告"
      data-testid="optimise-report"
    >
      <header className="mb-2 flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="text-base font-semibold">最適化の報告</h3>
          {r ? (
            <p className="text-xs text-text-muted">
              {r.actor_label}が最適化しました{r.reason ? `（${r.reason}）` : ''}・変わった枠 {r.changed} 件
              {r.undone ? '・この最適化は取り消してあります' : ''}
            </p>
          ) : null}
        </div>
        <button type="button" onClick={onClose} aria-label="報告を閉じる" className="rounded p-1 hover:bg-bg-muted">
          <X className="h-4 w-4" />
        </button>
      </header>
      {q.isLoading ? <p className="text-sm text-text-muted">読み込み中…</p> : null}
      {q.error ? <p className="text-sm text-error">{apiErrorMessage(q.error)}</p> : null}
      {r ? <ReportBody report={r} onFlash={onFlash} /> : null}
    </section>
  );
}
