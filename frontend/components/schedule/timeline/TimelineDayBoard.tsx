'use client';

/**
 * TimelineDayBoard — 縦タイムライン日ビュー (T-1・読み取り専用)。
 *
 * docs/plans/schedule-timeline-redesign-design.md / docs/mockups/timeline-mock.html。
 * 列=コース (ヘッダ=担当スタッフ主・コース記号従)、縦=時間軸 9:00〜18:00・30分格子、
 * カード高さ=所要時間に比例、地色=患者性別、勤務外=斜線、空き=「＋n分空き」、
 * 会議・イベント=全幅の藤色帯 (カイポケ反映外)、現在時刻ライン。
 *
 * T-1 は読み取り専用: カード / 空き枠クリックで既存の患者詳細 (onPatientClick) を開くのみ。
 * ドラッグ移動・クリック登録は T-2 で解禁する。データ変換・API・ソルバは一切持たない
 * (CourseDayTablePanel が組んだ CourseGridVisit をそのまま受け取る = 表示専用)。
 */

import { useMemo } from 'react';

import type { CourseGridVisit } from '@/components/schedule/v2/CourseDayTable';
import type { CourseV2Read } from '@/lib/queries/courses';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { StaffRead } from '@/lib/schemas/staff';
import type { EventRead } from '@/lib/schemas/staff-events';
import type { FreeGap } from '@/lib/scheduling/freeGaps';
import { parseHM } from '@/lib/scheduling/freeGaps';
import {
  assignLanes,
  durationToHeight,
  genderKey,
  genderPalette,
  minutesToY,
  TL_DAY_END_MIN,
  TL_DAY_START_MIN,
  TL_MIN_CARD_PX,
  TL_ROW_PX,
  TL_SHOW_PILLS_PX,
  TL_SHOW_SVC_PX,
  timelineHeightPx,
} from '@/lib/scheduling/timeline';
import { cn } from '@/lib/utils';

const COL_MIN_W = 172;
const TIME_RAIL_W = 54;

export interface TimelineCourseColumn {
  key: string;
  template: CourseTemplateRead;
  course: CourseV2Read | null;
  officeName: string;
  visits: CourseGridVisit[];
  /** 担当スタッフ (course.assigned_staff_id を解決したもの)。ヘッダのアバター色に sex を使う。 */
  assignedStaff: StaffRead | null;
  freeGaps: FreeGap[];
  capacity: { filled: number; max: number };
  /** 当該コース担当スタッフの当日イベント (勤務外/会議帯として描く元)。 */
  staffEvents: EventRead[];
}

export interface TimelineDayBoardProps {
  columns: TimelineCourseColumn[];
  weekdayLabel: string;
  /** カード / 空き枠クリック → 患者詳細 (既存ダイアログ)。T-1 は詳細を開くのみ。 */
  onPatientClick?: (patientId: string) => void;
  /** 現在時刻ライン (0時起点の分)。当日でないときは null で非表示。 */
  nowMinutes?: number | null;
}

/** 会議・イベント (訪問ではないスタッフ予定) を全幅帯として時間帯で描く。 */
interface EventBand {
  key: string;
  startMin: number;
  endMin: number;
  label: string;
}

function eventBandsOf(columns: TimelineCourseColumn[]): EventBand[] {
  // 全コース共通で出す「全体」会議はモックの全幅帯に相当。ここでは各スタッフの
  // イベントを時間帯で重複排除し、全幅の帯として描く (T-1 は表示のみ)。
  const seen = new Map<string, EventBand>();
  for (const col of columns) {
    for (const ev of col.staffEvents) {
      const s = parseHM(ev.start_time);
      const e = parseHM(ev.end_time);
      if (s === null || e === null || e <= s) continue;
      const key = `${ev.start_time}-${ev.end_time}-${ev.title ?? ev.type}`;
      if (!seen.has(key)) {
        const label = ev.title ? `${ev.type}: ${ev.title}` : ev.type;
        seen.set(key, { key, startMin: s, endMin: e, label });
      }
    }
  }
  return [...seen.values()].sort((a, b) => a.startMin - b.startMin);
}

function PersonMark() {
  return (
    <svg viewBox="0 0 10 10" width="9" height="9" aria-hidden="true" className="inline-block">
      <circle cx="5" cy="3" r="2.1" fill="currentColor" />
      <path d="M1.2 9.4c.5-2 2-3 3.8-3s3.3 1 3.8 3z" fill="currentColor" />
    </svg>
  );
}

