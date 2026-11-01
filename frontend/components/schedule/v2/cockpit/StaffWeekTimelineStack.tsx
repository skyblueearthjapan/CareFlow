'use client';

/**
 * StaffWeekTimelineStack — 職員の「週・タイムライン」(PO 決定 2026-11-01・案 B)。
 *
 * 設計 = `docs/plans/staff-week-timeline-stack-design-2026-11-01.md`。
 * 1 日の表 (`StaffTimelineView`) を `embedded` で月〜土の 6 段に縦に並べるだけで、
 * 週専用の描き方は持たない (帯・件数の札・印・未送信・取消・非稼働・休みは 1 日の表のまま)。
 *
 * - 横スクロールは 6 段をまとめて 1 つの `SyncedHScroll` で包む (6 段とも同じ幅なので位置がそろう)。
 * - 職員の並びは 6 段で同じ。知らない担当 (「（不明なスタッフ）」) は週の訪問から先に集めて
 *   6 段に同じ行として渡し、「（担当なし）」は 6 段とも常に出す。休みだけを曜日ごとに入れる。
 * - 上に固定した曜日の札を押すと、その段へ移る。開いたときは今日 (無ければ次の日) の段へ移る。
 * - ドラッグは段の中だけ (時刻・担当)。曜日は訪問のメニューの「📅 曜日を移動…」で変える。
 *
 * この部品は API を呼ばない (結線は CourseDayTablePanel の `handleTimelineVisitMove` ほか)。
 */
import * as React from 'react';
import { addDays, format } from 'date-fns';

import type { WeekOverrideRead } from '@/lib/queries/staff-overrides';
import { classifyVisitDisplay } from '@/lib/schedule/visitVisibility';
import type { StaffDayCount } from '@/lib/schedule/staffDayCount';
import { SyncedHScroll } from '@/components/ui/synced-h-scroll';

import { UNASSIGNED_ROW_KEY } from '../courseDnd';
import type { SuggestionBadgeMap } from './suggestionBadge';
import {
  resolveVisitRowKey,
  StaffTimelineView,
  type FixedAllDayBand,
  type StaffSwapPayload,
  type StaffTimelineRow,
  type TimelineEvent,
  type TimelineMarker,
  type TimelineVisit,
  type VisitMovePayload,
} from './StaffTimelineView';

const WEEKDAYS = [0, 1, 2, 3, 4, 5] as const;
const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;
/** 段へ移ったとき、上に固定した曜日の札の下へ見出しが来るように空ける幅 (px)。 */
const DAY_SCROLL_OFFSET_PX = 56;

/** 段を memo で包む = パネルが描き直されても、渡すものが同じ段は描き直さない。 */
const DayTimeline = React.memo(StaffTimelineView);

export interface StaffWeekTimelineStackProps {
  /** 対象週の月曜。 */
  weekStart: Date;
  /** 在籍の職員 (並びはこのまま 6 段で使う)。`off` は曜日ごとに入れ直すので見ない。 */
  staffRows: StaffTimelineRow[];
  /** `${staffId}:${weekday}` → その日の休み/時間変更。 */
  offByStaffWeekday?: Map<string, WeekOverrideRead>;
  /** 週の全訪問 (段が `weekday` で自分の分を拾う)。 */
  visits: TimelineVisit[];
  events?: Map<string, TimelineEvent[]>;
  /** 曜日 (0=月..5=土) ごとの「全員（固定）」帯。 */
  fixedAllDayByDay?: FixedAllDayBand[][];
  markers?: TimelineMarker[];
  unsentIds?: Set<string> | null;
  /** 今日 (JST・YYYY-MM-DD)。今日を含む過去の段はドラッグ不可。 */
  todayIso: string;
  canEdit: boolean;
  assignedStaffByTemplateWeekday?: Map<string, string>;
  courseIdByTemplateWeekday?: Map<string, string>;
  suggestionBadges?: SuggestionBadgeMap;
  highlightStaffId?: string | null;
  showInactive?: boolean;
  dayCounts?: Map<string, StaffDayCount>;
  countLimit?: number | null;
  companionVisitsByCell?: Map<string, TimelineVisit[]>;
  onVisitClick: (visit: TimelineVisit, anchorEl: HTMLElement) => void;
  onVisitMove: (payload: VisitMovePayload) => void;
  onEventClick?: (ev: TimelineEvent, staffId: string) => void;
  onStaffSwap?: (payload: StaffSwapPayload) => void;
  onMarkOff?: (staffId: string, day: number, anchorEl: HTMLElement) => void;
  onAddVisit?: (staffId: string | null, day: number, anchorEl: HTMLElement) => void;
  onAddEvent?: (staffId: string, day: number) => void;
  onSuggestCourse?: (courseId: string, weekday: number, anchorEl: HTMLElement) => void;
}

