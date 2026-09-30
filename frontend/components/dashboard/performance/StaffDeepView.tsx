/**
 * C: 1 人を深く見る。週ごとの推移をチーム平均と重ねた折れ線 4 枚 + 1 日の内訳 + 週ごとの表。
 * 見た目の正典: docs/mockups/dashboard-staff-performance-mock.html (C)。
 */
'use client';

import * as React from 'react';

import { Card } from '@/components/ui/card';
import { hhmm, monthDay, r0, r1 } from '@/lib/dashboard-period';
import type { PerformanceMetrics, StaffPerformanceResponse } from '@/lib/schemas/dashboard';
import { cn } from '@/lib/utils';

import { PERF_COLORS } from './colors';
import { Tag, actualText, roleTags } from './StaffPerformanceCard';
import { WeeklyLineChart } from './WeeklyLineChart';

const SELF = { key: 'self', name: 'この人', color: PERF_COLORS.self };
const TEAM = { key: 'team', name: 'チーム平均', color: PERF_COLORS.team, dashed: true };

const PARTS = [
  { key: 'visit_min_per_day', name: '訪問', color: PERF_COLORS.self },
  { key: 'travel_min_per_day', name: '移動', color: PERF_COLORS.travel },
  { key: 'meeting_min_per_day', name: '会議・研修など', color: PERF_COLORS.meeting },
  { key: 'idle_min_per_day', name: '訪問の合間', color: PERF_COLORS.idle },
] as const;

function BreakdownBar({ label, m, max }: { label: string; m: PerformanceMetrics; max: number }) {
  const total = PARTS.reduce((t, p) => t + (m[p.key] ?? 0), 0);
  return (
    <div className="flex items-center gap-3">
      <span className="w-20 shrink-0 text-xs text-text-secondary">{label}</span>
      <div className="flex h-4 flex-1 gap-0.5" role="img" aria-label={`${label}の 1 日の内訳`}>
        {PARTS.map((p) => {
          const v = m[p.key] ?? 0;
          if (v <= 0) return null;
          return (
            <span
              key={p.key}
              className="block h-full first:rounded-l last:rounded-r"
              style={{ width: `${(v / max) * 100}%`, backgroundColor: p.color }}
              title={`${p.name} ${hhmm(v)}（1 日あたり）`}
            />
          );
        })}
      </div>
      <span className="w-12 shrink-0 text-right text-xs tnum text-text-secondary">
        {hhmm(total)}
      </span>
    </div>
  );
}

export interface StaffDeepViewProps {
  data: StaffPerformanceResponse;
  staffId: string;
  onSelect: (staffId: string) => void;
  onBack: () => void;
}

