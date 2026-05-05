'use client';

/**
 * Staff × day grid for a single ISO week.
 *
 * Layout: rows = staff, columns = Mon–Sun. Visits are bucketed by
 * `(primary_staff_id, visit_date)` and rendered as `<VisitChip>` stacks.
 *
 * Highlighting:
 *  - cells over `maxPerDay` are flagged on every chip in that cell.
 *  - chips that overlap another chip in the same cell are flagged too.
 *
 * Drag&drop is intentionally out of scope — Phase 4-9 will add it.
 */
import { useMemo } from 'react';
import { format } from 'date-fns';
import { ja } from 'date-fns/locale';

import { cn } from '@/lib/utils';
import type { StaffRead } from '@/lib/schemas/staff';
import type { VisitRead } from '@/lib/schemas/visit';
import { VisitChip } from '@/components/schedule/VisitChip';
import { addDays } from '@/components/schedule/WeekSelector';

interface WeekGridProps {
  weekStart: Date;
  staff: StaffRead[];
  visits: VisitRead[];
  /** Soft cap; cells exceeding this count are highlighted in red. */
  maxPerDay?: number;
  onVisitClick?: (visit: VisitRead) => void;
  /** Optional lookup `office_id -> short label` for the chip suffix. */
  officeLabel?: (officeId: string | null | undefined) => string | null;
}

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

function dateKey(d: Date): string {
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

function timeToMinutes(t: string | null | undefined): number {
  if (!t) return 0;
  const [hh, mm] = t.split(':');
  return Number(hh) * 60 + Number(mm);
}

function overlaps(a: VisitRead, b: VisitRead): boolean {
  const a0 = timeToMinutes(a.start_time);
  const a1 = timeToMinutes(a.end_time);
  const b0 = timeToMinutes(b.start_time);
  const b1 = timeToMinutes(b.end_time);
  return a0 < b1 && b0 < a1;
}

export function WeekGrid({
  weekStart,
  staff,
  visits,
  maxPerDay = 6,
  onVisitClick,
  officeLabel,
}: WeekGridProps) {
  const days = useMemo(
    () => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)),
    [weekStart],
  );

  /** Bucket: `${staffId}|${yyyy-MM-dd}` -> visits sorted by start_time. */
  const buckets = useMemo(() => {
    const map = new Map<string, VisitRead[]>();
    for (const v of visits) {
      const sid = v.primary_staff_id ?? '__unassigned__';
      const key = `${sid}|${v.visit_date}`;
      const arr = map.get(key);
      if (arr) arr.push(v);
      else map.set(key, [v]);
    }
    for (const arr of map.values()) {
      arr.sort(
        (a, b) => timeToMinutes(a.start_time) - timeToMinutes(b.start_time),
      );
    }
    return map;
  }, [visits]);

  /** Set of visit ids that should render with the warning tone. */
  const flagged = useMemo(() => {
    const out = new Set<string>();
    for (const [key, arr] of buckets) {
      const overCap = arr.length > maxPerDay;
      const overlapping = new Set<string>();
      for (let i = 0; i < arr.length; i += 1) {
        for (let j = i + 1; j < arr.length; j += 1) {
          if (overlaps(arr[i]!, arr[j]!)) {
            overlapping.add(arr[i]!.id);
            overlapping.add(arr[j]!.id);
          }
        }
      }
      if (overCap || overlapping.size > 0) {
        for (const v of arr) {
          if (overCap || overlapping.has(v.id)) out.add(v.id);
        }
      }
      // referencing key to satisfy lint without wasted work
      void key;
    }
    return out;
  }, [buckets, maxPerDay]);

  if (staff.length === 0) {
    return (
      <p className="rounded-md border border-border-default bg-bg-base p-6 text-center text-sm text-text-muted">
        スタッフが登録されていません。
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-md border border-border-default bg-bg-base">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-border-default bg-bg-muted/50 text-text-secondary">
            <th className="sticky left-0 z-10 min-w-[10rem] bg-bg-muted/50 px-3 py-2 text-left font-medium">
              スタッフ
            </th>
            {days.map((d, i) => (
              <th
                key={d.toISOString()}
                className="min-w-[9rem] px-2 py-2 text-left font-medium"
              >
                <div className="flex items-baseline gap-2">
                  <span className="tnum">{format(d, 'M/d', { locale: ja })}</span>
                  <span className="text-xs text-text-muted">
                    ({WEEKDAY_LABELS[i]})
                  </span>
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {staff.map((s) => (
            <tr key={s.id} className="border-b border-border-default last:border-0">
              <td className="sticky left-0 z-10 min-w-[10rem] bg-bg-base px-3 py-2 align-top font-medium text-text-primary">
                {s.name}
              </td>
              {days.map((d) => {
                const key = `${s.id}|${dateKey(d)}`;
                const cellVisits = buckets.get(key) ?? [];
                const overCap = cellVisits.length > maxPerDay;
                return (
                  <td
                    key={key}
                    className={cn(
                      'min-w-[9rem] border-l border-border-default px-1 py-1 align-top',
                      overCap && 'bg-error/5',
                    )}
                  >
                    {cellVisits.length === 0 ? (
                      <div className="h-6" aria-hidden />
                    ) : (
                      <div className="flex flex-col gap-1">
                        {cellVisits.map((v) => (
                          <VisitChip
                            key={v.id}
                            visit={v}
                            tone={flagged.has(v.id) ? 'warning' : 'default'}
                            onClick={onVisitClick}
                            officeLabel={
                              officeLabel
                                ? officeLabel(s.primary_office_id)
                                : null
                            }
                          />
                        ))}
                      </div>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
