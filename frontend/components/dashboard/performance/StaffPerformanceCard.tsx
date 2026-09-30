/**
 * B: 人ごとのカード (1 人 1 枚)。押すとその人の C (1 人を深く見る) を開く。
 * 見た目の正典: docs/mockups/dashboard-staff-performance-mock.html (B)。
 */
'use client';

import * as React from 'react';

import { diff1, monthDay, r0, r1 } from '@/lib/dashboard-period';
import type { PerformanceMetrics, StaffPerformanceRow } from '@/lib/schemas/dashboard';

import { PERF_COLORS } from './colors';

/** 役割の札 (拠点・管理者・准看護師・新人)。 */
export function roleTags(row: StaffPerformanceRow): string[] {
  const tags: string[] = [];
  if (row.office_short) tags.push(row.office_short);
  if (row.is_manager) tags.push('管理者');
  if (row.qualification === '准看護師') tags.push('准看護師');
  if (row.is_trainee) tags.push('新人');
  return tags;
}

export function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-full border border-border-default bg-bg-muted px-2 text-[11px] leading-5 text-text-secondary">
      {children}
    </span>
  );
}

/** 実績の一言 (5 件未満は「—」と理由)。 */
export function actualText(m: PerformanceMetrics, minSamples: number): string {
  if (m.actual_min != null) return `実績 ${r0(m.actual_min)} 分・QR ${m.actual_samples} 件`;
  if (m.actual_samples > 0)
    return `実績 —（QR ${m.actual_samples} 件・${minSamples} 件から出します）`;
  if (m.arrival_only > 0) return `実績 —（到着のみ ${m.arrival_only} 件）`;
  return '実績 —（QR の記録がありません）';
}

function Tile({ k, v, unit, d }: { k: string; v: string; unit: string; d: string }) {
  return (
    <div className="rounded-md border border-border-default px-2.5 py-2">
      <div className="text-[11.5px] text-text-secondary">{k}</div>
      <div className="text-[22px] font-bold leading-tight tnum text-text-primary">
        {v}
        <small className="ml-0.5 text-xs font-medium text-text-secondary">{unit}</small>
      </div>
      <div className="text-[11.5px] text-text-secondary">{d}</div>
    </div>
  );
}

/** 週ごとの 1 日あたり件数の小さな棒。 */
function MiniBars({ weeks, labels }: { weeks: PerformanceMetrics[]; labels: string[] }) {
  const max = Math.max(1, ...weeks.map((w) => w.per_day ?? 0));
  return (
    <div
      className="flex h-24 items-end gap-1 border-b border-border-strong"
      role="img"
      aria-label="週ごとの 1 日あたり件数"
    >
      {weeks.map((w, i) => {
        const v = w.per_day ?? 0;
        return (
          <div
            key={labels[i]}
            className="flex min-w-0 flex-1 flex-col items-center justify-end"
            title={`${labels[i]} の週: 1 日あたり ${r1(w.per_day)} 件（${w.visits} 件 / ${w.days} 日）`}
          >
            <span className="text-[10.5px] tnum text-text-secondary">{v ? r1(v) : ''}</span>
            <div
              className="w-full max-w-[22px] rounded-t"
              style={{
                height: `${(v / max) * 64}px`,
                minHeight: 1,
                backgroundColor: PERF_COLORS.self,
              }}
            />
          </div>
        );
      })}
    </div>
  );
}

export interface StaffPerformanceCardProps {
  row: StaffPerformanceRow;
  team: PerformanceMetrics;
  weekStarts: string[];
  minActualSamples: number;
  onOpen: (staffId: string) => void;
}

export function StaffPerformanceCard({
  row,
  team,
  weekStarts,
  minActualSamples,
  onOpen,
}: StaffPerformanceCardProps) {
  const m = row.period;
  const labels = weekStarts.map(monthDay);
  const perDayDiff = diff1(m.per_day, team.per_day);
  const kmDiff = diff1(m.km_per_day, team.km_per_day);
  return (
    <button
      type="button"
      onClick={() => onOpen(row.staff_id)}
      data-testid="perf-staff-card"
      className="rounded-lg border border-border-default bg-bg-base px-4 py-3.5 text-left shadow-outer-card transition-colors hover:border-brand-primary-light focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-primary"
    >
      <h3 className="text-base font-bold text-text-primary">{row.name}</h3>
      <div className="mt-0.5 flex flex-wrap gap-1">
        {roleTags(row).map((t) => (
          <Tag key={t}>{t}</Tag>
        ))}
        <Tag>出勤 {m.days} 日</Tag>
      </div>
      <div className="my-2.5 grid grid-cols-2 gap-2">
        <Tile k="訪問" v={String(m.visits)} unit="件" d={`利用者 ${m.patients} 名`} />
        <Tile
          k="1 日あたり"
          v={r1(m.per_day)}
          unit="件"
          d={`平均 ${r1(team.per_day)}${perDayDiff ? `（${perDayDiff}）` : ''}`}
        />
        <Tile
          k="1 回あたり"
          v={r0(m.plan_min)}
          unit="分（予定）"
          d={actualText(m, minActualSamples)}
        />
        <Tile
          k="移動（直線）"
          v={r1(m.km_per_day)}
          unit="km/日"
          d={`平均 ${r1(team.km_per_day)}${kmDiff ? `（${kmDiff}）` : ''}・${r1(m.km_per_visit)} km/件`}
        />
      </div>
      <div className="mb-0.5 text-[11.5px] text-text-secondary">週ごとの 1 日あたり件数</div>
      <MiniBars weeks={row.weeks} labels={labels} />
      <div className="mt-1 flex gap-1">
        {labels.map((l) => (
          <span key={l} className="flex-1 text-center text-[10.5px] tnum text-text-secondary">
            {l}
          </span>
        ))}
      </div>
    </button>
  );
}
