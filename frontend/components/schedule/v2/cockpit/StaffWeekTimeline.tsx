'use client';

/**
 * StaffWeekTimeline — 職員の「週・タイムライン」(PO 決定 2026-10-21)。
 *
 * 行 = 職員 (スタッフコード順・末尾に「（担当なし）」) / 列 = 月〜土。週のリスト
 * (StaffWeekBoard) と同じ並び。各マスに 8〜19 時の小さな横の時間軸を持ち、訪問を
 * 所要時間の長さの帯で描く。
 *
 *   - 帯の title = 「時刻・患者名・コース」。クリック = 訪問のメニュー (`onVisitClick`)
 *   - 休み・半休・時間変更は働かない時間だけを斜線 (`offDutyRanges` = 1 日タイムラインと同じ)
 *   - イベント = 緑の細い帯 / 同行・2 人目 = 薄い帯 (動かせない)
 *   - マスの角に「N件」(色は `staffDayCount.ts` の決まり)・今日のマスは薄く色を付ける
 *
 * ドラッグ (HTML5 DnD・`SWT_VISIT_DND_MIME`): 訪問の帯を別のマス (職員×曜日) の横の位置へ
 * 落とすと、落とした位置から時刻 (15 分刻み) を決めて `onVisitMove` を呼ぶ。
 * 変わった軸だけを載せる (時刻 = toStart / 曜日 = toWeekday / 担当 = toStaffId)。
 * 今日より前の日 (今日を含む・1 日タイムラインと同じ) はつかめず、落とせもしない。
 * この部品は API を呼ばない (結線は CourseDayTablePanel の handleTimelineVisitMove)。
 */
import * as React from 'react';
import { addDays, format } from 'date-fns';

import type { WeekOverrideRead } from '@/lib/queries/staff-overrides';
import { classifyVisitDisplay } from '@/lib/schedule/visitVisibility';
import {
  resolveVisitRowKey,
  staffDayCountKey,
  weekVisitTotal,
  type StaffDayCount,
} from '@/lib/schedule/staffDayCount';
import { parseHM } from '@/lib/scheduling/freeGaps';
import {
  assignLanes,
  genderPalette,
  TL_COCKPIT_END_MIN,
  TL_COCKPIT_START_MIN,
} from '@/lib/scheduling/timeline';
import { SyncedHScroll } from '@/components/ui/synced-h-scroll';
import { cn } from '@/lib/utils';

import { buildStaffWeekTimelineCellId, SWT_VISIT_DND_MIME, UNASSIGNED_ROW_KEY } from '../courseDnd';
import { StaffDayCountBadge } from '../StaffDayCountBadge';
import {
  offDutyRanges,
  type StaffTimelineRow,
  type TimelineEvent,
  type TimelineVisit,
  type VisitMovePayload,
} from './StaffTimelineView';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;
const WEEKDAYS = [0, 1, 2, 3, 4, 5] as const;
const TOTAL_MIN = TL_COCKPIT_END_MIN - TL_COCKPIT_START_MIN;
/** 見出しの行の目盛り (時)。 */
const TICK_HOURS = [9, 12, 15, 18] as const;
/** 氏名の列の幅 (px)。 */
const NAME_COL_PX = 200;
const SNAP_MIN = 15;
const DEFAULT_DURATION_MIN = 30;
/** 帯の高さ / レーン送り / イベントの細い帯 (px)。 */
const BAR_H = 12;
const LANE_STEP = 14;
const EVENT_H = 4;
const TRACK_PAD = 4;

const HATCH =
  'repeating-linear-gradient(135deg, transparent 0 6px, var(--sched-offduty-hatch) 6px 7px)';

function hhmm(min: number): string {
  return `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`;
}
const pct = (min: number) => ((min - TL_COCKPIT_START_MIN) / TOTAL_MIN) * 100;
const wpct = (dur: number) => (dur / TOTAL_MIN) * 100;

function spanOf(start: string | null | undefined, end: string | null | undefined) {
  const s = parseHM(start);
  if (s === null) return null;
  const e = parseHM(end);
  return { startMin: s, endMin: e !== null && e > s ? e : s + DEFAULT_DURATION_MIN };
}

/**
 * 落とした位置 (マスの左端からの px) → 開始の分。つかんだ位置 (帯の左端からの分) を引き、
 * 15 分に丸め、時間軸の外へは出さない。純関数。
 */
