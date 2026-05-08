'use client';

/**
 * StaffTimeGrid — 1 スタッフ分の時刻×曜日テーブル (W16 Phase B-1 / B-2).
 *
 * Wave 16: スタッフ別テーブル N 個構造 (旧 ScheduleUnifiedView 廃止).
 *
 * 1 テーブル = 1 スタッフ.
 *   - 行 = 9:00〜19:00 / 15min 刻み (= 41 行)
 *   - 列 = 月〜土 (= 6 列、日曜は表示しない)
 *   - 左端 = 時刻ラベル
 *   - セル = 当該スタッフが当該曜日の当該時刻に担当する visit (患者氏名)
 *
 * dnd-kit:
 *   - 各セルは droppable. id = `cell:${staffId}:${weekday}:${HH:MM}`.
 *   - 親の StaffWeekTablePanel が DragEnd を受け取り place-and-fix を呼ぶ.
 */
import { useMemo } from 'react';
import { useDroppable } from '@dnd-kit/core';
import { format } from 'date-fns';
import { ja } from 'date-fns/locale';

import { addDays } from '@/components/schedule/WeekSelector';
import { cn } from '@/lib/utils';

import type { StaffGridVisit } from './StaffWeekTablePanel';

// ─────────────────────────────────────────────────────────────────────────
// Constants — 時刻軸 (B-2)
// ─────────────────────────────────────────────────────────────────────────

/**
 * 表示時刻範囲: 9:00〜19:00 (両端含む).
 * 15 分刻み → 41 スロット (09:00, 09:15, …, 18:45, 19:00).
 */
export const TIME_SLOT_START_HOUR = 9;
export const TIME_SLOT_END_HOUR = 19;
export const TIME_SLOT_MINUTES = 15;

/** 表示曜日 (月〜土の 6 列). 日曜は表示しない. */
export const DISPLAY_WEEKDAYS = [0, 1, 2, 3, 4, 5] as const;
const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;

/** 41 行の時刻ラベル ("HH:MM"). */
export function buildTimeSlots(): string[] {
  const out: string[] = [];
  const total = (TIME_SLOT_END_HOUR - TIME_SLOT_START_HOUR) * 60 + TIME_SLOT_MINUTES;
  for (let m = 0; m < total; m += TIME_SLOT_MINUTES) {
    const hh = TIME_SLOT_START_HOUR + Math.floor(m / 60);
    const mm = m % 60;
    out.push(`${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`);
  }
  return out;
}

const TIME_SLOTS = buildTimeSlots();

// ─────────────────────────────────────────────────────────────────────────
// dnd-kit ID helpers (StaffWeekTablePanel と整合)
// ─────────────────────────────────────────────────────────────────────────

export function staffCellDroppableId(staffId: string, weekday: number, time: string): string {
  // staffId は UUID, weekday=0..6, time=HH:MM
  return `staff-cell:${staffId}:${weekday}:${time}`;
}

/** "staff-cell:UUID:weekday:HH:MM" を分解. UUID 中のハイフンに注意. */
export function parseStaffCellId(
  id: string,
): { staffId: string; weekday: number; time: string } | null {
  if (!id.startsWith('staff-cell:')) return null;
  const rest = id.slice('staff-cell:'.length);
  const parts = rest.split(':');
  // UUID は ':' を含まないので [staffId, weekday, hh, mm] の 4 要素になる.
  if (parts.length < 4) return null;
  const staffId = parts[0]!;
  const weekday = Number.parseInt(parts[1]!, 10);
  const time = `${parts[2]}:${parts[3]}`;
  if (Number.isNaN(weekday) || weekday < 0 || weekday > 6) return null;
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) return null;
  return { staffId, weekday, time };
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export interface StaffTimeGridProps {
  staffId: string;
  weekStart: Date;
  /** 当該スタッフ × 当週 の visits (親側で staff フィルタ済み). */
  visits: StaffGridVisit[];
  canEdit: boolean;
}

/**
 * 1 スタッフ分のテーブル本体. 親はヘッダー (スタッフ名 + コース割当バッジ) を
 * 別途レンダーしてから本コンポーネントを描画する。
 */
