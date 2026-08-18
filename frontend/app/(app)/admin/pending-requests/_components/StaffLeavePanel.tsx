'use client';

/**
 * StaffLeavePanel — 申請履歴ページ右カラムの「休み・月確定」パネル
 * (staff-shift-confirmation-design.md §3・2026-08-18 独立ページから移設)。
 *
 * スタッフを選ぶ → ミニ月カレンダー (5状態) + 申請中/登録済みの箇条書き +
 * 「この月を確定して通知」。日クリックで休みの追加/取消 (取消・却下・確定は
 * 本人へ通知)。スタッフ選択は親へ通知し、左の申請リストの絞り込みと連動する。
 */
import { useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { CalendarCheck, CalendarHeart, ChevronLeft, ChevronRight, Trash2 } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import { Card } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import { RakusukeNote } from '@/components/brand/Rakusuke';
import { StaffCombobox } from '@/components/master/StaffCombobox';
import { cn } from '@/lib/utils';
import {
  useApproveRequest,
  usePendingRequests,
  useRejectRequest,
} from '@/lib/queries/pending_requests';
import {
  staffOverridesScopeKey,
  useCreateOverride,
  useDeleteOverride,
  useStaffOverrides,
} from '@/lib/queries/staff-overrides';
import { useStaffShifts } from '@/lib/queries/staff-shifts';
import {
  useConfirmShiftMonth,
  useShiftConfirmations,
} from '@/lib/queries/staff-shift-confirmations';
import {
  buildShiftMonth,
  endOfMonth,
  fmtIsoLocal,
  startOfMonth,
  summarizeShiftMonth,
  type ShiftDay,
} from '@/lib/shift-calendar';
import type { PendingRequestV2Read } from '@/lib/schemas/pending_request';

const WEEKDAYS_JP = ['日', '月', '火', '水', '木', '金', '土'] as const;

function fmtJp(iso: string): string {
  const d = new Date(iso + 'T00:00:00');
  return `${d.getMonth() + 1}/${d.getDate()}（${WEEKDAYS_JP[d.getDay()]}）`;
}

function LegendChip({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-text-muted">
      <span className={cn('inline-block h-2.5 w-2.5 rounded-full', className)} />
      {label}
    </span>
  );
}

interface StaffLeavePanelProps {
  /** スタッフ選択の変更を親へ通知 (左の申請リストの絞り込み連動用)。'' = 解除 */
  onStaffChange?: (staffId: string) => void;
  className?: string;
}

export function StaffLeavePanel({ onStaffChange, className }: StaffLeavePanelProps) {
  const qc = useQueryClient();
  const [staffId, setStaffId] = useState('');
  const [month, setMonth] = useState<Date>(() => startOfMonth(new Date()));
  const monthStart = useMemo(() => fmtIsoLocal(startOfMonth(month)), [month]);
  const monthEnd = useMemo(() => fmtIsoLocal(endOfMonth(month)), [month]);

  const [rejectTarget, setRejectTarget] = useState<PendingRequestV2Read | null>(null);
  const [rejectReason, setRejectReason] = useState('');

  const shiftsQuery = useStaffShifts(staffId || null);
  const overridesQuery = useStaffOverrides(staffId || null, { from: monthStart, to: monthEnd });
  const confirmationsQuery = useShiftConfirmations(staffId || null, {
    from: monthStart,
    to: monthStart,
  });
  const requestsQuery = usePendingRequests({
    request_type: 'staff_off',
    target_staff_id: staffId || undefined,
    target_date_from: monthStart,
    target_date_to: monthEnd,
    limit: 100,
  });

  const createOverride = useCreateOverride(staffId);
  const deleteOverride = useDeleteOverride(staffId);
  const approveMut = useApproveRequest();
  const rejectMut = useRejectRequest();
  const confirmMut = useConfirmShiftMonth(staffId);

  const days = useMemo(
    () =>
      buildShiftMonth({
        month,
        shifts: shiftsQuery.data?.shifts ?? [],
        overrides: overridesQuery.data ?? [],
      }),
    [month, shiftsQuery.data, overridesQuery.data],
  );
  const dayByIso = useMemo(() => new Map(days.map((d) => [d.date, d])), [days]);
  const summary = useMemo(() => summarizeShiftMonth(days), [days]);
  const confirmation = confirmationsQuery.data?.[0] ?? null;

  const pendingItems = useMemo(
    () =>
      (requestsQuery.data?.items ?? [])
        .filter((i) => i.status === 'pending' && i.target_date)
        .sort((a, b) => (a.target_date! < b.target_date! ? -1 : 1)),
    [requestsQuery.data],
  );
  const pendingDates = useMemo(
    () => new Set(pendingItems.map((i) => i.target_date!)),
    [pendingItems],
  );
  const monthOverrides = useMemo(() => days.filter((d) => d.override !== null), [days]);

  const dateOf = (iso: string) => new Date(iso + 'T00:00:00');
  const modifierDates = (pick: (d: ShiftDay) => boolean) =>
    days.filter(pick).map((d) => dateOf(d.date));

  const busy =
    createOverride.isPending ||
    deleteOverride.isPending ||
    approveMut.isPending ||
    rejectMut.isPending ||
    confirmMut.isPending;

  const invalidateOverrides = () => {
    if (staffId) void qc.invalidateQueries({ queryKey: staffOverridesScopeKey(staffId) });
  };

  const selectStaff = (id: string) => {
    setStaffId(id);
    onStaffChange?.(id);
  };

  async function handleDayClick(day: Date) {
    if (!staffId || busy) return;
    const iso = fmtIsoLocal(day);
    if (!iso.startsWith(monthStart.slice(0, 7))) return;
    if (pendingDates.has(iso)) {
      toast.info('申請中の日です。「申請中の休み」から承認または却下してください');
      return;
    }
    const st = dayByIso.get(iso);
    if (st?.override) {
      if (
        !window.confirm(
          `${fmtJp(iso)} の「${st.override.type}」を取り消しますか？\n本人に通知が送られます。`,
        )
      )
        return;
      try {
        await deleteOverride.mutateAsync(st.override.id);
        toast.success(`${fmtJp(iso)} の「${st.override.type}」を取り消し、本人に通知しました`);
      } catch (e) {
        toast.error('取消に失敗しました', {
          description: e instanceof Error ? e.message : String(e),
        });
      }
      return;
    }
    if (!window.confirm(`${fmtJp(iso)} を「休み」として登録しますか？`)) return;
    try {
      await createOverride.mutateAsync({ date: iso, type: '休み' });
      toast.success(`${fmtJp(iso)} を休みとして登録しました`);
    } catch (e) {
      toast.error('登録に失敗しました', {
        description: e instanceof Error ? e.message : String(e),
      });
    }
  }

  async function handleApprove(item: PendingRequestV2Read) {
    try {
      await approveMut.mutateAsync(item.id);
      invalidateOverrides();
      toast.success(`${item.target_date ? fmtJp(item.target_date) : ''} の休み申請を承認しました`);
    } catch (e) {
      toast.error('承認に失敗しました', {
        description: e instanceof Error ? e.message : String(e),
      });
    }
  }

  async function handleRejectSubmit() {
    if (!rejectTarget || !rejectReason.trim()) return;
    try {
      await rejectMut.mutateAsync({ id: rejectTarget.id, rejection_reason: rejectReason.trim() });
      toast.success('却下し、本人に通知しました');
      setRejectTarget(null);
      setRejectReason('');
    } catch (e) {
      toast.error('却下に失敗しました', {
        description: e instanceof Error ? e.message : String(e),
      });
    }
  }

  async function handleConfirmMonth() {
    if (!staffId) return;
    const label = `${month.getFullYear()}年${month.getMonth() + 1}月`;
    const warn =
      pendingItems.length > 0
        ? `未処理の申請が ${pendingItems.length} 件あります。このまま${label}を確定しますか？\n（確定しても申請は残ります）`
        : `${label}の出勤カレンダーを確定して本人に通知しますか？`;
    if (!window.confirm(warn)) return;
    try {
      await confirmMut.mutateAsync({ month: monthStart });
      toast.success(`${label}を確定し、本人に通知しました`);
    } catch (e) {
      toast.error('確定に失敗しました', {
        description: e instanceof Error ? e.message : String(e),
      });
    }
  }

  const changeMonth = (delta: number) =>
    setMonth((m) => new Date(m.getFullYear(), m.getMonth() + delta, 1));

  const loading =
    !!staffId && (shiftsQuery.isLoading || overridesQuery.isLoading || requestsQuery.isLoading);

  return (
    <Card className={cn('flex flex-col overflow-hidden', className)}>
      {/* ヘッダー: ブランド淡色の帯でメイン表とトーンを分ける */}
      <div className="flex items-center gap-3 border-b border-border-default bg-brand-primary-50 px-4 py-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-primary/15">
          <CalendarHeart className="h-5 w-5 text-brand-primary" />
        </span>
        <div className="min-w-0">
          <h2 className="font-serif text-base font-bold leading-tight text-text-primary">
            休み・月確定
          </h2>
          <p className="truncate text-[11px] text-text-muted">
            スタッフ別に休みを調整し、月を確定して本人へ通知
          </p>
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-3 p-4">
        <StaffCombobox
          value={staffId}
          onChange={selectStaff}
          clearLabel="― 全員（絞り込みなし）"
          placeholder="スタッフを選択（未選択 = 全員）"
          className="w-full"
        />

        {!staffId && (
          // 左カラムとの下端揃えで伸びた分は空状態を縦中央に置いて埋める
          <div className="flex flex-1 items-center justify-center">
            <RakusukeNote
              pose="calendar"
              size="sm"
              title="スタッフを選んでください"
              comment="その人の休みカレンダーがここに表示されます"
            />
          </div>
        )}

        {staffId && (
          <>
            {/* 月ナビ */}
            <div className="flex items-center justify-between">
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                aria-label="前の月"
                onClick={() => changeMonth(-1)}
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <div className="text-center">
                <p className="font-serif text-sm font-bold tnum text-text-primary">
                  {month.getFullYear()}年{month.getMonth() + 1}月
                </p>
                {confirmation ? (
                  <Badge variant="success" className="mt-0.5 text-[10px]">
                    ✓ 確定済み
                  </Badge>
                ) : (
                  <p className="text-[10px] text-text-muted">未確定</p>
                )}
              </div>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                aria-label="次の月"
                onClick={() => changeMonth(1)}
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>

            {loading ? (
              <Skeleton className="mx-auto h-64 w-64" />
            ) : (
              <>
                <Calendar
                  month={month}
                  onMonthChange={(m) => setMonth(startOfMonth(m))}
                  showOutsideDays={false}
                  onDayClick={(d) => void handleDayClick(d)}
                  modifiers={{
                    offday: modifierDates((d) => d.kind === 'off'),
                    partial: modifierDates((d) => d.kind === 'partial'),
                    customtime: modifierDates((d) => d.kind === 'custom'),
                    nonworking: modifierDates((d) => d.kind === 'nonworking'),
                    requested: [...pendingDates].map(dateOf),
                  }}
                  modifiersClassNames={{
                    offday: 'bg-brand-primary-light text-brand-primary line-through',
                    partial: 'bg-brand-primary-50 text-brand-primary',
                    customtime: 'bg-bg-muted text-text-primary underline decoration-dotted',
                    nonworking: 'text-text-muted opacity-50',
                    requested: 'ring-2 ring-inset ring-brand-primary',
                  }}
                  classNames={{
                    day: 'h-9 w-9 p-0 text-sm font-normal rounded-md hover:bg-bg-muted',
                    head_cell: 'text-text-muted rounded-md w-9 font-normal text-[0.75rem]',
                    nav: 'hidden',
                    caption: 'hidden',
                  }}
                  className="mx-auto w-fit p-0"
                />
                <div className="flex flex-wrap justify-center gap-2 border-t border-border-default pt-2">
                  <LegendChip className="bg-brand-primary-light line-through" label="休み" />
                  <LegendChip className="bg-brand-primary-50" label="半休" />
                  <LegendChip className="bg-bg-muted" label="時間変更" />
                  <LegendChip className="ring-2 ring-inset ring-brand-primary" label="申請中" />
                  <LegendChip className="opacity-50 border border-border-default" label="勤務外" />
                </div>
                <p className="text-center text-[11px] text-text-muted tnum">
                  出勤 {summary.workDays} 日 ・ 休み {summary.offDays} 日 —
                  日をクリックで休みの追加/取消
                </p>
              </>
            )}

            {/* 申請中 */}
            <div>
              <h3 className="text-xs font-medium text-text-secondary">
                申請中の休み（{pendingItems.length}）
              </h3>
              {pendingItems.length === 0 ? (
                <p className="mt-1 text-xs text-text-muted">この月の未処理申請はありません</p>
              ) : (
                <ul className="mt-1.5 max-h-52 space-y-1.5 overflow-y-auto pr-1">
                  {pendingItems.map((item) => (
                    <li
                      key={item.id}
                      className="flex items-center justify-between gap-2 rounded-md border border-brand-primary-light bg-brand-primary-50/60 px-2 py-1.5"
                    >
                      <div className="min-w-0">
                        <p className="text-xs font-medium text-text-primary">
                          {fmtJp(item.target_date!)}
                        </p>
                        {typeof item.payload?.note === 'string' && item.payload.note && (
                          <p className="truncate text-[11px] text-text-muted">
                            {item.payload.note}
                          </p>
                        )}
                      </div>
                      <div className="flex shrink-0 gap-1">
                        <Button
                          type="button"
                          size="sm"
                          className="h-7 px-2 text-xs"
                          onClick={() => void handleApprove(item)}
                          disabled={busy}
                        >
                          承認
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          className="h-7 px-2 text-xs"
                          onClick={() => {
                            setRejectTarget(item);
                            setRejectReason('');
                          }}
                          disabled={busy}
                        >
                          却下
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* 登録済み */}
            <div>
              <h3 className="text-xs font-medium text-text-secondary">
                登録済みの休み・時間変更（{monthOverrides.length}）
              </h3>
              {monthOverrides.length === 0 ? (
                <p className="mt-1 text-xs text-text-muted">この月の登録はありません</p>
              ) : (
                <ul className="mt-1.5 max-h-52 space-y-1.5 overflow-y-auto pr-1">
                  {monthOverrides.map((d) => (
                    <li
                      key={d.override!.id}
                      className="flex items-center justify-between gap-2 rounded-md border border-border-default bg-bg-base px-2 py-1.5"
                    >
                      <div className="min-w-0">
                        <p className="text-xs font-medium text-text-primary">
                          <Badge
                            variant={d.kind === 'off' ? 'destructive' : 'secondary'}
                            className="mr-1.5 px-1.5 py-0 text-[10px]"
                          >
                            {d.override!.type}
                          </Badge>
                          {fmtJp(d.date)}
                          {d.override!.start_time && d.override!.end_time && (
                            <span className="ml-1.5 text-[11px] text-text-muted tnum">
                              {d.override!.start_time}〜{d.override!.end_time}
                            </span>
                          )}
                        </p>
                        {d.override!.note && (
                          <p className="truncate text-[11px] text-text-muted">
                            {d.override!.note}
                          </p>
                        )}
                      </div>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="h-7 w-7 shrink-0 p-0 text-text-muted hover:text-destructive"
                        aria-label={`${fmtJp(d.date)} の${d.override!.type}を取消`}
                        onClick={() => void handleDayClick(dateOf(d.date))}
                        disabled={busy}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* 確定 (カラム下端揃えで伸びた分は mt-auto で最下部へ) */}
            <div className="mt-auto border-t border-border-default pt-3">
              <Button
                type="button"
                className="w-full"
                onClick={() => void handleConfirmMonth()}
                disabled={busy}
              >
                <CalendarCheck className="mr-1 h-4 w-4" />
                {confirmation ? '再確定して通知' : 'この月を確定して通知'}
              </Button>
              <p className="mt-1.5 text-center text-[11px] text-text-muted">
                {confirmation
                  ? `${new Date(confirmation.confirmed_at).toLocaleString('ja-JP')} に通知済み。変更後は再確定で再通知できます`
                  : '確定すると本人へ通知され、出勤カレンダーに確定バッジが付きます'}
              </p>
            </div>
          </>
        )}
      </div>

      {/* 却下ダイアログ */}
      <Dialog
        open={rejectTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRejectTarget(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>休み申請を却下</DialogTitle>
            <DialogDescription>
              {rejectTarget?.target_date ? fmtJp(rejectTarget.target_date) : ''}{' '}
              の休み申請を却下します。理由は本人への通知に記載されます。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="leave-reject-reason">却下理由（必須）</Label>
            <textarea
              id="leave-reject-reason"
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              rows={3}
              maxLength={2000}
              className="w-full rounded-md border border-border-default bg-bg-base p-2 text-sm"
              placeholder="例: その日は人員不足のため出勤をお願いします"
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setRejectTarget(null)}>
              キャンセル
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => void handleRejectSubmit()}
              disabled={!rejectReason.trim() || rejectMut.isPending}
            >
              {rejectMut.isPending ? '送信中…' : '却下して通知'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
