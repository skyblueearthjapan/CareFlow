/**
 * TrendChart — Recharts wrapper that renders a 3-line series
 * (total / completed / unassigned) for the dashboard.
 *
 * Recharts is a client-only library, so this file MUST stay marked
 * 'use client'. Tooltip and axis text use the design tokens defined in
 * `app/globals.css`.
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

import type { DashboardTrendItem } from '@/lib/queries/dashboard';

export interface TrendChartProps {
  data: DashboardTrendItem[];
  /** Pixel height of the chart container. Default 260. */
  height?: number;
}

interface ChartRow {
  date: string;
  /** Short JP label (e.g. "5/05"). */
  label: string;
  total: number;
  completed: number;
  unassigned: number;
}

function formatLabel(iso: string): string {
  // Inputs are always YYYY-MM-DD from the backend; avoid Date timezone math.
  const [, m, d] = iso.split('-');
  if (!m || !d) return iso;
  return `${Number(m)}/${d}`;
}

const SERIES = [
  { key: 'total', name: '合計', color: '#0f766e' },
  { key: 'completed', name: '完了', color: '#2563eb' },
  { key: 'unassigned', name: '未割当', color: '#dc2626' },
] as const;

function ChartTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="rounded-md border border-border-default bg-bg-base px-3 py-2 text-xs shadow-md">
      <p className="font-medium text-text-primary">{label}</p>
      <ul className="mt-1 space-y-0.5">
        {(payload as Payload<number, string>[]).map((entry) => (
          <li
            key={String(entry.dataKey)}
            className="flex items-center justify-between gap-3 tnum"
            style={{ color: entry.color }}
          >
            <span>{entry.name}</span>
            <span className="font-medium">{entry.value ?? 0}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function TrendChart({ data, height = 260 }: TrendChartProps) {
  const rows: ChartRow[] = React.useMemo(
    () =>
      data.map((d) => ({
        date: d.date,
        label: formatLabel(d.date),
        total: d.total,
        completed: d.completed,
        unassigned: d.unassigned,
      })),
    [data],
  );

  return (
    <div style={{ width: '100%', height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--border-default, #e5e7eb)" strokeDasharray="3 3" />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 12, fill: 'var(--text-secondary, #475569)' }}
            tickLine={false}
            axisLine={{ stroke: 'var(--border-default, #e5e7eb)' }}
          />
          <YAxis
            allowDecimals={false}
            tick={{ fontSize: 12, fill: 'var(--text-secondary, #475569)' }}
            tickLine={false}
            axisLine={{ stroke: 'var(--border-default, #e5e7eb)' }}
            width={32}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ stroke: '#94a3b8', strokeDasharray: '3 3' }} />
          {SERIES.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.name}
              stroke={s.color}
              strokeWidth={2}
              dot={{ r: 3 }}
              activeDot={{ r: 5 }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-secondary">
        {SERIES.map((s) => (
          <li key={s.key} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2 w-3 rounded-sm"
              style={{ backgroundColor: s.color }}
              aria-hidden
            />
            {s.name}
          </li>
        ))}
      </ul>
    </div>
  );
}