/** レーン (時間帯が重なる訪問の左右分割)。lane=0/laneCount=1 のとき全幅。 */
interface CardLane {
  lane: number;
  laneCount: number;
}

function VisitCard({
  visit,
  onClick,
  laneInfo,
}: {
  visit: CourseGridVisit;
  onClick?: () => void;
  laneInfo?: CardLane;
}) {
  const startMin = parseHM(visit.start_time);
  const endMin = parseHM(visit.end_time);
  if (startMin === null || endMin === null || endMin <= startMin) return null;
  const isCancelled = visit.status === 'cancelled';
  const pal = genderPalette(visit.patient_sex);
  // 範囲外 (9:00前/18:00後) はカードを軸内にクランプして隠れ・はみ出しを防ぐ (LOW-4)。
  const clampedStart = Math.max(startMin, TL_DAY_START_MIN);
  const clampedEnd = Math.min(endMin, TL_DAY_END_MIN);
  const top = minutesToY(clampedStart) + 1;
  const rawH = durationToHeight(Math.max(clampedEnd - clampedStart, 0)) - 3;
  const height = Math.max(rawH, TL_MIN_CARD_PX);
  const durMin = endMin - startMin;
  const isMulti = visit.patient_requires_multiple_staff || visit.group_slot_label != null;

  // 重なり時のみ左右に分割 (MED-1)。laneCount=1 は全幅 (従来どおり)。
  const lanes = laneInfo?.laneCount ?? 1;
  const lane = laneInfo?.lane ?? 0;
  const laneStyle =
    lanes > 1
      ? {
          left: `calc(3px + ${(lane / lanes) * 100}% - ${(lane / lanes) * 6}px)`,
          width: `calc(${100 / lanes}% - ${6 / lanes}px)`,
          right: 'auto' as const,
        }
      : { left: '3px', right: '3px' };

  const pills: string[] = [];
  if (visit.patient_sex_restriction_label) pills.push(visit.patient_sex_restriction_label);
  if (isMulti) pills.push('2名');
  if (visit.same_address_group_id) pills.push('📍同住所');

  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={`tl-visit-${visit.id}`}
      className="absolute flex flex-col gap-px overflow-hidden rounded-lg border border-l-[3px] px-2 py-[3px] text-left shadow-[var(--shadow-xs)] transition-shadow hover:z-[4] hover:shadow-[var(--shadow-md)] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
      style={{
        top,
        height,
        ...laneStyle,
        background: isCancelled ? 'var(--bg-muted)' : pal.bg,
        borderColor: isCancelled ? 'var(--border-default)' : pal.ln,
        borderLeftColor: isCancelled ? 'var(--text-muted)' : pal.bar,
        color: isCancelled ? 'var(--text-muted)' : pal.ink,
        opacity: isCancelled ? 0.7 : 1,
      }}
    >
      <span className="flex min-w-0 items-center gap-1">
        {visit.is_pinned && (
          <span className="shrink-0 text-[9px]" aria-label="ピン留め">
            🔒
          </span>
        )}
        {isMulti && (
          <span className="inline-flex shrink-0 gap-px text-brand-primary" aria-label="2名体制">
            <PersonMark />
            <PersonMark />
          </span>
        )}
        <span className={cn('truncate text-[11.5px] font-bold', isCancelled && 'line-through')}>
          {visit.patient_name ?? '—'}
        </span>
        <span className="tnum ml-auto shrink-0 text-[9px] opacity-75">
          {(visit.start_time ?? '').slice(0, 5)}・{durMin}分
        </span>
      </span>
      {height >= TL_SHOW_SVC_PX && (
        <span className="truncate text-[9.5px] opacity-80">
          {isCancelled ? 'キャンセル' : (visit.patient_time_type ?? '')}
        </span>
      )}
      {pills.length > 0 && height >= TL_SHOW_PILLS_PX && (
        <span className="mt-auto flex flex-wrap gap-[3px] pb-px">
          {pills.map((p) => (
            <span
              key={p}
              className="rounded-full px-1.5 py-px text-[8.5px] font-bold text-white"
              style={{ background: pal.bar }}
            >
              {p}
            </span>
          ))}
        </span>
      )}
    </button>
  );
}

