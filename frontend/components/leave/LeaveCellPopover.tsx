'use client';

/**
 * 「休み・勤務表」のマスの小窓 (設計 §5・モックの「小窓」)。押したマスの近くに出す。
 *
 *   - 申請中: 内容・移す訪問の件数・「この日休む人：今 N 人 → 承認すると N 人」・
 *             承認／種類を変えて承認／却下 (理由は必須)
 *   - 登録済み: 種類を変える・取り消す (「元に戻す」は出さない・§5-3)
 *   - 出勤の日: 休みを入れる (時間変更があれば「消えます」と出す)
 *   - 勤務しない曜日: 説明だけ
 * 過去の日は「訪問は動かしません（休みの記録だけ）」。
 */
import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react';

import { Button } from '@/components/ui/button';
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import { fmtClock, fmtDayJp, LEAVE_DESC, LEAVE_LABEL, LEAVE_STAMP_TYPES } from '@/lib/leave-sheet';
import type {
  LeaveMonthCell,
  LeaveMonthDay,
  LeaveMonthStaff,
  LeavePendingItem,
  LeaveSource,
  LeaveTypeCode,
} from '@/lib/schemas/leave';

const SOURCE_TEXT: Record<LeaveSource, string> = {
  request: '本人の申請を承認済み',
  admin: '管理者が入れた休み',
  kaipoke: 'カイポケから取り込んだ休み',
};

export interface LeaveCellPopoverProps {
  anchor: HTMLElement;
  staff: LeaveMonthStaff;
  day: LeaveMonthDay;
  cell: LeaveMonthCell | undefined;
  /** このマスの申請中 (無ければ null)。 */
  pending: LeavePendingItem | null;
  /** この日のほかの申請中 (このマスを除く)。 */
  otherPendingSameDay: LeavePendingItem[];
  offWarnThreshold: number;
  /** 今日 (JST)。これより前の日は休みの記録だけ。 */
  today: string;
  busy: boolean;
  onClose: () => void;
  onSetLeave: (type: LeaveTypeCode) => void;
  onCancelLeave: () => void;
  onApprove: (overrideType: LeaveTypeCode | null) => void;
  onReject: (reason: string) => void;
}