export function StaffTimeGrid({ staffId, weekStart, visits, canEdit }: StaffTimeGridProps) {
  // visits を (weekday, slot) → patient[] にバケット化 (slot = HH:MM 開始時刻).
  // start_time は "HH:MM" or "HH:MM:SS" のため 5 文字に丸めて 15 分の境界に
  // 切り下げる (例 09:07 → 09:00).
  const occupants = useMemo(() => {
    const m = new Map<string, StaffGridVisit[]>();
    for (const v of visits) {
      const weekday = visitWeekday(v.visit_date);
      if (weekday === null) continue;
      if (!DISPLAY_WEEKDAYS.includes(weekday as (typeof DISPLAY_WEEKDAYS)[number])) {
        continue;
      }
      const slot = floorToSlot(v.start_time ?? '');
      if (!slot) continue;
      const key = `${weekday}:${slot}`;
      const arr = m.get(key) ?? [];
      arr.push(v);
      m.set(key, arr);
    }
    return m;
  }, [visits]);

  return (
    <div
      className="overflow-x-auto"
      data-testid={`staff-time-grid-${staffId}`}
      data-staff-id={staffId}
    >
      <div
        className="grid border border-border-default text-[11px]"
        style={{
          gridTemplateColumns: `64px repeat(${DISPLAY_WEEKDAYS.length}, minmax(110px, 1fr))`,
        }}
      >
        {/* ヘッダー行: 時刻 + 月-土 */}
        <div className="border-b border-r border-border-default bg-bg-muted px-1.5 py-1 text-[10px] font-semibold text-text-muted">
          時刻
        </div>
        {DISPLAY_WEEKDAYS.map((wd) => (
          <div
            key={wd}
            className="border-b border-r border-border-default bg-bg-muted px-1.5 py-1 text-center text-[11px] font-semibold text-text-secondary"
          >
            {WEEKDAY_LABELS[wd]}{' '}
            <span className="tnum text-text-muted">
              {format(addDays(weekStart, wd), 'M/d', { locale: ja })}
            </span>
          </div>
        ))}

        {/* 41 行 × (1 時刻列 + 6 曜日列) */}
        {TIME_SLOTS.map((time) => (
          <TimeRow
            key={time}
            staffId={staffId}
            time={time}
            occupants={occupants}
            canEdit={canEdit}
          />
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────────

interface TimeRowProps {
  staffId: string;
  time: string;
  occupants: Map<string, StaffGridVisit[]>;
  canEdit: boolean;
}

function TimeRow({ staffId, time, occupants, canEdit }: TimeRowProps) {
  // 30 分境界 (HH:00 / HH:30) は時刻ラベルを表示。それ以外は薄いグリッド線のみ。
  const showLabel = time.endsWith(':00') || time.endsWith(':30');
  return (
    <>
      <div
        className={cn(
          'border-r border-t border-border-default/60 px-1.5 py-0.5 text-right tnum',
          showLabel ? 'text-[10px] text-text-secondary' : 'text-[9px] text-text-muted',
        )}
      >
        {showLabel ? time : ''}
      </div>
      {DISPLAY_WEEKDAYS.map((wd) => {
        const key = `${wd}:${time}`;
        const items = occupants.get(key) ?? [];
        return (
          <StaffCell
            key={wd}
            staffId={staffId}
            weekday={wd}
            time={time}
            occupants={items}
            canEdit={canEdit}
          />
        );
      })}
    </>
  );
}

interface StaffCellProps {
  staffId: string;
  weekday: number;
  time: string;
  occupants: StaffGridVisit[];
  canEdit: boolean;
}

function StaffCell({ staffId, weekday, time, occupants, canEdit }: StaffCellProps) {
  const droppableId = staffCellDroppableId(staffId, weekday, time);
  const { isOver, setNodeRef } = useDroppable({
    id: droppableId,
    disabled: !canEdit,
    data: { kind: 'staff-cell', staffId, weekday, time },
  });

  return (
    <div
      ref={setNodeRef}
      data-droppable={droppableId}
      data-weekday={weekday}
      data-time={time}
      data-staff-id={staffId}
      className={cn(
        'min-h-[16px] border-r border-t border-border-default/40 px-0.5 py-px text-[10px] leading-tight transition-colors',
        isOver && canEdit ? 'bg-brand-primary/10 ring-1 ring-brand-primary ring-inset' : '',
      )}
    >
      {occupants.map((v) => (
        <div
          key={v.id}
          className="truncate rounded border border-brand-primary/40 bg-brand-primary/5 px-0.5 leading-tight text-text-primary"
          title={`${(v.start_time ?? '').slice(0, 5)} ${v.patient_name ?? v.patient_id}`}
        >
          {v.patient_name ?? v.patient_id}
        </div>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

/** "yyyy-MM-dd" → 0=月..6=日. invalid なら null. */
function visitWeekday(visitDate: string | null | undefined): number | null {
  if (!visitDate) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(visitDate);
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]) - 1;
  const d = Number(m[3]);
  const date = new Date(y, mo, d);
  if (Number.isNaN(date.getTime())) return null;
  // JS getDay(): Sun=0..Sat=6. → Mon=0..Sun=6 へ変換.
  return (date.getDay() + 6) % 7;
}

/** "HH:MM[:SS]" → 15 分境界に切り下げた "HH:MM". 範囲外 (9:00 未満 / 19:00 超) は null. */
function floorToSlot(rawTime: string): string | null {
  const m = /^([01]\d|2[0-3]):([0-5]\d)/.exec(rawTime);
  if (!m) return null;
  const hh = Number(m[1]);
  const mm = Number(m[2]);
  if (hh < TIME_SLOT_START_HOUR) return null;
  if (hh > TIME_SLOT_END_HOUR) return null;
  // 19:00 ちょうどは含む。19:01 以降は外す。
  if (hh === TIME_SLOT_END_HOUR && mm > 0) return null;
  const floored = mm - (mm % TIME_SLOT_MINUTES);
  return `${String(hh).padStart(2, '0')}:${String(floored).padStart(2, '0')}`;
}