function TimelineColumn({
  col,
  onPatientClick,
}: {
  col: TimelineCourseColumn;
  onPatientClick?: (patientId: string) => void;
}) {
  const height = timelineHeightPx();
  // 勤務外バンド: スタッフイベント以外に、コース未生成/担当なしを表す薄いハッチは出さない
  // (T-1 はデータのある勤務外のみ)。将来 shift 情報で拡張。
  const rows: number[] = [];
  for (let m = TL_DAY_START_MIN; m < TL_DAY_END_MIN; m += 30) rows.push(m);

  // 時間帯が重なる訪問 (2名同時刻など) を左右レーンに分けて相互に隠さない (MED-1)。
  const lanes = assignLanes(
    col.visits
      .map((v) => {
        const s = parseHM(v.start_time);
        const e = parseHM(v.end_time);
        return s !== null && e !== null && e > s ? { id: v.id, startMin: s, endMin: e } : null;
      })
      .filter((b): b is { id: string; startMin: number; endMin: number } => b !== null),
  );

  return (
    <div
      className="relative border-l border-[var(--border-subtle)]"
      style={{ flex: 1, minWidth: COL_MIN_W }}
      data-testid={`tl-col-${col.key}`}
    >
      {rows.map((m) => (
        <div
          key={m}
          className={cn(
            'absolute left-0 right-0',
            m % 60 === 0
              ? 'border-t border-[var(--border-default)]'
              : 'border-t border-dashed border-[var(--border-subtle)]',
          )}
          style={{ top: minutesToY(m), height: TL_ROW_PX }}
        />
      ))}

      {/* 空き時間帯 (≥60分・remaining>0 のときだけ Panel が gap を渡す) */}
      {col.freeGaps.map((g) => {
        const top = minutesToY(g.startMin) + 2;
        const h = durationToHeight(g.endMin - g.startMin) - 5;
        if (h < 14) return null;
        return (
          <div
            key={`gap-${g.startMin}`}
            data-testid={`tl-gap-${col.key}-${g.startMin}`}
            className="absolute left-1 right-1 flex items-center justify-center gap-1.5 rounded-lg border border-dashed border-transparent text-[11px] font-bold text-brand-primary opacity-0 transition-opacity hover:border-brand-primary hover:bg-[color-mix(in_srgb,var(--brand-primary)_5%,transparent)] hover:opacity-100"
            style={{ top, height: h }}
          >
            <span className="grid h-[18px] w-[18px] place-items-center rounded-full bg-brand-primary text-[13px] leading-none text-white">
              ＋
            </span>
            <span className="tnum text-[9.5px] font-semibold text-text-muted">
              {g.endMin - g.startMin}分空き
            </span>
          </div>
        );
      })}

      {/* 訪問カード */}
      {col.visits.map((v) => (
        <VisitCard
          key={v.id}
          visit={v}
          laneInfo={lanes.get(v.id)}
          onClick={onPatientClick ? () => onPatientClick(v.patient_id) : undefined}
        />
      ))}

      <div style={{ height }} />
    </div>
  );
}