export function dropXToStartMinutes(
  offsetPx: number,
  trackWidthPx: number,
  grabOffsetMin: number,
  durationMin: number,
): number {
  const pxPerMin = trackWidthPx > 0 ? trackWidthPx / TOTAL_MIN : 0;
  const raw =
    pxPerMin > 0
      ? TL_COCKPIT_START_MIN + offsetPx / pxPerMin - grabOffsetMin
      : TL_COCKPIT_START_MIN;
  const snapped = Math.round(raw / SNAP_MIN) * SNAP_MIN;
  return Math.max(TL_COCKPIT_START_MIN, Math.min(TL_COCKPIT_END_MIN - durationMin, snapped));
}

interface DragData {
  visitId: string;
  /** 帯の左端からつかんだ位置 (分)。 */
  grabMin: number;
}

export interface StaffWeekTimelineProps {
  /** 対象週の月曜。 */
  weekStart: Date;
  /** 在籍の職員 (スタッフコード順)。「（担当なし）」は部品が足す。 */
  staffRows: StaffTimelineRow[];
  visits: TimelineVisit[];
  /** staffId → その週のイベント。 */
  events?: Map<string, TimelineEvent[]>;
  /** `${staffId}:${weekday}` → 休み/時間変更。 */
  offByStaffWeekday?: Map<string, WeekOverrideRead>;
  assignedStaffByTemplateWeekday?: Map<string, string>;
  /** 件数 (`countStaffDays`)。 */
  dayCounts?: Map<string, StaffDayCount>;
  /** 件数の色の上限 (設定の「1コースの最大人数」)。 */
  countLimit?: number | null;
  /** `${staffId}:${weekday}` → 同行する訪問 (薄い帯・動かせない)。 */
  companionVisitsByCell?: Map<string, TimelineVisit[]>;
  /** 今日 (JST・`YYYY-MM-DD`)。この日以前のマスはつかめず、落とせもしない。 */
  todayIso: string;
  canEdit: boolean;
  showInactive?: boolean;
  onVisitMove: (payload: VisitMovePayload) => void;
  onVisitClick?: (visit: TimelineVisit) => void;
  /** 提案の候補を hover 中の職員 (行を薄くブランド色に)。 */
  highlightStaffId?: string | null;
}

