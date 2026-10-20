'use client';

/**
 * 「休み・勤務表」の右の一覧「申請中」(設計 §5・モックの右の欄)。
 *
 * 日付順 (日ごと「この日をまとめて承認」・休む人 今 N → 全部承認すると N) と
 * 職員順 (人ごと「この人をまとめて承認」・休日数) を切り替える。
 * 行に指を置くと表のマスと列が光る。「…」は表のマスの小窓を開く (種類を変えて承認・却下)。
 */
import { useMemo } from 'react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import {
  fmtDayJp,
  fmtNum,
  groupPending,
  LEAVE_LABEL,
  offCountIfAllApproved,
  type PendingGroup,
} from '@/lib/leave-sheet';
import type { LeaveMonthView, LeavePendingItem } from '@/lib/schemas/leave';

export interface LeavePendingPanelProps {
  view: LeaveMonthView;
  mode: 'date' | 'staff';
  onModeChange: (m: 'date' | 'staff') => void;
  focusPending: boolean;
  onFocusPendingChange: (v: boolean) => void;
  busy: boolean;
  onHighlight: (target: { staffId: string; date: string } | null) => void;
  onApprove: (item: LeavePendingItem) => void;
  onApproveMany: (items: LeavePendingItem[]) => void;
  onOpenCell: (item: LeavePendingItem) => void;
}

export function LeavePendingPanel({
  view,
  mode,
  onModeChange,
  focusPending,
  onFocusPendingChange,
  busy,
  onHighlight,
  onApprove,
  onApproveMany,
  onOpenCell,
}: LeavePendingPanelProps) {
  const groups = useMemo(() => groupPending(view.pending, mode), [view.pending, mode]);
  const dayByDate = useMemo(() => new Map(view.days.map((d) => [d.date, d])), [view.days]);
  const staffById = useMemo(() => new Map(view.staff.map((s) => [s.id, s])), [view.staff]);
  const threshold = view.off_warn_threshold;

  return (
    <aside
      aria-label="申請中の休み"
      className="flex min-w-0 flex-col gap-3 rounded-lg border border-border-default bg-brand-primary-50 p-3 lg:max-h-[640px] lg:overflow-y-auto"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-base font-bold text-text-primary">
          申請中
          <span className="rounded-full bg-brand-primary px-2 text-xs font-bold text-white tnum">
            {view.pending.length}
          </span>
        </h2>
        <div
          className="flex rounded-md border border-border-default bg-bg-base p-0.5"
          role="group"
          aria-label="並べ方"
        >
          {(['date', 'staff'] as const).map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={mode === m}
              onClick={() => onModeChange(m)}
              className={cn(
                'rounded px-2 py-1 text-sm',
                mode === m ? 'bg-brand-primary text-white' : 'text-text-secondary',
              )}
            >
              {m === 'date' ? '日付順' : '職員順'}
            </button>
          ))}
        </div>
      </div>
      <label className="flex items-center gap-2 text-sm text-text-secondary">
        <input
          type="checkbox"
          checked={focusPending}
          onChange={(e) => onFocusPendingChange(e.target.checked)}
        />
        表で申請中を目立たせる（ほかを薄く）
      </label>

      {groups.length === 0 ? (
        <p className="rounded-md border border-border-default bg-bg-base p-3 text-sm text-text-secondary">
          申請中の休みはありません。「この月を確定して通知」へ進めます。
        </p>
      ) : (
        groups.map((g) => (
          <GroupBox
            key={g.key}
            group={g}
            mode={mode}
            headNote={groupNote(g)}
            busy={busy}
            threshold={threshold}
            weekGenerated={(date) => dayByDate.get(date)?.week_generated ?? false}
            onHighlight={onHighlight}
            onApprove={onApprove}
            onApproveMany={onApproveMany}
            onOpenCell={onOpenCell}
          />
        ))
      )}
    </aside>
  );

  function groupNote(g: PendingGroup): { title: string; note: string; warn: boolean } {
    if (g.date) {
      const day = dayByDate.get(g.date);
      const now = day?.off_count ?? 0;
      const after = day ? offCountIfAllApproved(day, g.items) : now;
      const warn = after >= threshold;
      return {
        title: `${fmtDayJp(g.date)}${day?.holiday_name ? ` ${day.holiday_name}` : ''}`,
        note: `申請 ${g.items.length} 件 ・ 休む人 ${now} 人 → 全部承認すると ${after} 人${
          warn ? '（休みが重なります）' : ''
        }`,
        warn,
      };
    }
    const s = g.staffId ? staffById.get(g.staffId) : undefined;
    let note = '非常勤（対象外）';
    if (s?.is_full_time && s.required_holidays != null) {
      const short = s.shortfall ?? 0;
      note = `休日 ${fmtNum(s.holidays)} / ${s.required_holidays}${
        short > 0 ? ` ・ あと ${fmtNum(short)} 日` : ' ・ 足りています'
      }`;
    }
    return { title: g.staffName ?? '', note, warn: false };
  }
}