export function StaffDeepView({ data, staffId, onSelect, onBack }: StaffDeepViewProps) {
  const row = data.staff.find((s) => s.staff_id === staffId);
  if (!row) return null;
  const labels = data.weeks.map((w) => monthDay(w.start));
  const has = (m: PerformanceMetrics) => m.days > 0;
  const rows = (pick: (m: PerformanceMetrics) => number | null) =>
    row.weeks.map((m, i) => ({
      label: labels[i] ?? '',
      self: has(m) ? pick(m) : null,
      team: pick(data.team.weeks[i] ?? m),
      actual: m.actual_min,
    }));
  const m = row.period;
  const team = data.team.period;
  const max = Math.max(1, ...[m, team].map((x) => PARTS.reduce((t, p) => t + (x[p.key] ?? 0), 0)));
  const speed = data.travel_speed_kmh;

  return (
    <div className="space-y-3" data-testid="perf-deep-view">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onBack}
          className="rounded-md border border-border-default bg-bg-base px-3 py-1 text-sm text-text-secondary hover:bg-bg-muted"
        >
          ← 全員のカードに戻る
        </button>
        <span className="ml-2 text-xs text-text-secondary">職員</span>
        <div className="flex flex-wrap gap-1.5">
          {data.staff.map((s) => (
            <button
              key={s.staff_id}
              type="button"
              onClick={() => onSelect(s.staff_id)}
              aria-pressed={s.staff_id === staffId}
              className={cn(
                'rounded-full border px-3.5 py-1 text-[13px]',
                s.staff_id === staffId
                  ? 'border-text-primary bg-text-primary font-bold text-bg-base'
                  : 'border-border-default bg-bg-base text-text-secondary hover:bg-bg-muted',
              )}
            >
              {s.name}
            </button>
          ))}
        </div>
      </div>

      <Card className="px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-2">
          <h2 className="text-lg font-bold text-text-primary">{row.name}</h2>
          <div className="flex flex-wrap gap-1">
            {roleTags(row).map((t) => (
              <Tag key={t}>{t}</Tag>
            ))}
          </div>
          <p className="text-sm tnum text-text-secondary">
            出勤 {m.days} 日・訪問 {m.visits} 件・利用者 {m.patients} 名・1 日あたり {r1(m.per_day)}{' '}
            件・1 回あたり {r0(m.plan_min)} 分（予定）・
            {actualText(m, data.min_actual_samples)}
            {m.no_show_count > 0
              ? `・未訪問の記録 ${m.no_show_count} 件（件数には含めています）`
              : ''}
          </p>
        </div>
      </Card>

      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(300px,1fr))]">
        <WeeklyLineChart
          title="1 日あたりの訪問件数"
          note="訪問件数 ÷ 訪問のあった日数"
          unit="件"
          rows={rows((x) => x.per_day)}
          series={[SELF, TEAM]}
        />
        <WeeklyLineChart
          title="1 回あたりの時間"
          note={`予定の長さと、QR で取れた実績（${data.min_actual_samples} 件以上ある週だけ）`}
          unit="分"
          rows={rows((x) => x.plan_min)}
          series={[
            { ...SELF, name: 'この人（予定）' },
            { key: 'actual', name: '実績（QR）', color: PERF_COLORS.actual },
            TEAM,
          ]}
        />
        <WeeklyLineChart
          title="1 日あたりの移動距離"
          note="拠点からの行き帰りを含む直線距離"
          unit="km"
          rows={rows((x) => x.km_per_day)}
          series={[SELF, TEAM]}
        />
        <WeeklyLineChart
          title="1 日あたりの訪問の合間"
          note="訪問と訪問の間から、移動と会議・研修などを引いた時間"
          unit="分"
          rows={rows((x) => x.idle_min_per_day)}
          series={[SELF, TEAM]}
        />
      </div>

      <Card className="space-y-2 px-4 py-3" data-testid="perf-breakdown">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h4 className="text-sm font-bold text-text-primary">1 日の内訳（1 日あたり）</h4>
          <p className="text-xs tnum text-text-secondary">
            1 件あたりの移動 {r1(m.km_per_visit)} km（チーム平均 {r1(team.km_per_visit)} km）
          </p>
        </div>
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-secondary">
          {PARTS.map((p) => (
            <li key={p.key} className="flex items-center gap-1.5">
              <span
                className="inline-block h-3 w-3 rounded-sm"
                style={{ backgroundColor: p.color }}
                aria-hidden
              />
              {p.name} {hhmm(m[p.key])}
            </li>
          ))}
        </ul>
        <BreakdownBar label="この人" m={m} max={max} />
        <BreakdownBar label="チーム平均" m={team} max={max} />
        <p className="text-xs text-text-secondary">
          訪問 = 予定の長さの合計／移動 = 直線距離 ÷ 時速 {speed}km（スケジュール計算と同じ前提）／
          会議・研修など = 休み以外の予定のうち、最初の訪問から最後の訪問までに重なる時間／合間 =
          訪問と訪問の間から移動と会議・研修などを引いた残り（記録・待ちなどを含みます）
        </p>
        {m.skipped_legs > 0 ? (
          <p className="text-xs text-text-secondary">
            住所の位置が登録されていないため、距離に入れていない区間が {m.skipped_legs}{' '}
            か所あります。その区間の移動は引けないため、訪問の合間が長めに出ることがあります。
          </p>
        ) : null}
      </Card>

      <Card className="overflow-x-auto">
        <table className="w-full border-collapse text-[13px]" data-testid="perf-week-table">
          <thead>
            <tr className="text-xs text-text-secondary">
              {[
                '週',
                '出勤',
                '訪問',
                '利用者',
                '1 日あたり',
                '1 回あたり（予定）',
                '実績（QR）',
                '移動 km/日',
                'km/件',
                '会議・研修など（週計）',
                '合間/日',
              ].map((h, i) => (
                <th
                  key={h}
                  className={cn(
                    'whitespace-nowrap border-b border-border-default px-2.5 py-2 font-medium',
                    i === 0 ? 'text-left' : 'text-right',
                  )}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {row.weeks.map((w, i) => (
              <tr key={labels[i]} className="tnum">
                <td className="whitespace-nowrap border-b border-border-default px-2.5 py-1.5 text-left">
                  {labels[i]} の週
                </td>
                {[
                  `${w.days} 日`,
                  `${w.visits} 件`,
                  `${w.patients} 名`,
                  r1(w.per_day),
                  has(w) ? `${r0(w.plan_min)} 分` : '—',
                  w.actual_min != null ? `${r0(w.actual_min)} 分（${w.actual_samples} 件）` : '—',
                  r1(w.km_per_day),
                  r1(w.km_per_visit),
                  hhmm(w.meeting_min_total),
                  has(w) ? hhmm(w.idle_min_per_day) : '—',
                ].map((v, j) => (
                  <td
                    key={j}
                    className="whitespace-nowrap border-b border-border-default px-2.5 py-1.5 text-right"
                  >
                    {v}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