export function LeaveCellPopover(props: LeaveCellPopoverProps) {
  const { anchor, staff, day, onClose } = props;
  const anchorRef = useRef<HTMLElement>(anchor);
  anchorRef.current = anchor;

  return (
    <Popover open onOpenChange={(open) => !open && onClose()}>
      <PopoverAnchor virtualRef={anchorRef as RefObject<HTMLElement>} />
      <PopoverContent
        side="bottom"
        align="center"
        collisionPadding={16}
        className="flex w-[min(360px,calc(100vw-32px))] flex-col gap-3 p-4 text-sm"
        aria-label={`${staff.name} ${fmtDayJp(day.date)}`}
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <PopoverBody {...props} />
        <div className="flex justify-end">
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            閉じる
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function PopoverBody({
  staff,
  day,
  cell,
  pending,
  otherPendingSameDay,
  offWarnThreshold,
  today,
  busy,
  onSetLeave,
  onCancelLeave,
  onApprove,
  onReject,
}: LeaveCellPopoverProps) {
  const leave = cell?.leave_type ?? null;
  const rest = staff.off_weekdays.includes(day.weekday);
  const past = pending?.past_day ?? day.date < today;
  const pastNote = past ? (
    <p className="text-sm text-text-secondary">
      今日より前の日です。訪問は動かしません（休みの記録だけ）。
    </p>
  ) : null;
  const title = (
    <div>
      <p className="font-bold text-text-primary">
        {staff.name}さん ・ {fmtDayJp(day.date)}
        {day.holiday_name && (
          <span className="ml-2 rounded bg-bg-muted px-1.5 text-xs font-normal text-red-600">
            {day.holiday_name}
          </span>
        )}
      </p>
    </div>
  );
  const visitLine = !day.week_generated
    ? 'この週はまだ作っていません（作るときに休みとして扱います）'
    : cell?.visit_count
      ? `この日の訪問 ${cell.visit_count} 件`
      : 'この日の訪問はありません';

  if (pending) {
    return (
      <PendingBody
        title={title}
        pending={pending}
        visitLine={visitLine}
        weekGenerated={day.week_generated}
        others={otherPendingSameDay}
        threshold={offWarnThreshold}
        busy={busy}
        onApprove={onApprove}
        onReject={onReject}
        pastNote={pastNote}
      />
    );
  }

  if (leave) {
    return (
      <>
        {title}
        <p className="text-sm text-text-secondary">
          {SOURCE_TEXT[cell?.source ?? 'admin']}
          {cell?.note ? ` ・ メモ：${cell.note}` : ''}
        </p>
        <div className="rounded-md bg-bg-muted px-3 py-2">
          <b>{LEAVE_LABEL[leave]}</b>（{LEAVE_DESC[leave]}）
        </div>
        {pastNote}
        <p className="text-xs text-text-muted">種類を変える</p>
        <TypeChoices
          current={leave}
          busy={busy}
          onPick={(t) => {
            if (t !== leave) onSetLeave(t);
          }}
        />
        <div className="flex justify-end">
          <Button type="button" variant="outline" size="sm" disabled={busy} onClick={onCancelLeave}>
            休みを取り消す
          </Button>
        </div>
      </>
    );
  }

  if (rest) {
    return (
      <>
        {title}
        <p className="text-sm text-text-secondary">
          もともと勤務しない曜日です（週の勤務の型）。休日に数えています。
        </p>
      </>
    );
  }

  return (
    <>
      {title}
      <p className="text-sm text-text-secondary">出勤の予定 ・ {visitLine}</p>
      {pastNote}
      {cell?.cancelled_after_approval && (
        <p className="text-xs text-text-muted">承認したあとに取り消した休みがある日です。</p>
      )}
      {cell?.time_change_start && (
        <div className="rounded-md bg-warning-bg px-3 py-2 text-sm text-warning-strong">
          時間変更 {fmtClock(cell.time_change_start)}–{fmtClock(cell.time_change_end)}
          は、休みを入れると消えます
        </div>
      )}
      <p className="text-xs text-text-muted">休みを入れる</p>
      <TypeChoices current={null} busy={busy} onPick={onSetLeave} />
    </>
  );
}

interface PendingBodyProps {
  title: ReactNode;
  pending: LeavePendingItem;
  visitLine: string;
  weekGenerated: boolean;
  others: LeavePendingItem[];
  threshold: number;
  busy: boolean;
  pastNote: ReactNode;
  onApprove: (overrideType: LeaveTypeCode | null) => void;
  onReject: (reason: string) => void;
}

function PendingBody({
  title,
  pending,
  visitLine,
  weekGenerated,
  others,
  threshold,
  busy,
  pastNote,
  onApprove,
  onReject,
}: PendingBodyProps) {
  const requested = pending.override_type;
  const [picked, setPicked] = useState<LeaveTypeCode | null>(
    requested === 'cancel' ? null : requested,
  );
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (rejecting) reasonRef.current?.focus();
  }, [rejecting]);

  const after = pending.off_count_if_approved;
  const allAfter = others.reduce(
    (n, it) => n + (it.off_count_if_approved - it.off_count_now),
    after,
  );
  const changed = picked !== null && requested !== 'cancel' && picked !== requested;

  return (
    <>
      {title}
      <p className="flex flex-wrap items-center gap-2 text-sm text-text-secondary">
        本人からの申請
        {pending.kind === 'change' && (
          <span className="rounded bg-amber-100 px-1.5 text-xs font-medium text-amber-800">
            変更のお願い
          </span>
        )}
      </p>
      <div className="rounded-md bg-bg-muted px-3 py-2">
        <b>{LEAVE_LABEL[requested]}</b>（{LEAVE_DESC[requested]}）
        {pending.current_leave_type && (
          <span className="block text-xs text-text-secondary">
            いまの登録：{LEAVE_LABEL[pending.current_leave_type]}
          </span>
        )}
        {pending.note && <span className="block">メモ：{pending.note}</span>}
      </div>
      {pastNote ??
        (pending.move_visit_count > 0 && weekGenerated ? (
          <div className="rounded-md bg-warning-bg px-3 py-2 text-sm text-warning-strong">
            承認すると、{visitLine}のうち {pending.move_visit_count} 件を「担当なし」へ移します
          </div>
        ) : (
          <p className="text-sm text-text-secondary">{visitLine}</p>
        ))}
      <div
        data-testid="pending-off-count"
        className={cn(
          'text-sm',
          after >= threshold
            ? 'rounded-md bg-warning-bg px-3 py-2 font-bold text-warning-strong'
            : 'text-text-secondary',
        )}
      >
        この日休む人：今 {pending.off_count_now} 人 → この申請を承認すると {after} 人
        {others.length > 0 && `（この日のほかの申請 ${others.length} 件も通すと ${allAfter} 人）`}
      </div>
      {requested !== 'cancel' && (
        <>
          <p className="text-xs text-text-muted">種類を変えて承認するとき</p>
          <TypeChoices current={picked} busy={busy} onPick={setPicked} />
        </>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={() => setRejecting(true)}
        >
          却下…
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={busy}
          onClick={() => onApprove(changed ? picked : null)}
        >
          {changed && picked ? `${LEAVE_LABEL[picked]}で承認する` : '承認する'}
        </Button>
      </div>
      {rejecting && (
        <div className="space-y-2">
          <label htmlFor="leave-reject-reason" className="block text-sm text-text-secondary">
            却下の理由（本人に届きます）<span className="text-error">*</span>
          </label>
          <Textarea
            id="leave-reject-reason"
            ref={reasonRef}
            rows={2}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="例: その日は人手が足りないため、別の日でお願いします"
          />
          <div className="flex justify-end">
            <Button
              type="button"
              variant="destructive"
              size="sm"
              disabled={busy || reason.trim().length === 0}
              onClick={() => onReject(reason.trim())}
            >
              却下して通知
            </Button>
          </div>
        </div>
      )}
    </>
  );
}

function TypeChoices({
  current,
  busy,
  onPick,
}: {
  current: LeaveTypeCode | null;
  busy: boolean;
  onPick: (t: LeaveTypeCode) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-1.5" role="group" aria-label="休みの種類">
      {LEAVE_STAMP_TYPES.map((t) => (
        <button
          key={t}
          type="button"
          disabled={busy}
          aria-pressed={current === t}
          onClick={() => onPick(t)}
          className={cn(
            'flex min-h-[44px] flex-col items-start justify-center rounded-md border px-2 py-1 text-left text-sm font-medium disabled:opacity-50',
            current === t
              ? 'border-brand-primary bg-brand-primary-50 text-text-primary'
              : 'border-border-default bg-bg-base text-text-primary hover:bg-bg-muted',
          )}
        >
          {LEAVE_LABEL[t]}
          <span className="text-xs font-normal text-text-muted">{LEAVE_DESC[t]}</span>
        </button>
      ))}
    </div>
  );
}
