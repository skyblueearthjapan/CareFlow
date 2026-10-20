'use client';

/**
 * 「休み・勤務表」の職員×日付の表 (設計 §5・モック「休み・勤務表（案A）」)。
 *
 * 名前の列を固定し、表の中だけ横にスクロールする (ページは横に広がらない)。
 * マスを押すと onCellClick (押すと入るもの／小窓はページが決める)。
 */
import { useMemo, type CSSProperties } from 'react';

import { cn } from '@/lib/utils';
import {
  cellKey,
  fmtClock,
  fmtDayJp,
  fmtNum,
  LEAVE_LABEL,
  LEAVE_SHORT,
  WEEKDAY_FROM_MON,
} from '@/lib/leave-sheet';
import type {
  LeaveMonthCell,
  LeaveMonthDay,
  LeaveMonthStaff,
  LeaveMonthView,
  LeaveSendStatus,
} from '@/lib/schemas/leave';

const SEND_STATUS_LABEL: Record<LeaveSendStatus, string> = {
  baseline: 'カイポケと照合前',
  unsent: 'カイポケへ未送信',
  sent: 'カイポケへ送信済み',
  kaipoke_action: 'カイポケで要対応',
};

/** 承認後に取り消した休みの斜線。 */
const STRIPES: CSSProperties = {
  backgroundImage:
    'repeating-linear-gradient(135deg, transparent 0 5px, var(--border-strong) 5px 6px)',
};

export interface LeaveSheetGridProps {
  view: LeaveMonthView;
  /** 申請中だけを目立たせる (ほかを薄く)。 */
  focusPending: boolean;
  /** 申請中の一覧に指を置いたマス (マスと列が光る)。 */
  highlight: { staffId: string; date: string } | null;
  /** その人の行を光らせる (?staff= で開いたとき)。 */
  highlightStaffId: string | null;
  onCellClick: (staffId: string, date: string, el: HTMLElement) => void;
}

