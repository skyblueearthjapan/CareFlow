'use client';

/**
 * FixedEventRow — 盤面最上段の「全員（固定）」帯 (週空間 Phase E・運転席)。
 *
 * 朝会などの固定イベント (source='fixed') は全スタッフ行に散らばると盤面が
 * 埋まるため、6 曜日 × 1 行にまとめて 🔒 で示す。
 *   ・休みの人は自動で除外 → 「休:○○」
 *   ・今週だけ外した人 (cancelled_at) → 「今週 ○○除外」
 *   ・クリック → その日の参加者を 1 人ずつ「今週だけ外す / 戻す」
 *
 * StaffWeekBoard の**外**に置く (盤面本体は触らない契約)。API は呼ばず
 * `onExclude(eventId, staffId, cancel)` を親 (FE-C) へ渡す。
 */
import * as React from 'react';

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import type { WeekOverrideRead } from '@/lib/queries/staff-overrides';
import type { CockpitEventRead } from '@/lib/schemas/v2/cockpit';
import type { StaffRead } from '@/lib/schemas/staff';
import { cn } from '@/lib/utils';

const WD_JA = ['月', '火', '水', '木', '金', '土'] as const;

export interface FixedEventRowProps {
  /** source='fixed' の当週イベント全件 (cancelled も含む)。 */
  events: CockpitEventRead[];
  staffMap: Map<string, StaffRead>;
  /** `${staffId}:${weekday}` → 休み/時間変更。 */
  offByStaffWeekday?: Map<string, WeekOverrideRead>;
  /** 対象週の月曜。 */
  weekStart: Date;
  canEdit?: boolean;
  className?: string;
  /**
   * cancel=true: 今週だけ外す / false: 戻す。
   * staffId は `POST /staff/{staff_id}/events/{event_id}/cancel-week` の URL 用。
   */
  onExclude: (eventId: string, staffId: string, cancel: boolean) => void;
}