interface GroupBoxProps {
  group: PendingGroup;
  mode: 'date' | 'staff';
  headNote: { title: string; note: string; warn: boolean };
  busy: boolean;
  threshold: number;
  weekGenerated: (date: string) => boolean;
  onHighlight: LeavePendingPanelProps['onHighlight'];
  onApprove: LeavePendingPanelProps['onApprove'];
  onApproveMany: LeavePendingPanelProps['onApproveMany'];
  onOpenCell: LeavePendingPanelProps['onOpenCell'];
}

function GroupBox({
  group,
  mode,
  headNote,
  busy,
  threshold,
  weekGenerated,
  onHighlight,
  onApprove,
  onApproveMany,
  onOpenCell,
}: GroupBoxProps) {
  return (
    <section
      aria-label={headNote.title}
      className="flex flex-col gap-2 rounded-lg border border-border-default bg-bg-base p-3"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <b className="text-sm text-text-primary">{headNote.title}</b>
          <p
            className={cn(
              'text-xs',
              headNote.warn ? 'font-bold text-warning-strong' : 'text-text-muted',
            )}
          >
            {headNote.note}
          </p>
        </div>
        {group.items.length > 1 && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => onApproveMany(group.items)}
          >
            {mode === 'date' ? 'この日をまとめて承認' : 'この人をまとめて承認'}
          </Button>
        )}
      </div>
      <ul className="flex flex-col gap-2">
        {group.items.map((it) => {
          const visits = it.past_day
            ? '休みの記録だけ'
            : !weekGenerated(it.date)
              ? '週は未作成'
              : it.move_visit_count > 0
                ? `訪問 ${it.move_visit_count} 件を担当なしへ`
                : '訪問なし';
          const label = `${mode === 'date' ? it.staff_name : fmtDayJp(it.date)} ${LEAVE_LABEL[it.override_type]}`;
          return (
            <li
              key={it.id}
              data-request={it.id}
              onMouseEnter={() => onHighlight({ staffId: it.staff_id, date: it.date })}
              onMouseLeave={() => onHighlight(null)}
              onFocus={() => onHighlight({ staffId: it.staff_id, date: it.date })}
              onBlur={() => onHighlight(null)}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 border-t border-dashed border-border-default pt-2 first:border-t-0 first:pt-0"
            >
              <div className="min-w-0">
                <p className="flex flex-wrap items-center gap-1 text-sm text-text-primary">
                  <span
                    className={cn(
                      'rounded px-1.5 text-xs font-bold',
                      it.override_type === 'paid_off'
                        ? 'bg-emerald-100 text-emerald-800'
                        : 'bg-brand-primary-light text-brand-primary-hover',
                    )}
                  >
                    {LEAVE_LABEL[it.override_type]}
                  </span>
                  <b>{mode === 'date' ? it.staff_name : fmtDayJp(it.date)}</b>
                  {it.kind === 'change' && (
                    <span className="rounded bg-amber-100 px-1.5 text-xs font-medium text-amber-800">
                      変更のお願い
                    </span>
                  )}
                </p>
                <p className="text-xs text-text-muted">
                  {it.note ? `${it.note} ・ ` : ''}
                  {visits}
                  {it.off_count_if_approved >= threshold && (
                    <span className="font-bold text-warning-strong">
                      {' '}
                      ・ 承認でこの日の休み {it.off_count_if_approved} 人
                    </span>
                  )}
                </p>
              </div>
              <div className="flex gap-1">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  aria-label={`${label} を表で開く（種類を変えて承認・却下）`}
                  onClick={() => onOpenCell(it)}
                >
                  …
                </Button>
                <Button
                  type="button"
                  size="sm"
                  disabled={busy}
                  aria-label={`${label} を承認`}
                  onClick={() => onApprove(it)}
                >
                  承認
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