export function StaffWeekTimeline({
  weekStart,
  staffRows,
  visits,
  events,
  offByStaffWeekday,
  assignedStaffByTemplateWeekday,
  dayCounts,
  countLimit = null,
  companionVisitsByCell,
  todayIso,
  canEdit,
  showInactive = false,
  onVisitMove,
  onVisitClick,
  highlightStaffId,
}: StaffWeekTimelineProps) {
  const [dragging, setDragging] = React.useState<string | null>(null);
  const [overCell, setOverCell] = React.useState<string | null>(null);
  const dragRef = React.useRef<DragData | null>(null);

  const dayIso = React.useCallback(
    (wd: number) => format(addDays(weekStart, wd), 'yyyy-MM-dd'),
    [weekStart],
  );
  const isPastDay = React.useCallback((wd: number) => dayIso(wd) <= todayIso, [dayIso, todayIso]);

  // 盤面に出す訪問 = 取消と連動取消を除く (件数と同じ見え方)。
  const shown = React.useMemo(
    () =>
      visits.filter(
        (v) => v.status !== 'cancelled' && classifyVisitDisplay(v, { showInactive }) !== 'hidden',
      ),
    [visits, showInactive],
  );

  const rowKeyOf = React.useCallback(
    (v: TimelineVisit) => resolveVisitRowKey(v, assignedStaffByTemplateWeekday),
    [assignedStaffByTemplateWeekday],
  );

  /** 行: 在籍の職員 + 盤面に出ている知らない担当 + 「（担当なし）」(常設)。 */
  const rows = React.useMemo(() => {
    const known = new Set(staffRows.map((r) => r.staffId));
    const list: StaffTimelineRow[] = [...staffRows];
    for (const v of shown) {
      const k = rowKeyOf(v);
      if (k !== UNASSIGNED_ROW_KEY && !known.has(k)) {
        known.add(k);
        list.push({ staffId: k, name: '（不明なスタッフ）' });
      }
    }
    list.push({ staffId: UNASSIGNED_ROW_KEY, name: '（担当なし）' });
    return list;
  }, [staffRows, shown, rowKeyOf]);
  const rowById = React.useMemo(() => new Map(rows.map((r) => [r.staffId, r])), [rows]);

  const visitsByCell = React.useMemo(() => {
    const m = new Map<string, TimelineVisit[]>();
    for (const v of shown) {
      const key = staffDayCountKey(rowKeyOf(v), v.weekday);
      const arr = m.get(key) ?? [];
      arr.push(v);
      m.set(key, arr);
    }
    return m;
  }, [shown, rowKeyOf]);
  const visitById = React.useMemo(() => new Map(shown.map((v) => [v.id, v])), [shown]);

  /** この行・この日へ落とせるか (編集権限・過去日・新人は担当になれない)。 */
  const canDropOn = (rowKey: string, wd: number) =>
    canEdit && !isPastDay(wd) && rowById.get(rowKey)?.isTrainee !== true;

  const handleDrop = (e: React.DragEvent<HTMLDivElement>, rowKey: string, wd: number) => {
    setOverCell(null);
    setDragging(null);
    let data: DragData | null = dragRef.current;
    try {
      const raw = e.dataTransfer?.getData(SWT_VISIT_DND_MIME);
      if (raw) data = JSON.parse(raw) as DragData;
    } catch {
      // getData が使えない環境は dragRef を使う。
    }
    dragRef.current = null;
    if (!data || !canDropOn(rowKey, wd)) return;
    const visit = visitById.get(data.visitId);
    if (!visit) return;
    e.preventDefault();
    const span = spanOf(visit.start_time, visit.end_time);
    if (!span) return;
    const rect = e.currentTarget.getBoundingClientRect();
    // 位置が読めない (合成イベントなど) ときは時刻を決められないので何もしない。
    if (!Number.isFinite(e.clientX)) return;
    const dur = span.endMin - span.startMin;
    const startMin = dropXToStartMinutes(e.clientX - rect.left, rect.width, data.grabMin, dur);
    const fromStart = hhmm(span.startMin);
    const toStartHM = hhmm(startMin);
    const fromKey = rowKeyOf(visit);
    const payload: VisitMovePayload = {
      visit,
      fromStart,
      toStart: toStartHM !== fromStart ? toStartHM : null,
    };
    if (wd !== visit.weekday) payload.toWeekday = wd;
    // 曜日をまたぐとコースが付け替わり担当もそのコースの担当になるので、同じ行のままでも
    // 落とした行の担当を必ず載せる (担当なしの行なら外す)。
    if (rowKey !== fromKey || wd !== visit.weekday) {
      payload.toStaffId = rowKey === UNASSIGNED_ROW_KEY ? null : rowKey;
    }
    if (payload.toStart === null && payload.toWeekday === undefined && !('toStaffId' in payload)) {
      return;
    }
    onVisitMove(payload);
  };

  /** `${rowKey}:${weekday}` → その日のイベント (今週だけ外したものを除く)。ドラッグ中も同じ参照。 */
  const eventsByCell = React.useMemo(() => {
    const m = new Map<string, TimelineEvent[]>();
    if (!events) return m;
    for (const [staffId, list] of events) {
      for (const ev of list) {
        if (ev.cancelled_at) continue;
        const wd = WEEKDAYS.find((d) => dayIso(d) === ev.date);
        if (wd === undefined) continue;
        const key = staffDayCountKey(staffId, wd);
        const arr = m.get(key) ?? [];
        arr.push(ev);
        m.set(key, arr);
      }
    }
    return m;
  }, [events, dayIso]);

  // マス (SwtCell・React.memo) へ渡す手は参照を変えない = ドラッグ中に表全体を描き直さない。
  const handleDropRef = React.useRef(handleDrop);
  handleDropRef.current = handleDrop;
  const onDropCell = React.useCallback(
    (e: React.DragEvent<HTMLDivElement>, rowKey: string, wd: number) =>
      handleDropRef.current(e, rowKey, wd),
    [],
  );
  const onBarDragStart = React.useCallback((data: DragData) => {
    dragRef.current = data;
    setDragging(data.visitId);
  }, []);
  const onBarDragEnd = React.useCallback(() => {
    setDragging(null);
    setOverCell(null);
  }, []);
  const onVisitClickRef = React.useRef(onVisitClick);
  onVisitClickRef.current = onVisitClick;
  const onVisitClickStable = React.useCallback(
    (v: TimelineVisit) => onVisitClickRef.current?.(v),
    [],
  );

  return (
    <SyncedHScroll data-testid="staff-week-timeline">
      <div
        className="grid text-xs"
        style={{
          gridTemplateColumns: `${NAME_COL_PX}px repeat(6, minmax(120px, 1fr))`,
          minWidth: NAME_COL_PX + 6 * 120,
        }}
        data-testid="swt-grid"
      >
        {/* 見出しの行 (曜日 + 目盛り 9・12・15・18 を 1 回だけ) */}
        <div className="sticky left-0 z-[2] border-b border-r border-border-default bg-bg-muted px-3 py-1.5 font-medium text-text-secondary">
          職員氏名
        </div>
        {WEEKDAYS.map((wd) => (
          <div
            key={`h-${wd}`}
            className={cn(
              'border-b border-r border-border-default bg-bg-muted px-1 pt-1 font-medium text-text-secondary',
              dayIso(wd) === todayIso && 'bg-brand-primary-50',
            )}
            data-testid={`swt-head-${wd}`}
          >
            <span className="block px-1">
              {format(addDays(weekStart, wd), 'M/d')}（{WEEKDAY_LABELS[wd]}）
            </span>
            <div className="relative h-4" aria-hidden>
              {TICK_HOURS.map((h) => (
                <span
                  key={h}
                  className="tnum absolute -translate-x-1/2 text-[10px] text-text-muted"
                  style={{ left: `${pct(h * 60)}%` }}
                >
                  {h}
                </span>
              ))}
            </div>
          </div>
        ))}

        {rows.map((row) => {
          const rowKey = row.staffId;
          const isUnassigned = rowKey === UNASSIGNED_ROW_KEY;
          const offs = WEEKDAYS.flatMap((wd) => {
            const off = isUnassigned ? undefined : offByStaffWeekday?.get(`${rowKey}:${wd}`);
            return off ? [{ wd, off }] : [];
          });
          return (
            <React.Fragment key={rowKey}>
              <div
                className={cn(
                  'sticky left-0 z-[1] border-b border-r border-border-default bg-bg-base px-3 py-1.5',
                  highlightStaffId === rowKey && 'bg-brand-primary-50',
                )}
                data-testid={`swt-name-${rowKey}`}
              >
                <span className="block truncate font-bold text-text-primary">{row.name}</span>
                {row.office || row.isTrainee ? (
                  <span className="block text-[10px] text-text-muted">
                    {row.office ?? ''}
                    {row.isTrainee ? '・⚠新人' : ''}
                  </span>
                ) : null}
                {dayCounts ? (
                  <span
                    className="tnum block text-[11px] font-bold text-text-secondary"
                    data-testid={`swt-week-total-${rowKey}`}
                  >
                    週 {weekVisitTotal(dayCounts, rowKey)}件
                  </span>
                ) : null}
                {offs.length > 0 ? (
                  <span className="mt-0.5 flex flex-wrap gap-0.5">
                    {offs.map(({ wd, off }) => (
                      <span
                        key={wd}
                        className="rounded bg-amber-100 px-1 py-px text-[10px] font-bold text-amber-800"
                        title={off.note ?? undefined}
                        data-testid={`swt-off-${rowKey}-${wd}`}
                      >
                        {WEEKDAY_LABELS[wd]} {off.type}
                      </span>
                    ))}
                  </span>
                ) : null}
              </div>
              {WEEKDAYS.map((wd) => {
                const cellKey = staffDayCountKey(rowKey, wd);
                const cellVisits = visitsByCell.get(cellKey) ?? EMPTY_VISITS;
                return (
                  <SwtCell
                    key={wd}
                    rowKey={rowKey}
                    wd={wd}
                    isTrainee={row.isTrainee === true}
                    cellVisits={cellVisits}
                    companions={companionVisitsByCell?.get(cellKey) ?? EMPTY_VISITS}
                    cellEvents={eventsByCell.get(cellKey) ?? EMPTY_EVENTS}
                    off={isUnassigned ? undefined : offByStaffWeekday?.get(`${rowKey}:${wd}`)}
                    count={dayCounts?.get(cellKey)}
                    showCount={dayCounts != null}
                    countLimit={countLimit}
                    isToday={dayIso(wd) === todayIso}
                    past={isPastDay(wd)}
                    droppable={canDropOn(rowKey, wd)}
                    canEdit={canEdit}
                    highlighted={highlightStaffId === rowKey}
                    dragActive={dragging != null}
                    draggingVisitId={
                      dragging != null && cellVisits.some((v) => v.id === dragging)
                        ? dragging
                        : null
                    }
                    isOver={overCell === cellKey}
                    onDropCell={onDropCell}
                    onOverCell={setOverCell}
                    onBarDragStart={onBarDragStart}
                    onBarDragEnd={onBarDragEnd}
                    onVisitClick={onVisitClickStable}
                  />
                );
              })}
            </React.Fragment>
          );
        })}
      </div>
    </SyncedHScroll>
  );
}