function isoOf(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`;
}

/**
 * その休み種別で、この開始時刻のイベントに出られないか。
 * 午前休/午後休は「かかっている時間帯だけ」外す (12:00 を境に判定)。
 */
function isOffForEvent(ov: WeekOverrideRead | undefined, startTime: string): boolean {
  if (!ov) return false;
  if (ov.type === '休み') return true;
  if (ov.type === '午前休') return startTime < '12:00';
  if (ov.type === '午後休') return startTime >= '12:00';
  return false; // 時間変更は勤務そのものはあるので自動除外しない
}

interface DayGroup {
  key: string;
  start: string;
  title: string;
  /** 参加者 (今週だけ外していない人)。 */
  joining: { eventId: string; staffId: string; name: string }[];
  /** 今週だけ外した人。 */
  excluded: { eventId: string; staffId: string; name: string }[];
  /** 休み (種別がこの時間帯にかかる) で自動除外される人。 */
  off: string[];
}

export function FixedEventRow({
  events,
  staffMap,
  offByStaffWeekday,
  weekStart,
  canEdit = true,
  className,
  onExclude,
}: FixedEventRowProps) {
  const dayIsos = React.useMemo(
    () =>
      Array.from({ length: 6 }, (_, i) =>
        isoOf(new Date(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() + i)),
      ),
    [weekStart],
  );

  const groupsByDay = React.useMemo(() => {
    const byDay = new Map<number, Map<string, DayGroup>>();
    for (const ev of events) {
      const weekday = dayIsos.indexOf(ev.date);
      if (weekday < 0) continue;
      const staffId = ev.staff_id ?? '';
      const name = staffMap.get(staffId)?.name ?? '（不明）';
      const key = `${ev.start_time}|${ev.title}`;
      const dayMap = byDay.get(weekday) ?? new Map<string, DayGroup>();
      const g: DayGroup = dayMap.get(key) ?? {
        key,
        start: ev.start_time,
        title: ev.title,
        joining: [],
        excluded: [],
        off: [],
      };
      const entry = { eventId: ev.id, staffId, name };
      if (ev.cancelled_at) g.excluded.push(entry);
      else if (isOffForEvent(offByStaffWeekday?.get(`${staffId}:${weekday}`), ev.start_time))
        g.off.push(name);
      else g.joining.push(entry);
      dayMap.set(key, g);
      byDay.set(weekday, dayMap);
    }
    return byDay;
  }, [events, dayIsos, staffMap, offByStaffWeekday]);

  return (
    <div
      className={cn(
        'grid grid-cols-[7rem_repeat(6,minmax(0,1fr))] rounded-t border border-border-subtle bg-bg-muted text-[11px]',
        className,
      )}
      data-testid="fixed-event-row"
      aria-label="全員（固定）の帯"
    >
      <div className="border-r border-border-subtle px-2 py-1.5">
        <div className="font-bold text-text-primary">全員（固定）</div>
        <div className="text-[10px] text-text-muted">休みの人は自動除外</div>
      </div>
      {WD_JA.map((wd, d) => {
        const groups = [...(groupsByDay.get(d)?.values() ?? [])].sort((a, b) =>
          a.start.localeCompare(b.start),
        );
        return (
          <div
            key={wd}
            className="space-y-1 border-r border-border-subtle px-1.5 py-1.5 last:border-r-0"
            data-testid={`fixed-event-day-${d}`}
          >
            {groups.length === 0 ? <span className="text-[10px] text-text-muted">—</span> : null}
            {groups.map((g) => (
              <Popover key={g.key}>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    className="w-full rounded border border-border-default bg-bg-base px-1.5 py-1 text-left hover:border-brand-primary/60"
                    data-testid={`fixed-event-band-${d}-${g.start}`}
                    title="固定イベント（型）。クリックで今週だけ外す"
                  >
                    <span aria-hidden>🔒</span> <span className="tnum">{g.start}</span> {g.title}
                    {g.off.length > 0 ? (
                      <span className="ml-1 text-[10px] text-text-muted">
                        休:{g.off.join('・')}
                      </span>
                    ) : null}
                    {g.excluded.length > 0 ? (
                      <span className="ml-1 rounded bg-warning-bg px-1 text-[10px] text-warning-strong">
                        今週 {g.excluded.map((e) => e.name).join('・')}除外
                      </span>
                    ) : null}
                  </button>
                </PopoverTrigger>
                <PopoverContent align="start" className="w-64 p-0">
                  <div className="border-b border-border-subtle px-3 py-2">
                    <p className="text-xs font-bold text-text-primary">
                      🔒 {g.title} {WD_JA[d]}曜
                    </p>
                    <p className="text-[11px] text-text-muted">
                      固定イベント（型）。全員参加（休みの人を除く）
                    </p>
                  </div>
                  <div className="space-y-1 px-3 py-2">
                    {g.joining.map((p) => (
                      <button
                        key={p.eventId}
                        type="button"
                        disabled={!canEdit}
                        onClick={() => onExclude(p.eventId, p.staffId, true)}
                        data-testid={`fixed-event-exclude-${p.eventId}`}
                        className="flex w-full items-center gap-2 rounded border border-border-default px-2 py-1 text-left text-[11px] hover:border-brand-primary/60 disabled:opacity-50"
                      >
                        <span className="text-text-muted">−</span>
                        <span className="text-text-primary">{p.name} を今週だけ外す</span>
                        <span className="ml-auto text-[10px] text-text-muted">●未送信になる</span>
                      </button>
                    ))}
                    {g.excluded.map((p) => (
                      <button
                        key={p.eventId}
                        type="button"
                        disabled={!canEdit}
                        onClick={() => onExclude(p.eventId, p.staffId, false)}
                        data-testid={`fixed-event-restore-${p.eventId}`}
                        className="flex w-full items-center gap-2 rounded border border-dashed border-border-default px-2 py-1 text-left text-[11px] hover:border-brand-primary/60 disabled:opacity-50"
                      >
                        <span className="text-text-muted">↶</span>
                        <span className="text-text-primary">{p.name} を戻す</span>
                      </button>
                    ))}
                    {g.off.length > 0 ? (
                      <p className="text-[10px] text-text-muted">
                        休み: {g.off.join('・')}（自動で除外されています）
                      </p>
                    ) : null}
                  </div>
                </PopoverContent>
              </Popover>
            ))}
          </div>
        );
      })}
    </div>
  );
}
