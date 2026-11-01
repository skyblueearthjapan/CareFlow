/**
 * 「月の実績」の最後の段「一覧を出す」の看護師別の表。表示だけ。
 *
 * 数え方は BE `services/monthly_actual/staff_report.py`（ダッシュボードのスタッフ別の実績と同じ入口）。
 * 見出しを押すと並べ替える（1 回目は大きい順、もう一度で小さい順）。空の値は常に下。
 * 最初は BE の並び（拠点 → 職員コード順）。
 */

import { useMemo, useState } from 'react';

import type { MonthlyStaffRow, MonthlyStaffSummary } from '@/lib/schemas/monthly-actual';

type SortKey =
  | 'name'
  | 'visits'
  | 'days'
  | 'per_day'
  | 'patients'
  | 'weekly_patients_avg'
  | 'accompany_visits'
  | 'provided_min_total'
  | 'avg_stay_min'
  | 'plus35'
  | 'km_total'
  | 'km_per_day'
  | 'km_per_visit';

interface Column {
  key: SortKey;
  label: string;
  value: (r: MonthlyStaffRow) => number | string | null;
  format: (r: MonthlyStaffRow) => string;
}

const dash = (v: number | null, unit = '') => (v === null ? '—' : `${v}${unit}`);
const km = (v: number | null) => (v === null ? '—' : v.toFixed(1));

const COLUMNS: ReadonlyArray<Column> = [
  { key: 'visits', label: '訪問の件数', value: (r) => r.visits, format: (r) => `${r.visits}` },
  { key: 'days', label: '稼働日数', value: (r) => r.days, format: (r) => `${r.days}` },
  { key: 'per_day', label: '1 日あたり', value: (r) => r.per_day, format: (r) => dash(r.per_day) },
  {
    key: 'patients',
    label: '患者様の数',
    value: (r) => r.patients,
    format: (r) => `${r.patients}`,
  },
  {
    key: 'weekly_patients_avg',
    label: '週の平均患者数',
    value: (r) => r.weekly_patients_avg,
    format: (r) => dash(r.weekly_patients_avg),
  },
  {
    key: 'accompany_visits',
    label: '2 名訪問・同行',
    value: (r) => r.accompany_visits,
    format: (r) => `${r.accompany_visits}`,
  },
  {
    key: 'provided_min_total',
    label: '提供時間（分）',
    value: (r) => r.provided_min_total,
    format: (r) => r.provided_min_total.toLocaleString('ja-JP'),
  },
  {
    key: 'avg_stay_min',
    label: '平均滞在（分）',
    value: (r) => r.avg_stay_min,
    format: (r) => dash(r.avg_stay_min),
  },
  {
    key: 'plus35',
    label: '到着＋35 分',
    value: (r) => r.short_stay_count + r.no_departure_count,
    format: (r) => {
      const total = r.short_stay_count + r.no_departure_count;
      return r.no_departure_count > 0
        ? `${total}（うち退出なし ${r.no_departure_count}）`
        : `${total}`;
    },
  },
  {
    key: 'km_total',
    label: '移動の合計（km）',
    value: (r) => r.km_total,
    format: (r) => km(r.km_total),
  },
  {
    key: 'km_per_day',
    label: '1 日あたり（km）',
    value: (r) => r.km_per_day,
    format: (r) => km(r.km_per_day),
  },
  {
    key: 'km_per_visit',
    label: '1 件あたり（km）',
    value: (r) => r.km_per_visit,
    format: (r) => km(r.km_per_visit),
  },
];

const NAME_COLUMN: Column = {
  key: 'name',
  label: '看護師',
  value: (r) => r.name,
  format: (r) => r.name,
};

export function sortStaffRows(
  rows: ReadonlyArray<MonthlyStaffRow>,
  key: SortKey | null,
  dir: 'asc' | 'desc',
): MonthlyStaffRow[] {
  if (!key) return [...rows];
  const col = key === 'name' ? NAME_COLUMN : COLUMNS.find((c) => c.key === key);
  if (!col) return [...rows];
  const sign = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = col.value(a);
    const vb = col.value(b);
    if (va === null && vb === null) return 0;
    if (va === null) return 1;
    if (vb === null) return -1;
    if (typeof va === 'string' || typeof vb === 'string') {
      return String(va).localeCompare(String(vb), 'ja') * sign;
    }
    return (va - vb) * sign;
  });
}

export interface MonthlyStaffTableProps {
  summary: MonthlyStaffSummary;
}

export function MonthlyStaffTable({ summary }: MonthlyStaffTableProps) {
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [dir, setDir] = useState<'asc' | 'desc'>('desc');
  const rows = useMemo(
    () => sortStaffRows(summary.staff, sortKey, dir),
    [summary.staff, sortKey, dir],
  );

  const onSort = (key: SortKey) => {
    if (sortKey === key) {
      setDir(dir === 'desc' ? 'asc' : 'desc');
    } else {
      setSortKey(key);
      setDir(key === 'name' ? 'asc' : 'desc');
    }
  };

  const header = (col: Column, align: 'left' | 'right') => {
    const active = sortKey === col.key;
    return (
      <th
        key={col.key}
        scope="col"
        aria-sort={active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}
        className={`px-2 py-2 font-medium ${align === 'right' ? 'text-right' : 'text-left'}`}
      >
        <button
          type="button"
          className="inline-flex items-center gap-0.5 hover:text-text-primary"
          onClick={() => onSort(col.key)}
          data-testid={`ma-staff-sort-${col.key}`}
        >
          {col.label}
          <span aria-hidden="true" className="text-[10px]">
            {active ? (dir === 'asc' ? '▲' : '▼') : ''}
          </span>
        </button>
      </th>
    );
  };

  if (summary.staff.length === 0) {
    return (
      <p className="rounded-md bg-bg-muted px-3 py-2 text-sm" data-testid="ma-staff-empty">
        この月の訪問はありません。
      </p>
    );
  }

  const until = summary.counted_until
    ? summary.counted_until.split('-').map(Number).slice(1).join('/')
    : null;

  return (
    <div className="space-y-2">
      {until && (
        <p className="text-xs text-text-secondary" data-testid="ma-staff-until">
          月の途中です。到着の記録なしの数と要確認は、
          {`今日（${until}）までの訪問です（今日の分は予定の終わりの時刻が過ぎたものだけ）。`}
        </p>
      )}
      <div className="overflow-x-auto rounded-lg border border-border-default">
        <table className="w-full min-w-[1100px] text-sm" data-testid="ma-staff-table">
          <thead className="bg-bg-muted text-xs text-text-secondary">
            <tr>
              {header(NAME_COLUMN, 'left')}
              {COLUMNS.map((c) => header(c, 'right'))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.staff_id}
                className="border-t border-border-default"
                data-testid="ma-staff-row"
              >
                <td className="px-2 py-1.5">
                  <span className="font-medium">{r.name}</span>
                  {r.office_short && (
                    <span className="ml-1.5 text-xs text-text-muted">{r.office_short}</span>
                  )}
                </td>
                {COLUMNS.map((c) => (
                  <td key={c.key} className="tnum px-2 py-1.5 text-right">
                    {c.format(r)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
