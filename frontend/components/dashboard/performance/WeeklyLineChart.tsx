/**
 * 週ごとの推移の折れ線 (この人 × チーム平均)。軸は 1 本・系列が 2 本以上なので凡例を必ず出す。
 * recharts は client 専用 (TrendChart と同じ作り)。
 */
'use client';

import * as React from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { TooltipProps } from 'recharts';
import type { Payload } from 'recharts/types/component/DefaultTooltipContent';

import { Card } from '@/components/ui/card';

import { PERF_COLORS } from './colors';

export interface WeeklySeries {
  key: string;
  name: string;
  color: string;
  dashed?: boolean;
}

export interface WeeklyLineChartProps {
  title: string;
  note: string;
  unit: string;
  /** 1 行 = 1 週。label は「9/7」。値が null の週は線を切る。 */
  rows: Array<{ label: string } & Record<string, number | string | null>>;
  series: WeeklySeries[];
  height?: number;
}

function formatValue(v: number | null | undefined, unit: string): string {
  if (v == null) return '—';
  return `${Math.round(v * 10) / 10} ${unit}`;
}

function makeTooltip(unit: string) {
  return function ChartTooltip({ active, payload, label }: TooltipProps<number, string>) {
    if (!active || !payload || payload.length === 0) return null;
    return (
      <div className="rounded-md border border-border-default bg-bg-base px-3 py-2 text-xs shadow-md">
        <p className="font-medium text-text-primary">{label} の週</p>
        <ul className="mt-1 space-y-0.5">
          {(payload as Payload<number, string>[]).map((entry) => (
            <li
              key={String(entry.dataKey)}
              className="flex items-center justify-between gap-3 tnum"
            >
              <span className="flex items-center gap-1.5 text-text-secondary">
                <span
                  className="inline-block h-0.5 w-3 rounded-sm"
                  style={{ backgroundColor: entry.color }}
                  aria-hidden
                />
                {entry.name}
              </span>
              <span className="font-medium text-text-primary">
                {formatValue(entry.value as number | null, unit)}
              </span>
            </li>
          ))}
        </ul>
      </div>
    );
  };
}

export function WeeklyLineChart({
  title,
  note,
  unit,
  rows,
  series,
  height = 170,
}: WeeklyLineChartProps) {
  const Tip = React.useMemo(() => makeTooltip(unit), [unit]);
  return (
    <Card className="p-4" data-testid="perf-line-chart">
      <h4 className="text-sm font-bold text-text-primary">{title}</h4>
      <p className="mb-1 text-xs text-text-secondary">{note}</p>
      <div style={{ width: '100%', height }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={PERF_COLORS.grid} vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 11, fill: PERF_COLORS.axis }}
              tickLine={false}
              axisLine={{ stroke: 'var(--border-default, #e7e5e4)' }}
            />
            <YAxis
              tick={{ fontSize: 11, fill: PERF_COLORS.axis }}
              tickLine={false}
              axisLine={false}
              width={34}
              allowDecimals={false}
              domain={[0, 'auto']}
            />
            <Tooltip content={<Tip />} cursor={{ stroke: PERF_COLORS.idle }} />
            {series.map((s) => (
              <Line
                key={s.key}
                type="linear"
                dataKey={s.key}
                name={s.name}
                stroke={s.color}
                strokeWidth={2}
                strokeDasharray={s.dashed ? '4 3' : undefined}
                dot={{ r: 3, fill: s.color, strokeWidth: 0 }}
                activeDot={{ r: 5 }}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-secondary">
        {series.map((s) => (
          <li key={s.key} className="flex items-center gap-1.5">
            <span
              className="inline-block h-0.5 w-4 rounded-sm"
              style={{ backgroundColor: s.color }}
              aria-hidden
            />
            {s.name}
          </li>
        ))}
      </ul>
    </Card>
  );
}
