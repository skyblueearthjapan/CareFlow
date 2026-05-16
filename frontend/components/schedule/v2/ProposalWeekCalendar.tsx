'use client';

/**
 * ProposalWeekCalendar — W41 v2 「全体」タブ用 週カレンダー Before/After.
 *
 * `/schedule` の「週」ビュー風に スタッフ行 × 曜日列 で提案結果を俯瞰する.
 * 1 つの side ('before' | 'after') を 1 グリッドとして描画し、呼び出し側で
 * Before/After を縦に積む.
 *
 * 行軸: assigned_staff_id がある course の担当スタッフ + 「未アサイン」.
 *       Before は固定枠ベースで assigned_staff_id が無いことが多いので
 *       基本「未アサイン」行に集約される.
 * 列軸: 月〜土 (DISPLAY_WEEKDAYS).
 * セル: その日に当該スタッフが訪問する patient のチップ (時刻 + 名前).
 */
import * as React from 'react';

import { cn } from '@/lib/utils';
import type {
  V2CourseSummary,
  V2VisitForUI,
  V2WeekdayBeforeAfter,
} from '@/lib/schemas/v2/autoScheduleV2';

import { trimSeconds } from './_autoScheduleUtils';

const DISPLAY_WEEKDAYS = [0, 1, 2, 3, 4, 5] as const;
const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;

const UNASSIGNED_KEY = '__UNASSIGNED__';

interface ProposalWeekCalendarProps {
  proposals: V2WeekdayBeforeAfter[];
  side: 'before' | 'after';
  /** staff_id -> 表示名. 無ければ短縮 UUID にフォールバック. */
  staffNameById: Map<string, string>;
}

interface CellVisit {
  visit: V2VisitForUI;
  courseCode: string;
  officeName: string;
}

/** (staff_id key, weekday) -> visit リスト の二重 map を構築. */
function buildCellMap(
  proposals: V2WeekdayBeforeAfter[],
  side: 'before' | 'after',
): { rows: string[]; cells: Map<string, Map<number, CellVisit[]>> } {
  const cells = new Map<string, Map<number, CellVisit[]>>();
  const rowSet = new Set<string>();

  for (const wp of proposals) {
    const courses: V2CourseSummary[] = (side === 'before' ? wp.before : wp.after).courses;
    for (const c of courses) {
      const key = c.assigned_staff_id ?? UNASSIGNED_KEY;
      rowSet.add(key);
      if (!cells.has(key)) cells.set(key, new Map());
      const byWd = cells.get(key)!;
      if (!byWd.has(wp.weekday)) byWd.set(wp.weekday, []);
      const bucket = byWd.get(wp.weekday)!;
      for (const v of c.visits) {
        bucket.push({
          visit: v,
          courseCode: c.code,
          officeName: c.office_name ?? '不明',
        });
      }
    }
  }

  // 各セルを start_time で昇順ソート.
  for (const byWd of cells.values()) {
    for (const list of byWd.values()) {
      list.sort((a, b) => a.visit.start_time.localeCompare(b.visit.start_time));
    }
  }

  return { rows: Array.from(rowSet), cells };
}

function shortenUuid(uuid: string): string {
  // UUID は表示用に最初 8 文字だけにする (UUID 全長は読みにくい).
  return uuid.slice(0, 8);
}

export function ProposalWeekCalendar({
  proposals,
  side,
  staffNameById,
}: ProposalWeekCalendarProps) {
  const { rows, cells } = React.useMemo(() => buildCellMap(proposals, side), [proposals, side]);

  // 行のソート: 未アサインを最後、それ以外は staff 名で.
  const sortedRows = React.useMemo(() => {
    const named = rows.filter((r) => r !== UNASSIGNED_KEY);
    named.sort((a, b) => {
      const na = staffNameById.get(a) ?? a;
      const nb = staffNameById.get(b) ?? b;
      return na.localeCompare(nb);
    });
    return rows.includes(UNASSIGNED_KEY) ? [...named, UNASSIGNED_KEY] : named;
  }, [rows, staffNameById]);

  const sideHeaderCls =
    side === 'after'
      ? 'border-brand-primary/40 bg-brand-primary/5 text-brand-primary'
      : 'border-border-default bg-bg-muted text-text-muted';

  return (
    <div
      className="overflow-hidden rounded border border-border-default"
      data-testid={`proposal-week-calendar-${side}`}
    >
      <div className={cn('border-b px-2 py-1 text-[11px] font-semibold', sideHeaderCls)}>
        {side === 'after' ? 'After 週ビュー' : 'Before 週ビュー'}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[10px]">
          <thead>
            <tr className="bg-bg-muted text-text-muted">
              <th className="sticky left-0 z-10 border-b border-r border-border-default bg-bg-muted px-2 py-1 text-left">
                スタッフ
              </th>
              {DISPLAY_WEEKDAYS.map((wd) => (
                <th key={wd} className="border-b border-border-default px-1 py-1 text-center">
                  {WEEKDAY_LABELS[wd]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sortedRows.length === 0 ? (
              <tr>
                <td
                  colSpan={DISPLAY_WEEKDAYS.length + 1}
                  className="py-3 text-center text-[10px] text-text-muted"
                >
                  (提案なし)
                </td>
              </tr>
            ) : (
              sortedRows.map((rowKey) => {
                const name =
                  rowKey === UNASSIGNED_KEY
                    ? '未アサイン'
                    : (staffNameById.get(rowKey) ?? shortenUuid(rowKey));
                const byWd = cells.get(rowKey) ?? new Map();
                return (
                  <tr key={rowKey} className="border-b border-border-default last:border-b-0">
                    <td
                      className={cn(
                        'sticky left-0 z-10 border-r border-border-default bg-bg-default px-2 py-1 align-top text-[10px] font-semibold',
                        rowKey === UNASSIGNED_KEY ? 'text-text-muted italic' : 'text-text-primary',
                      )}
                    >
                      {name}
                    </td>
                    {DISPLAY_WEEKDAYS.map((wd) => {
                      const items = byWd.get(wd) ?? [];
                      return (
                        <td
                          key={wd}
                          className="min-w-[88px] border-r border-border-default px-1 py-1 align-top last:border-r-0"
                        >
                          {items.length === 0 ? (
                            <span className="text-[9px] text-text-muted">—</span>
                          ) : (
                            <ul className="space-y-0.5">
                              {items.map((it: CellVisit, i: number) => (
                                <li
                                  key={`${it.visit.patient_id}-${i}`}
                                  className="flex flex-wrap items-center gap-0.5 rounded bg-bg-muted/60 px-1 py-0.5 leading-tight"
                                  title={`${it.officeName} ${it.courseCode}コース`}
                                >
                                  <span className="tnum text-text-muted">
                                    {trimSeconds(it.visit.start_time)}
                                  </span>
                                  <span className="text-text-primary">{it.visit.patient_name}</span>
                                </li>
                              ))}
                            </ul>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
