'use client';

/**
 * EventStrip — その日/その週のイベント (staff_events) を緑チップで一列表示する共通部品。
 *
 * PO確定 2026-07-26: イベントは全スケジュール表 (日/週 × リスト/タイムライン) に
 * 緑で表示する。既存のイベント描画は「コースのセル/列」にぶら下がるため、
 * その日コースを持たないスタッフ (= 休みの人!) のイベントが構造的に見えなかった。
 * 本ストリップはコースに紐づかず全スタッフのイベントを必ず表示する受け皿。
 *
 * 📝 = ゼロ長 (start==end) のメモ系 (カイポケ個別業務取込)。
 */
import * as React from 'react';
import { addDays, format } from 'date-fns';

import type { StaffRead } from '@/lib/schemas/staff';
import type { EventRead } from '@/lib/schemas/staff-events';

import { getStaffEventsForWeekday } from '../v2/courseGrid';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;

export interface EventStripProps {
  staffEventsByStaff: Map<string, EventRead[]>;
  staffMap: Map<string, StaffRead>;
  /** 表示対象の曜日 (0=月..5=土)。日ビュー=[activeWeekday] / 週ビュー=[0..5]。 */
  weekdays: number[];
  /** 対象週の月曜 (日付ラベル用)。 */
  weekStart: Date;
  testId?: string;
}

interface StripEntry {
  ev: EventRead;
  staffName: string;
}

function hhmm(t: string): string {
  return t.slice(0, 5);
}

function collectDayEvents(
  weekday: number,
  staffEventsByStaff: Map<string, EventRead[]>,
  staffMap: Map<string, StaffRead>,
): StripEntry[] {
  const out: StripEntry[] = [];
  for (const staffId of staffEventsByStaff.keys()) {
    const staff = staffMap.get(staffId);
    if (!staff) continue;
    for (const ev of getStaffEventsForWeekday(staffId, weekday, staffEventsByStaff)) {
      out.push({ ev, staffName: staff.name });
    }
  }
  // 開始時刻 → スタッフ名で安定ソート (メモ 00:00 は先頭に来る)。
  out.sort(
    (a, b) =>
      a.ev.start_time.localeCompare(b.ev.start_time) ||
      a.staffName.localeCompare(b.staffName, 'ja'),
  );
  return out;
}

function EventChip({ entry }: { entry: StripEntry }) {
  const { ev, staffName } = entry;
  const isMemo = ev.start_time === ev.end_time;
  const label = ev.title || ev.type;
  return (
    <span
      className="inline-flex max-w-full items-center gap-1 truncate rounded border border-l-[3px] px-1.5 py-0.5 text-[10px] font-medium"
      style={{
        background: 'var(--sched-event-bg)',
        borderColor: 'var(--sched-event-ln)',
        borderLeftColor: 'var(--sched-event-bar)',
        color: 'var(--sched-event-ink)',
      }}
      title={`${staffName} ${isMemo ? '📝' : `${hhmm(ev.start_time)}〜${hhmm(ev.end_time)}`} ${label}${ev.note ? `\n備考: ${ev.note}` : ''}`}
      data-testid={`event-strip-chip-${ev.id}`}
    >
      {isMemo ? <span aria-hidden>📝</span> : null}
      <span className="shrink-0 font-bold">{staffName}</span>
      {!isMemo && (
        <span className="tnum shrink-0 opacity-80">
          {hhmm(ev.start_time)}〜{hhmm(ev.end_time)}
        </span>
      )}
      <span className="min-w-0 truncate">{label}</span>
    </span>
  );
}

export function EventStrip({
  staffEventsByStaff,
  staffMap,
  weekdays,
  weekStart,
  testId = 'event-strip',
}: EventStripProps) {
  const byDay = React.useMemo(
    () =>
      weekdays
        .map((wd) => ({
          wd,
          entries: collectDayEvents(wd, staffEventsByStaff, staffMap),
        }))
        .filter((d) => d.entries.length > 0),
    [weekdays, staffEventsByStaff, staffMap],
  );

  if (byDay.length === 0) return null;
  const showDayLabel = weekdays.length > 1;

  return (
    <div
      className="space-y-1 rounded-lg border border-border-subtle bg-bg-base px-2 py-1.5"
      data-testid={testId}
    >
      {byDay.map(({ wd, entries }) => (
        <div key={wd} className="flex flex-wrap items-center gap-1">
          {showDayLabel && (
            <span className="shrink-0 text-[10px] font-bold text-text-secondary">
              {format(addDays(weekStart, wd), 'M/d')}（{WEEKDAY_LABELS[wd]}）
            </span>
          )}
          {!showDayLabel && (
            <span className="shrink-0 text-[10px] font-bold text-text-secondary">イベント</span>
          )}
          {entries.map((e) => (
            <EventChip key={e.ev.id} entry={e} />
          ))}
        </div>
      ))}
    </div>
  );
}
