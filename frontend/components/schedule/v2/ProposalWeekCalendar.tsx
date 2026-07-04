'use client';

/**
 * ProposalWeekCalendar — W41 v2 「全体」タブ用 週カレンダー Before/After.
 *
 * `/schedule` の「週」ビュー風に **コース行 × 曜日列** で提案結果を俯瞰する.
 * 1 つの side ('before' | 'after') を 1 グリッドとして描画し、呼び出し側で
 * Before/After を縦に積む.
 *
 * 行軸: 「拠点 + コース」(例: 稲毛 A コース). スタッフ未アサインでも
 *       コース割当は完了しているため、コース粒度で俯瞰する.
 *       各コースに担当スタッフ名がある場合は行ラベルに併記.
 * 列軸: 月〜土 (DISPLAY_WEEKDAYS).
 * セル: その日に当該コースで訪問される patient のチップ (時刻 + 名前).
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

interface ProposalWeekCalendarProps {
  proposals: V2WeekdayBeforeAfter[];
  side: 'before' | 'after';
  /** staff_id -> 表示名. 担当スタッフ名を行ラベルに併記するために使用. */
  staffNameById: Map<string, string>;
}

interface CourseRow {
  /** 行の安定 key. */
  key: string;
  officeName: string;
  code: string;
  /** 表示名: "稲毛 Aコース". */
  label: string;
  /** weekday -> visits */
  byWeekday: Map<number, V2VisitForUI[]>;
  /** weekday -> 担当 staff id set (複数曜日で別スタッフが担当する場合もある). */
  staffIdsByWeekday: Map<number, Set<string>>;
}

/** 拠点+コード 単位でコースを集約し、行リストを構築. */
function buildCourseRows(proposals: V2WeekdayBeforeAfter[], side: 'before' | 'after'): CourseRow[] {
  const rows = new Map<string, CourseRow>();

  for (const wp of proposals) {
    const courses: V2CourseSummary[] = (side === 'before' ? wp.before : wp.after).courses;
    for (const c of courses) {
      const officeName = c.office_name ?? '不明';
      const key = `${officeName}|${c.code}`;
      if (!rows.has(key)) {
        rows.set(key, {
          key,
          officeName,
          code: c.code,
          label: `${officeName} ${c.code}コース`,
          byWeekday: new Map(),
          staffIdsByWeekday: new Map(),
        });
      }
      const row = rows.get(key)!;
      if (!row.byWeekday.has(wp.weekday)) row.byWeekday.set(wp.weekday, []);
      row.byWeekday.get(wp.weekday)!.push(...c.visits);
      if (c.assigned_staff_id) {
        if (!row.staffIdsByWeekday.has(wp.weekday)) {
          row.staffIdsByWeekday.set(wp.weekday, new Set());
        }
        row.staffIdsByWeekday.get(wp.weekday)!.add(c.assigned_staff_id);
      }
    }
  }

  // 各セルを start_time で昇順ソート.
  for (const row of rows.values()) {
    for (const list of row.byWeekday.values()) {
      list.sort((a, b) => a.start_time.localeCompare(b.start_time));
    }
  }

  // 行のソート: (拠点名, コード) で安定化.
  return Array.from(rows.values()).sort((a, b) => {
    if (a.officeName !== b.officeName) return a.officeName.localeCompare(b.officeName);
    return a.code.localeCompare(b.code);
  });
}

export function ProposalWeekCalendar({
  proposals,
  side,
  staffNameById,
}: ProposalWeekCalendarProps) {
  const rows = React.useMemo(() => buildCourseRows(proposals, side), [proposals, side]);

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
        {side === 'after' ? '変更後 週ビュー' : '変更前 週ビュー'}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[10px]">
          <thead>
            <tr className="bg-bg-muted text-text-muted">
              <th className="sticky left-0 z-10 border-b border-r border-border-default bg-bg-muted px-2 py-1 text-left">
                コース
              </th>
              {DISPLAY_WEEKDAYS.map((wd) => (
                <th key={wd} className="border-b border-border-default px-1 py-1 text-center">
                  {WEEKDAY_LABELS[wd]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td
                  colSpan={DISPLAY_WEEKDAYS.length + 1}
                  className="py-3 text-center text-[10px] text-text-muted"
                >
                  (提案なし)
                </td>
              </tr>
            ) : (
              rows.map((row) => {
                return (
                  <tr key={row.key} className="border-b border-border-default last:border-b-0">
                    <td className="sticky left-0 z-10 border-r border-border-default bg-bg-default px-2 py-1 align-top">
                      <div className="text-[10px] font-semibold text-text-primary">{row.label}</div>
                    </td>
                    {DISPLAY_WEEKDAYS.map((wd) => {
                      const visits = row.byWeekday.get(wd) ?? [];
                      const staffIds = row.staffIdsByWeekday.get(wd);
                      const staffNames = staffIds
                        ? Array.from(staffIds)
                            .map((id) => staffNameById.get(id))
                            .filter((s): s is string => !!s)
                        : [];
                      return (
                        <td
                          key={wd}
                          className="min-w-[88px] border-r border-border-default px-1 py-1 align-top last:border-r-0"
                        >
                          {visits.length === 0 ? (
                            <span className="text-[9px] text-text-muted">—</span>
                          ) : (
                            <>
                              {staffNames.length > 0 ? (
                                <div className="mb-0.5 text-[8px] text-brand-primary">
                                  👤 {staffNames.join(', ')}
                                </div>
                              ) : null}
                              <ul className="space-y-0.5">
                                {visits.map((v: V2VisitForUI, i: number) => (
                                  <li
                                    key={`${v.patient_id}-${i}`}
                                    className="flex flex-wrap items-center gap-0.5 rounded bg-bg-muted/60 px-1 py-0.5 leading-tight"
                                  >
                                    <span className="tnum text-text-muted">
                                      {trimSeconds(v.start_time)}
                                    </span>
                                    <span className="text-text-primary">{v.patient_name}</span>
                                  </li>
                                ))}
                              </ul>
                            </>
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