export function LeaveSheetGrid({
  view,
  focusPending,
  highlight,
  highlightStaffId,
  onCellClick,
}: LeaveSheetGridProps) {
  const cells = useMemo(
    () => new Map(view.cells.map((c) => [cellKey(c.staff_id, c.date), c])),
    [view.cells],
  );

  return (
    <div
      className="max-w-full overflow-x-auto overscroll-x-contain"
      data-testid="leave-grid-scroll"
    >
      <table
        className="border-separate border-spacing-0 text-sm tnum"
        aria-label="職員ごとの休み（月の表）"
      >
        <thead>
          <tr>
            <th
              scope="col"
              className="sticky left-0 top-0 z-30 min-w-[132px] border-b border-r border-border-default bg-bg-base px-2 py-1 text-left text-xs font-medium text-text-secondary"
            >
              職員
            </th>
            {view.days.map((d) => (
              <DayHeader key={d.date} day={d} highlighted={highlight?.date === d.date} />
            ))}
            <th
              scope="col"
              className="sticky top-0 z-20 min-w-[180px] border-b border-border-default bg-bg-base px-2 py-1 text-left text-xs font-medium text-text-secondary"
            >
              休日数（必要 {view.required_holidays} 日）
            </th>
          </tr>
        </thead>
        <tbody>
          {view.staff.map((s) => (
            <tr key={s.id} data-staff-row={s.id}>
              <th
                scope="row"
                className={cn(
                  'sticky left-0 z-10 min-w-[132px] whitespace-nowrap border-b border-r border-border-default bg-bg-base px-2 py-1 text-left font-medium text-text-primary',
                  highlightStaffId === s.id && 'bg-brand-primary-50',
                )}
              >
                {s.name}
                <span className="block text-xs font-normal text-text-muted">
                  {[s.code, s.is_full_time ? '常勤' : '非常勤', s.is_trainee ? '新人' : null]
                    .filter(Boolean)
                    .join('・')}
                </span>
              </th>
              {view.days.map((d) => (
                <td
                  key={d.date}
                  className={cn(
                    'border-b border-r border-border-default p-0',
                    highlight?.date === d.date && 'bg-brand-primary-50',
                  )}
                >
                  <SheetCell
                    staff={s}
                    day={d}
                    cell={cells.get(cellKey(s.id, d.date))}
                    focusPending={focusPending}
                    highlighted={highlight?.staffId === s.id && highlight.date === d.date}
                    onClick={(el) => onCellClick(s.id, d.date, el)}
                  />
                </td>
              ))}
              <td className="border-b border-border-default bg-bg-base px-2 py-1 align-middle">
                <StaffSummary staff={s} />
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th
              scope="row"
              className="sticky left-0 z-10 whitespace-nowrap border-r border-border-default bg-bg-muted px-2 py-1 text-left text-sm font-medium text-text-primary"
            >
              その日休む人
              <span className="block text-xs font-normal text-text-muted">
                承認済み・半休も 1 人・新人は除く
              </span>
            </th>
            {view.days.map((d) => (
              <td
                key={d.date}
                data-testid={`off-count-${d.date}`}
                data-warn={d.off_warn ? 'true' : 'false'}
                title={d.off_warn ? `休みが ${d.off_count} 人重なっています` : undefined}
                className={cn(
                  'border-r border-border-default px-0 py-1 text-center text-xs',
                  d.off_warn
                    ? 'bg-warning-bg font-bold text-warning-strong'
                    : 'bg-bg-muted text-text-secondary',
                )}
              >
                {d.off_count || ''}
              </td>
            ))}
            <td className="bg-bg-muted px-2 py-1 text-xs text-text-muted">
              {view.off_warn_threshold} 人以上の重なりを黄色
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function DayHeader({ day, highlighted }: { day: LeaveMonthDay; highlighted: boolean }) {
  const dayNum = Number(day.date.slice(8));
  const sat = day.weekday === 5;
  const red = day.weekday === 6 || !!day.holiday_name;
  const title = [
    day.holiday_name,
    day.closed_label ? `事業所の休み：${day.closed_label}` : null,
    day.pending_count ? `申請 ${day.pending_count} 件` : null,
  ]
    .filter(Boolean)
    .join('・');
  return (
    <th
      scope="col"
      data-col={day.date}
      title={title || undefined}
      className={cn(
        'sticky top-0 z-20 min-w-[44px] border-b border-r border-border-default bg-bg-base px-0 py-1 text-center align-top text-xs font-medium text-text-secondary',
        day.closed_label && 'border-t-4 border-t-border-strong bg-bg-muted',
        highlighted && 'bg-brand-primary-50',
      )}
    >
      <span className={cn('block text-sm font-bold text-text-primary', red && 'text-red-600')}>
        {dayNum}
      </span>
      <span className={cn('block', sat && 'text-blue-600', red && 'text-red-600')}>
        {WEEKDAY_FROM_MON[day.weekday]}
      </span>
      {day.holiday_name && (
        <span className="block text-xs text-red-600" aria-label={day.holiday_name}>
          祝
        </span>
      )}
      {day.closed_label && (
        <span
          className="block text-xs text-text-muted"
          aria-label={`事業所の休み ${day.closed_label}`}
        >
          所休
        </span>
      )}
      {day.pending_count > 0 && (
        <span className="mx-auto mt-0.5 block w-fit rounded-full bg-brand-primary px-1.5 text-xs font-bold leading-4 text-white">
          申{day.pending_count}
        </span>
      )}
    </th>
  );
}

interface SheetCellProps {
  staff: LeaveMonthStaff;
  day: LeaveMonthDay;
  cell: LeaveMonthCell | undefined;
  focusPending: boolean;
  highlighted: boolean;
  onClick: (el: HTMLElement) => void;
}

function SheetCell({ staff, day, cell, focusPending, highlighted, onClick }: SheetCellProps) {
  const leave = cell?.leave_type ?? null;
  const pending = cell?.pending ?? null;
  const rest = staff.off_weekdays.includes(day.weekday);
  const timeChange = !leave && cell?.time_change_start ? cell : null;

  let label = '';
  if (pending) label = `${LEAVE_SHORT[pending.override_type]}?`;
  else if (leave) label = LEAVE_SHORT[leave];
  else if (timeChange) label = '時';

  const visits = cell?.visit_count ?? 0;
  const showVisits =
    day.week_generated &&
    visits > 0 &&
    (!leave || leave === 'am_off' || leave === 'pm_off' || pending !== null);

  const status: string[] = [];
  if (leave) status.push(LEAVE_LABEL[leave]);
  if (cell?.source === 'admin') status.push('管理者が入れた');
  if (cell?.source === 'kaipoke') status.push('カイポケから取り込んだ');
  if (pending) {
    status.push(
      `${LEAVE_LABEL[pending.override_type]}を申請中${pending.kind === 'change' ? '（変更のお願い）' : ''}`,
    );
  }
  if (!leave && !pending && rest) status.push('勤務しない曜日');
  if (timeChange) {
    status.push(
      `時間変更 ${fmtClock(timeChange.time_change_start)}–${fmtClock(timeChange.time_change_end)}`,
    );
  }
  if (cell?.cancelled_after_approval) status.push('承認後に取り消し');
  if (cell?.send_status) status.push(SEND_STATUS_LABEL[cell.send_status]);
  if (showVisits) status.push(`訪問 ${visits} 件`);

  return (
    <button
      type="button"
      data-cell={cellKey(staff.id, day.date)}
      data-leave={leave ?? undefined}
      data-pending={pending ? pending.override_type : undefined}
      aria-label={`${staff.name} ${fmtDayJp(day.date)}${status.length ? ` ${status.join('・')}` : ''}`}
      onClick={(e) => onClick(e.currentTarget)}
      style={cell?.cancelled_after_approval && !leave ? STRIPES : undefined}
      className={cn(
        'relative block h-12 w-11 bg-bg-base p-0 text-xs font-bold text-brand-primary-hover transition-opacity',
        'hover:shadow-[inset_0_0_0_2px_var(--border-strong)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-primary',
        leave === 'off' && 'bg-brand-primary-light',
        leave === 'paid_off' && 'bg-emerald-100 text-emerald-800',
        !leave && rest && 'bg-bg-muted font-normal text-text-muted',
        pending &&
          (pending.override_type === 'paid_off'
            ? 'rounded ring-2 ring-inset ring-emerald-600'
            : 'rounded ring-2 ring-inset ring-brand-primary'),
        pending && !leave && 'bg-brand-primary-50',
        focusPending && !pending && 'opacity-30',
        highlighted && 'opacity-100 ring-[3px] ring-inset ring-brand-primary-hover',
      )}
    >
      {(leave === 'am_off' || leave === 'pm_off') && (
        <span
          aria-hidden
          className={cn(
            'absolute inset-x-0 h-1/2 bg-brand-primary-light',
            leave === 'am_off' ? 'top-0' : 'bottom-0',
          )}
        />
      )}
      {cell?.send_status && cell.send_status !== 'sent' && <SendMark status={cell.send_status} />}
      {(cell?.source === 'admin' || cell?.source === 'kaipoke') && (
        <span aria-hidden className="absolute right-0.5 top-0 text-xs font-normal text-text-muted">
          {cell.source === 'admin' ? '足' : 'カ'}
        </span>
      )}
      <span aria-hidden className="relative">
        {label}
      </span>
      {showVisits && (
        <span
          aria-hidden
          className="absolute inset-x-0 bottom-0 text-xs font-normal text-text-muted"
        >
          {visits}件
        </span>
      )}
    </button>
  );
}

/** 送信の状態の印 (照合前＝灰の丸・未送信＝白丸・要対応＝赤の「!」)。送信済みは出さない。 */
function SendMark({ status }: { status: LeaveSendStatus }) {
  if (status === 'kaipoke_action') {
    return (
      <span aria-hidden className="absolute left-0.5 top-0 text-xs font-bold text-error">
        !
      </span>
    );
  }
  return (
    <span
      aria-hidden
      className={cn(
        'absolute left-1 top-1 h-2 w-2 rounded-full',
        status === 'baseline' ? 'bg-text-muted' : 'border border-text-secondary bg-bg-base',
      )}
    />
  );
}

function StaffSummary({ staff }: { staff: LeaveMonthStaff }) {
  const short = staff.shortfall ?? 0;
  const afterPending = Math.max(0, short - staff.pending_gain);
  return (
    <div className="space-y-0.5 text-left" data-testid={`staff-summary-${staff.id}`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
        <span className="text-sm font-bold text-text-primary">
          {fmtNum(staff.holidays)}
          {staff.is_full_time && staff.required_holidays != null
            ? ` / ${staff.required_holidays}`
            : ' 日'}
        </span>
        {!staff.is_full_time ? (
          <span className="rounded bg-bg-muted px-1.5 text-xs text-text-secondary">
            対象外（非常勤）
          </span>
        ) : short > 0 ? (
          <span className="rounded bg-warning-bg px-1.5 text-xs font-bold text-warning-strong">
            あと {fmtNum(short)} 日
          </span>
        ) : (
          <span className="rounded bg-success-bg px-1.5 text-xs text-success">✓ 足りています</span>
        )}
      </div>
      <div className="text-xs text-text-muted">
        有給 {staff.paid_days} 日
        {staff.pending_count > 0 && (
          <>
            {' '}
            ・ 申請中 {staff.pending_count} 件
            {staff.is_full_time && short > 0 && staff.pending_gain > 0
              ? `（通れば あと ${fmtNum(afterPending)}）`
              : ''}
          </>
        )}
      </div>
    </div>
  );
}
