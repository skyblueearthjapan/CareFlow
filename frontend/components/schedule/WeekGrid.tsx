'use client';

/**
 * Staff × day grid for a single ISO week.
 *
 * Layout: rows = staff, columns = Mon–Sun. Visits are bucketed by
 * `(staffId, visit_date)` where `staffId` is the chip's "owning" staff —
 * primary, secondary (同行), or mentor (指導). The same visit may appear in
 * up to three rows so co-visits are visible from each staff member's view.
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
  /** False (staff role) makes chips non-interactive. */
  canEditVisit?: boolean;
  onVisitClick?: (visit: VisitRead) => void;
  /** Optional lookup `office_id -> short label` for the chip suffix. */
  officeLabel?: (officeId: string | null | undefined) => string | null;
}

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;
const FULL_WEEKDAY_LABELS = [
  '月曜日',
  '火曜日',
  '水曜日',
  '木曜日',
  '金曜日',
  '土曜日',
  '日曜日',
] as const;

type ChipRole = 'primary' | 'secondary' | 'mentor';

interface ChipEntry {
  visit: VisitRead;
  role: ChipRole;
}

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

function roleBadge(role: ChipRole): string | null {
  if (role === 'secondary') return '同行';
  if (role === 'mentor') return '指導';
  return null;
}

export function WeekGrid({
  weekStart,
  staff,
  visits,
  maxPerDay = 6,
  canEditVisit = true,
  onVisitClick,
  officeLabel,
}: WeekGridProps) {
  const days = useMemo(
    () => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)),
    [weekStart],
  );

  /** Bucket: `${staffId}|${yyyy-MM-dd}` -> entries sorted by start_time.
   * A visit fans out to every staff slot it occupies (primary / secondary /
   * mentor) so each staff row shows their own co-visits. */
  const buckets = useMemo(() => {
    const map = new Map<string, ChipEntry[]>();
    const push = (sid: string, entry: ChipEntry) => {
      const key = `${sid}|${entry.visit.visit_date}`;
      const arr = map.get(key);
      if (arr) arr.push(entry);
      else map.set(key, [entry]);
    };
    for (const v of visits) {
      if (v.primary_staff_id) {
        push(v.primary_staff_id, { visit: v, role: 'primary' });
      }
      if (v.secondary_staff_id && v.secondary_staff_id !== v.primary_staff_id) {
        push(v.secondary_staff_id, { visit: v, role: 'secondary' });
      }
      if (
        v.mentor_staff_id &&
        v.mentor_staff_id !== v.primary_staff_id &&
        v.mentor_staff_id !== v.secondary_staff_id
      ) {
        push(v.mentor_staff_id, { visit: v, role: 'mentor' });
      }
    }
    for (const arr of map.values()) {
      arr.sort(
        (a, b) =>
          timeToMinutes(a.visit.start_time) - timeToMinutes(b.visit.start_time),
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
          if (overlaps(arr[i]!.visit, arr[j]!.visit)) {
            overlapping.add(arr[i]!.visit.id);
            overlapping.add(arr[j]!.visit.id);
          }
        }
      }
      if (overCap || overlapping.size > 0) {
        for (const e of arr) {
          if (overCap || overlapping.has(e.visit.id)) out.add(e.visit.id);
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

  const weekLabel = `${format(weekStart, 'yyyy年M月d日', { locale: ja })} 週`;

  return (
    <div className="overflow-x-auto rounded-md border border-border-default bg-bg-base">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">{weekLabel}の週次スケジュール</caption>
        <thead>
          <tr className="border-b border-border-default bg-bg-muted/50 text-text-secondary">
            <th
              scope="col"
              className="sticky left-0 z-10 min-w-[10rem] bg-bg-muted/50 px-3 py-2 text-left font-medium"
            >
              スタッフ
            </th>
            {days.map((d, i) => {
              const ariaLabel = `${format(d, 'M月d日', { locale: ja })} ${FULL_WEEKDAY_LABELS[i]}`;
              return (
                <th
                  key={d.toISOString()}
                  scope="col"
                  aria-label={ariaLabel}
                  className="min-w-[9rem] px-2 py-2 text-left font-medium"
                >
                  <div className="flex items-baseline gap-2">
                    <span className="tnum">{format(d, 'M/d', { locale: ja })}</span>
                    <span className="text-xs text-text-muted">
                      ({WEEKDAY_LABELS[i]})
                    </span>
                  </div>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {staff.map((s) => (
            <tr key={s.id} className="border-b border-border-default last:border-0">
              <th
                scope="row"
                className="sticky left-0 z-10 min-w-[10rem] bg-bg-base px-3 py-2 text-left align-top font-medium text-text-primary"
              >
                {s.name}
              </th>
              {days.map((d) => {
                const key = `${s.id}|${dateKey(d)}`;
                const cellEntries = buckets.get(key) ?? [];
                const overCap = cellEntries.length > maxPerDay;
                return (
                  <td
                    key={key}
                    className={cn(
                      'min-w-[9rem] border-l border-border-default px-1 py-1 align-top',
                      overCap && 'bg-error/5',
                    )}
                  >
                    {cellEntries.length === 0 ? (
                      <div className="h-6" aria-hidden />
                    ) : (
                      <div className="flex flex-col gap-1">
                        {cellEntries.map((e) => (
                          <VisitChip
                            key={`${e.visit.id}|${e.role}`}
                            visit={e.visit}
                            tone={flagged.has(e.visit.id) ? 'warning' : 'default'}
                            canEdit={canEditVisit}
                            onClick={onVisitClick}
                            officeLabel={
                              officeLabel
                                ? officeLabel(s.primary_office_id)
                                : null
                            }
                            roleBadge={roleBadge(e.role)}
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