export function TimelineDayBoard({
  columns,
  weekdayLabel,
  onPatientClick,
  nowMinutes,
}: TimelineDayBoardProps) {
  const height = timelineHeightPx();
  const events = useMemo(() => eventBandsOf(columns), [columns]);
  const hours: number[] = [];
  for (let m = TL_DAY_START_MIN; m <= TL_DAY_END_MIN; m += 60) hours.push(m);

  if (columns.length === 0) {
    return (
      <div className="rounded-lg border border-border-default bg-bg-surface p-4 text-sm text-text-muted">
        {weekdayLabel}曜日の表示対象コースがありません。
      </div>
    );
  }

  const showNow =
    nowMinutes != null && nowMinutes >= TL_DAY_START_MIN && nowMinutes <= TL_DAY_END_MIN;

  return (
    <div
      className="overflow-auto rounded-lg border border-border-default bg-bg-base"
      data-testid="timeline-day-board"
    >
      {/* 列ヘッダ (担当スタッフ主・コース記号従) */}
      <div className="sticky top-0 z-[6] flex min-w-fit border-b border-border-default bg-bg-surface">
        <div style={{ width: TIME_RAIL_W, flex: `0 0 ${TIME_RAIL_W}px` }} />
        {columns.map((col) => {
          const gk = genderKey(col.assignedStaff?.sex);
          const pal = genderPalette(col.assignedStaff?.sex);
          const staffName = col.assignedStaff?.name ?? '（未割当）';
          const full = col.capacity.filled >= col.capacity.max;
          return (
            <div
              key={col.key}
              className="flex items-center gap-2 border-l border-[var(--border-subtle)] px-2.5 py-2"
              style={{ flex: 1, minWidth: COL_MIN_W }}
            >
              <span
                className="grid h-7 w-7 shrink-0 place-items-center rounded-full border-[1.5px] text-[11px] font-bold"
                style={{
                  background: pal.bg,
                  borderColor: pal.bar,
                  color: pal.ink,
                }}
                data-gender={gk}
              >
                {staffName[0]}
              </span>
              <div className="min-w-0">
                <div className="truncate text-[12.5px] font-bold text-text-primary">
                  {staffName}
                </div>
                <div className="flex items-center gap-1.5 text-[9.5px] text-text-muted">
                  <span
                    className="rounded px-1.5 py-px text-[9px] font-extrabold text-white"
                    style={{ background: 'var(--brand-primary)' }}
                  >
                    {col.officeName}
                    {col.template.label}
                  </span>
                  <span className={cn('tnum font-bold', full && 'text-status-mismatch')}>
                    {col.capacity.filled}/{col.capacity.max}件
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* 本体 (時間軸レール + コース列 + イベント帯 + 現在線) */}
      <div className="relative flex min-w-fit">
        <div style={{ width: TIME_RAIL_W, flex: `0 0 ${TIME_RAIL_W}px` }} className="relative">
          {hours.map((m) => (
            <span
              key={m}
              className="tnum absolute right-2 -translate-y-[7px] text-[10px] text-text-muted"
              style={{ top: minutesToY(m) }}
            >
              {String(Math.floor(m / 60)).padStart(2, '0')}:{String(m % 60).padStart(2, '0')}
            </span>
          ))}
          <div style={{ height }} />
        </div>

        {columns.map((col) => (
          <TimelineColumn key={col.key} col={col} onPatientClick={onPatientClick} />
        ))}

        {/* 会議・イベント帯 (全幅・藤色・カイポケ反映外) */}
        {events.map((ev) => {
          const top = minutesToY(ev.startMin) + 1;
          const h = durationToHeight(ev.endMin - ev.startMin) - 3;
          return (
            <div
              key={ev.key}
              data-testid={`tl-event-${ev.key}`}
              // T-1 は表示専用のため pointer-events-none (下の訪問カードのクリック/hover を透過)。
              className="pointer-events-none absolute z-[3] flex items-center gap-2 rounded-lg border border-l-[3px] px-3 py-[5px] shadow-[var(--shadow-sm)]"
              style={{
                top,
                left: TIME_RAIL_W + 4,
                right: 10,
                height: Math.max(h, 22),
                background: 'var(--sched-event-bg)',
                borderColor: 'var(--sched-event-ln)',
                borderLeftColor: 'var(--sched-event-bar)',
              }}
            >
              <span className="text-[13px]" style={{ color: 'var(--sched-event-bar)' }}>
                👥
              </span>
              <span className="text-[12px] font-bold" style={{ color: 'var(--sched-event-ink)' }}>
                {ev.label}
              </span>
              <span
                className="tnum text-[10.5px] opacity-75"
                style={{ color: 'var(--sched-event-ink)' }}
              >
                {String(Math.floor(ev.startMin / 60)).padStart(2, '0')}:
                {String(ev.startMin % 60).padStart(2, '0')}〜 {ev.endMin - ev.startMin}分
              </span>
              <span
                className="ml-auto whitespace-nowrap rounded-full border bg-bg-base px-1.5 py-px text-[8.5px] font-bold"
                style={{ color: 'var(--sched-event-ink)', borderColor: 'var(--sched-event-ln)' }}
              >
                カイポケ反映外
              </span>
            </div>
          );
        })}

        {/* 現在時刻ライン */}
        {showNow && nowMinutes != null && (
          <div
            data-testid="timeline-now-line"
            className="pointer-events-none absolute left-0 right-0 z-[5] border-t-2"
            style={{ top: minutesToY(nowMinutes), borderColor: 'var(--now, #d2683c)' }}
          >
            <span
              className="tnum absolute left-0.5 -top-[9px] rounded px-1.5 py-px text-[9px] font-bold text-white"
              style={{ background: 'var(--now, #d2683c)' }}
            >
              {String(Math.floor(nowMinutes / 60)).padStart(2, '0')}:
              {String(nowMinutes % 60).padStart(2, '0')}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