const EMPTY_VISITS: TimelineVisit[] = [];
const EMPTY_EVENTS: TimelineEvent[] = [];

interface SwtCellProps {
  rowKey: string;
  wd: number;
  isTrainee: boolean;
  cellVisits: TimelineVisit[];
  companions: TimelineVisit[];
  cellEvents: TimelineEvent[];
  off: WeekOverrideRead | undefined;
  count: StaffDayCount | undefined;
  showCount: boolean;
  countLimit: number | null;
  isToday: boolean;
  past: boolean;
  droppable: boolean;
  canEdit: boolean;
  highlighted: boolean;
  /** どれかの帯をつかんでいる (落とせるマスに点線を出す)。 */
  dragActive: boolean;
  /** このマスの帯をつかんでいるときだけその id (それ以外は null = 描き直さない)。 */
  draggingVisitId: string | null;
  isOver: boolean;
  onDropCell: (e: React.DragEvent<HTMLDivElement>, rowKey: string, wd: number) => void;
  onOverCell: React.Dispatch<React.SetStateAction<string | null>>;
  onBarDragStart: (data: DragData) => void;
  onBarDragEnd: () => void;
  onVisitClick: (v: TimelineVisit) => void;
}

/** 1 マス (職員 × 曜日)。React.memo = ドラッグ中は変わったマスだけ描き直す。 */
const SwtCell = React.memo(function SwtCell({
  rowKey,
  wd,
  isTrainee,
  cellVisits,
  companions,
  cellEvents,
  off,
  count,
  showCount,
  countLimit,
  isToday,
  past,
  droppable,
  canEdit,
  highlighted,
  dragActive,
  draggingVisitId,
  isOver,
  onDropCell,
  onOverCell,
  onBarDragStart,
  onBarDragEnd,
  onVisitClick,
}: SwtCellProps) {
  const cellKey = staffDayCountKey(rowKey, wd);
  // レーン: 訪問と同行の帯を同じ並びに (重ならないように)。
  const items = [
    ...cellVisits.flatMap((v) => {
      const sp = spanOf(v.start_time, v.end_time);
      return sp ? [{ id: v.id, ...sp, visit: v, companion: false }] : [];
    }),
    ...companions.flatMap((v) => {
      const sp = spanOf(v.start_time, v.end_time);
      return sp ? [{ id: `acc:${v.id}`, ...sp, visit: v, companion: true }] : [];
    }),
  ];
  const lanes = assignLanes(items);
  const laneCount = Math.max(1, ...items.map((it) => (lanes.get(it.id)?.lane ?? 0) + 1));
  const eventTop = TRACK_PAD;
  const barTop0 = TRACK_PAD + (cellEvents.length > 0 ? EVENT_H + 2 : 0);
  const height = barTop0 + laneCount * LANE_STEP + TRACK_PAD;
  return (
    <div
      className={cn(
        'relative border-b border-r border-border-subtle',
        isToday && 'bg-brand-primary-50/60',
        highlighted && 'bg-brand-primary-50',
      )}
      data-testid={`swt-cell-wrap-${rowKey}-${wd}`}
    >
      {/* 角の件数 */}
      {showCount ? (
        <div className="flex justify-end px-1 pt-0.5">
          <StaffDayCountBadge
            count={count}
            limit={countLimit}
            unassigned={rowKey === UNASSIGNED_ROW_KEY}
            testId={`staff-day-count-${rowKey}-${wd}`}
          />
        </div>
      ) : null}
      {/* 時間軸 (= 落とし先) */}
      <div
        className={cn(
          'relative mx-1 mb-1 rounded-sm',
          dragActive && droppable && 'outline-dashed outline-1 outline-brand-primary/40',
          isOver && 'bg-brand-primary/15 outline outline-2 outline-brand-primary',
        )}
        style={{
          height,
          backgroundImage: 'linear-gradient(90deg, var(--border-subtle) 1px, transparent 1px)',
          backgroundSize: `${(60 / TOTAL_MIN) * 100}% 100%`,
        }}
        data-droppable-id={buildStaffWeekTimelineCellId(rowKey, wd)}
        data-droppable={droppable ? 'true' : 'false'}
        data-testid={`swt-cell-${rowKey}-${wd}`}
        title={
          past
            ? '今日より前の日は動かせません'
            : isTrainee
              ? '新人は担当にできません（同行で割り当ててください）'
              : undefined
        }
        onDragOver={
          droppable
            ? (e) => {
                if (!e.dataTransfer?.types?.includes(SWT_VISIT_DND_MIME)) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                if (!isOver) onOverCell(cellKey);
              }
            : undefined
        }
        onDragLeave={() => onOverCell((c) => (c === cellKey ? null : c))}
        onDrop={(e) => onDropCell(e, rowKey, wd)}
      >
        {/* 働かない時間だけ斜線 (終日休みはマス全体) */}
        {offDutyRanges(off).map(([s, e2]) => (
          <div
            key={`off-${s}`}
            aria-hidden
            className="pointer-events-none absolute inset-y-0"
            style={{
              left: `${pct(s)}%`,
              width: `${wpct(e2 - s)}%`,
              backgroundImage: HATCH,
            }}
            data-testid={`swt-off-shade-${rowKey}-${wd}`}
          />
        ))}
        {/* イベント = 緑の細い帯 */}
        {cellEvents.flatMap((ev) => {
          const sp = spanOf(ev.start_time, ev.end_time);
          if (!sp) return [];
          return [
            <div
              key={`ev-${ev.id}`}
              className="pointer-events-auto absolute rounded-sm"
              style={{
                top: eventTop,
                height: EVENT_H,
                left: `${pct(sp.startMin)}%`,
                width: `${Math.max(wpct(sp.endMin - sp.startMin), 1)}%`,
                background: 'var(--sched-event-bar)',
              }}
              title={`${hhmm(sp.startMin)}〜${hhmm(sp.endMin)} ${ev.title || ev.type}`}
              data-testid={`swt-event-${ev.id}`}
            />,
          ];
        })}
        {items.map((it) => {
          const top = barTop0 + (lanes.get(it.id)?.lane ?? 0) * LANE_STEP;
          const v = it.visit;
          const label = `${hhmm(it.startMin)}〜${hhmm(it.endMin)} ${v.patient_name ?? '（無名）'}${
            v.course_label ? ` ${v.course_label}` : ''
          }`;
          const style: React.CSSProperties = {
            top,
            height: BAR_H,
            left: `${pct(it.startMin)}%`,
            width: `${Math.max(wpct(it.endMin - it.startMin), 1.5)}%`,
          };
          if (it.companion) {
            return (
              <div
                key={it.id}
                className="pointer-events-none absolute rounded-sm border border-dashed opacity-70"
                style={{
                  ...style,
                  borderColor: 'var(--border-strong)',
                  background: 'var(--bg-base)',
                }}
                title={`同行 ${label}（件数には入れません）`}
                data-testid={`swt-companion-${rowKey}-${v.id}`}
              />
            );
          }
          const pal = genderPalette(v.patient_sex);
          const grabbable = canEdit && !past;
          return (
            <div
              key={it.id}
              role="button"
              tabIndex={0}
              aria-label={label}
              className={cn(
                'absolute rounded-sm border border-l-[3px]',
                grabbable ? 'cursor-grab active:cursor-grabbing' : 'cursor-pointer',
                draggingVisitId === v.id && 'opacity-40',
              )}
              style={{
                ...style,
                background: pal.bg,
                borderColor: pal.ln,
                borderLeftColor: pal.bar,
              }}
              title={grabbable ? label : `${label}（今日より前の日は動かせません）`}
              draggable={grabbable}
              aria-disabled={grabbable ? undefined : true}
              onDragStart={
                grabbable
                  ? (e) => {
                      const track = e.currentTarget.parentElement;
                      const tr = track?.getBoundingClientRect();
                      const br = e.currentTarget.getBoundingClientRect();
                      const pxPerMin = tr && tr.width > 0 ? tr.width / TOTAL_MIN : 0;
                      const grabMin =
                        pxPerMin > 0 && Number.isFinite(e.clientX)
                          ? Math.max(0, (e.clientX - br.left) / pxPerMin)
                          : 0;
                      const data: DragData = { visitId: v.id, grabMin };
                      e.dataTransfer?.setData(SWT_VISIT_DND_MIME, JSON.stringify(data));
                      if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
                      onBarDragStart(data);
                    }
                  : undefined
              }
              onDragEnd={onBarDragEnd}
              onClick={() => onVisitClick(v)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onVisitClick(v);
                }
              }}
              data-testid={`swt-visit-${v.id}`}
            />
          );
        })}
      </div>
    </div>
  );
});