type Handlers = Pick<
  StaffWeekTimelineStackProps,
  | 'onVisitClick'
  | 'onVisitMove'
  | 'onEventClick'
  | 'onStaffSwap'
  | 'onMarkOff'
  | 'onAddVisit'
  | 'onAddEvent'
  | 'onSuggestCourse'
>;

export function StaffWeekTimelineStack({
  weekStart,
  staffRows,
  offByStaffWeekday,
  visits,
  events,
  fixedAllDayByDay,
  markers,
  unsentIds,
  todayIso,
  canEdit,
  assignedStaffByTemplateWeekday,
  courseIdByTemplateWeekday,
  suggestionBadges,
  highlightStaffId,
  showInactive = false,
  dayCounts,
  countLimit = null,
  companionVisitsByCell,
  ...handlers
}: StaffWeekTimelineStackProps) {
  // ── 渡す手は参照を変えない (パネルは毎回新しい手を渡すため、段の memo が効かなくなる) ──
  const handlersRef = React.useRef<Handlers>(handlers);
  handlersRef.current = handlers;
  const stable = React.useMemo(
    () => ({
      onVisitClick: (visit: TimelineVisit, el: HTMLElement) =>
        handlersRef.current.onVisitClick(visit, el),
      onVisitMove: (payload: VisitMovePayload) => handlersRef.current.onVisitMove(payload),
      onEventClick: (ev: TimelineEvent, staffId: string) =>
        handlersRef.current.onEventClick?.(ev, staffId),
      onStaffSwap: (payload: StaffSwapPayload) => handlersRef.current.onStaffSwap?.(payload),
      onMarkOff: (staffId: string, day: number, el: HTMLElement) =>
        handlersRef.current.onMarkOff?.(staffId, day, el),
      onAddVisit: (staffId: string | null, day: number, el: HTMLElement) =>
        handlersRef.current.onAddVisit?.(staffId, day, el),
      onAddEvent: (staffId: string, day: number) => handlersRef.current.onAddEvent?.(staffId, day),
      onSuggestCourse: (courseId: string, weekday: number, el: HTMLElement) =>
        handlersRef.current.onSuggestCourse?.(courseId, weekday, el),
    }),
    [],
  );
  // 渡されていない手は渡さない (部品はそれでボタンや ⠿ を出し分ける)。
  const has = {
    onEventClick: handlers.onEventClick != null,
    onStaffSwap: handlers.onStaffSwap != null,
    onMarkOff: handlers.onMarkOff != null,
    onAddVisit: handlers.onAddVisit != null,
    onAddEvent: handlers.onAddEvent != null,
    onSuggestCourse: handlers.onSuggestCourse != null,
  };

  // ── 知らない担当の行 (6 段で同じ行を出すため、週の訪問から先に集める) ──
  const unknownRows = React.useMemo<StaffTimelineRow[]>(() => {
    const known = new Set(staffRows.map((r) => r.staffId));
    const unknown = new Set<string>();
    const note = (key: string | null | undefined) => {
      if (!key || key === UNASSIGNED_ROW_KEY || known.has(key)) return;
      unknown.add(key);
    };
    for (const v of visits) {
      if (classifyVisitDisplay(v, { showInactive }) === 'hidden') continue;
      note(resolveVisitRowKey(v, assignedStaffByTemplateWeekday));
    }
    for (const m of markers ?? []) {
      note(m.staffId);
      note(m.beforeStaffId);
    }
    return [...unknown].sort().map((key) => ({
      staffId: key,
      name: '（不明なスタッフ）',
      office: key.slice(0, 8),
      unknown: true,
    }));
  }, [staffRows, visits, markers, showInactive, assignedStaffByTemplateWeekday]);

  /** 曜日ごとの行。並びは 6 段で同じで、休みだけを曜日ごとに入れる。 */
  const rowsByDay = React.useMemo<StaffTimelineRow[][]>(
    () =>
      WEEKDAYS.map((wd) => [
        ...staffRows.map((r) => ({
          ...r,
          off: offByStaffWeekday?.get(`${r.staffId}:${wd}`) ?? null,
        })),
        ...unknownRows,
      ]),
    [staffRows, offByStaffWeekday, unknownRows],
  );

  const dayIsos = React.useMemo(
    () => WEEKDAYS.map((wd) => format(addDays(weekStart, wd), 'yyyy-MM-dd')),
    [weekStart],
  );

  // ── 曜日の札 → その段へ移る ──
  const dayRefs = React.useRef<Array<HTMLElement | null>>([]);
  const jumpTo = React.useCallback((wd: number, behavior: ScrollBehavior = 'smooth') => {
    const el = dayRefs.current[wd];
    if (!el) return;
    // 縦に送れるいちばん近い入れ物 (画面の main) だけを送る。scrollIntoView は外側の
    // 入れ物やページ全体まで動かしてしまうため使わない。
    let box: HTMLElement | null = el.parentElement;
    while (box) {
      const oy = getComputedStyle(box).overflowY;
      if ((oy === 'auto' || oy === 'scroll') && box.scrollHeight > box.clientHeight) break;
      box = box.parentElement;
    }
    // 中に入れ物が無い画面では、ページそのものが入れ物になる。
    const scroller = box ?? (document.scrollingElement as HTMLElement | null);
    if (!scroller) return;
    const boxTop = box ? box.getBoundingClientRect().top : 0;
    const top = scroller.scrollTop + el.getBoundingClientRect().top - boxTop - DAY_SCROLL_OFFSET_PX;
    scroller.scrollTo?.({ top, behavior });
  }, []);

  // 開いたときは今日 (無ければ次の日) の段へ移る。月曜なら動かさない。
  React.useEffect(() => {
    const target = dayIsos.findIndex((iso) => iso >= todayIso);
    if (target > 0) jumpTo(target, 'auto');
    // 週を変えたときだけ動かす (今日の日付が変わる程度で勝手に動かさない)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dayIsos, jumpTo]);

  return (
    <div
      className="rounded-lg border"
      style={{ borderColor: 'var(--border-default)', background: 'var(--bg-base)' }}
      data-testid="staff-week-timeline-stack"
      aria-label="職員の週タイムライン"
    >
      {/* 操作の案内 (6 段で 1 回だけ)。 */}
      <div
        className="rounded-t-lg border-b px-2 py-1.5 text-[11px]"
        style={{
          borderColor: 'var(--border-default)',
          background: 'var(--bg-muted)',
          color: 'var(--text-muted)',
        }}
      >
        バーを横にドラッグ=時刻(15分刻み) / 同じ曜日の別の行へ=担当変更 / クリック=メニュー
        （曜日を変えるときはメニューの「📅 曜日を移動…」）
        {handlers.onStaffSwap && canEdit
          ? ' ／ 氏名の ⠿ を同じ曜日の別の行へ=その日の予定を丸ごと入れ替え'
          : ''}
      </div>

      {/* 曜日の札 (上に固定)。押すとその段へ移る。 */}
      <nav
        className="sticky top-0 z-[7] flex h-10 items-center gap-1 overflow-x-auto border-b px-2"
        style={{ borderColor: 'var(--border-default)', background: 'var(--bg-base)' }}
        aria-label="曜日の段へ移る"
        data-testid="swt-day-nav"
      >
        {WEEKDAYS.map((wd) => (
          <button
            key={wd}
            type="button"
            onClick={() => jumpTo(wd)}
            className="h-7 shrink-0 rounded border px-2 text-[12px] font-bold tabular-nums hover:border-brand-primary hover:text-brand-primary"
            style={{
              borderColor: 'var(--border-default)',
              background: 'var(--bg-base)',
              color: dayIsos[wd] === todayIso ? 'var(--brand-primary)' : 'var(--text-primary)',
            }}
            aria-label={`${format(addDays(weekStart, wd), 'M/d')}（${WEEKDAY_LABELS[wd]}）の段へ移る`}
            data-testid={`swt-jump-${wd}`}
          >
            {WEEKDAY_LABELS[wd]} {format(addDays(weekStart, wd), 'M/d')}
          </button>
        ))}
      </nav>

      <SyncedHScroll topOffsetClassName="top-10" data-testid="swt-hscroll">
        {WEEKDAYS.map((wd) => (
          <section
            key={wd}
            ref={(el) => {
              dayRefs.current[wd] = el;
            }}
            // 上に固定した札の下へ見出しが来るようにする。
            style={{ scrollMarginTop: DAY_SCROLL_OFFSET_PX }}
            className="border-b-2"
            data-testid={`swt-day-${wd}`}
            data-tl-day={wd}
            aria-label={`${format(addDays(weekStart, wd), 'M/d')}（${WEEKDAY_LABELS[wd]}）`}
          >
            <div
              className="border-b px-2 py-1.5"
              style={{ borderColor: 'var(--border-default)', background: 'var(--bg-muted)' }}
            >
              <span
                className="sticky left-2 text-sm font-bold"
                style={{
                  color: dayIsos[wd] === todayIso ? 'var(--brand-primary)' : 'var(--text-primary)',
                }}
                data-testid={`swt-day-head-${wd}`}
              >
                {format(addDays(weekStart, wd), 'M/d')}（{WEEKDAY_LABELS[wd]}）
              </span>
            </div>
            <DayTimeline
              embedded
              weekStart={weekStart}
              day={wd}
              staffRows={rowsByDay[wd]!}
              visits={visits}
              events={events}
              fixedAllDay={fixedAllDayByDay?.[wd]}
              markers={markers}
              unsentIds={unsentIds}
              canEdit={canEdit}
              // 当日以前 (JST) は実績の可能性があるためドラッグ不可 (1 日の表と同じ)。
              isPast={dayIsos[wd]! <= todayIso}
              assignedStaffByTemplateWeekday={assignedStaffByTemplateWeekday}
              alwaysShowUnassignedRow
              courseIdByTemplateWeekday={courseIdByTemplateWeekday}
              suggestionBadges={suggestionBadges}
              highlightStaffId={highlightStaffId}
              showInactive={showInactive}
              dayCounts={dayCounts}
              countLimit={countLimit}
              companionVisitsByCell={companionVisitsByCell}
              onVisitClick={stable.onVisitClick}
              onVisitMove={stable.onVisitMove}
              onEventClick={has.onEventClick ? stable.onEventClick : undefined}
              onStaffSwap={has.onStaffSwap ? stable.onStaffSwap : undefined}
              onMarkOff={has.onMarkOff ? stable.onMarkOff : undefined}
              onAddVisit={has.onAddVisit ? stable.onAddVisit : undefined}
              onAddEvent={has.onAddEvent ? stable.onAddEvent : undefined}
              onSuggestCourse={has.onSuggestCourse ? stable.onSuggestCourse : undefined}
            />
          </section>
        ))}
      </SyncedHScroll>
    </div>
  );
}
