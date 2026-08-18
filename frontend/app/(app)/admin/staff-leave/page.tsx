'use client';

/**
 * /admin/staff-leave — スタッフ休み・月確定 (staff-shift-confirmation-design.md §3).
 *
 * スタッフを選ぶ → その月の休みをカレンダー + 箇条書きで確認し、
 *   - 日クリックで休みを増やす (override 追加) / 減らす (override 取消 = 本人へ通知)
 *   - 申請中 (pending staff_off) は一覧から承認 / 却下 (却下 = 本人へ通知)
 *   - 「この月を確定」で確定記録を upsert し本人へ通知 (再確定 = 再通知)
 * モバイル側の対になる画面 = /m/shifts (出勤カレンダー・確定バッジ表示)。
 */
import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { useQueryClient } from '@tanstack/react-query';
import { CalendarCheck, ChevronLeft, ChevronRight, Trash2 } from 'lucide-react';

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
import { StaffCombobox } from '@/components/master/StaffCombobox';
import { cn } from '@/lib/utils';
import { isAdminRole } from '@/lib/rbac';
import { usePendingRequests, useApproveRequest, useRejectRequest } from '@/lib/queries/pending_requests';
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
    <span className="inline-flex items-center gap-1.5 text-xs text-text-muted">
      <span className={cn('inline-block h-3 w-3 rounded-full', className)} />
      {label}
    </span>
  );
}

export default function AdminStaffLeavePage() {
  const { data: session, status: sessionStatus } = useSession();
  const router = useRouter();
  const role = session?.user?.role;
  const isAuthorized = isAdminRole(role);

  // Soft client-side guard (API 側でも RBAC 強制)
  useEffect(() => {
    if (sessionStatus === 'authenticated' && !isAuthorized) {
      router.replace('/dashboard');
    }
  }, [sessionStatus, isAuthorized, router]);

  const qc = useQueryClient();
  const [staffId, setStaffId] = useState('');
  const [month, setMonth] = useState<Date>(() => startOfMonth(new Date()));
  const monthStart = useMemo(() => fmtIsoLocal(startOfMonth(month)), [month]);
  const monthEnd = useMemo(() => fmtIsoLocal(endOfMonth(month)), [month]);

  // 却下ダイアログ
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
  const monthOverrides = useMemo(
    () => days.filter((d) => d.override !== null),
    [days],
  );

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

  async function handleDayClick(day: Date) {
    if (!staffId || busy) return;
    const iso = fmtIsoLocal(day);
    if (!iso.startsWith(monthStart.slice(0, 7))) return;
    if (pendingDates.has(iso)) {
      toast.info('申請中の日です。下の「申請中の休み」から承認または却下してください');
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
    <div className="space-y-4 p-6">
      <div>
        <h1 className="font-serif text-xl font-bold text-text-primary">スタッフ休み・月確定</h1>
        <p className="mt-1 text-sm text-text-muted">
          スタッフを選んで休みを調整し、月の出勤カレンダーを確定して本人に通知します。
          日をクリックすると休みの追加・取消ができます（取消・却下・確定は本人に通知されます）。
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <StaffCombobox value={staffId} onChange={setStaffId} className="w-72" />
        <div className="flex items-center gap-1">
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label="前の月"
            onClick={() => changeMonth(-1)}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="min-w-28 text-center font-serif text-base font-bold tnum">
            {month.getFullYear()}年{month.getMonth() + 1}月
          </span>
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label="次の月"
            onClick={() => changeMonth(1)}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setMonth(startOfMonth(new Date()))}
          >
            今月
          </Button>
        </div>
      </div>

      {!staffId && (
        <Card className="p-8 text-center text-sm text-text-muted">
          スタッフを選択すると、その人の休みカレンダーが表示されます
        </Card>
      )}

      {staffId && (
        <div className="grid gap-4 lg:grid-cols-[auto_1fr]">
          <Card className="p-3">
            {loading ? (
              <Skeleton className="h-72 w-80" />
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
                    day: 'h-10 w-10 p-0 font-normal rounded-md hover:bg-bg-muted',
                    head_cell: 'text-text-muted rounded-md w-10 font-normal text-[0.8rem]',
                    nav: 'hidden',
                    caption: 'hidden',
                  }}
                />
                <div className="flex flex-wrap gap-3 border-t border-border-default px-1 pt-2">
                  <LegendChip className="bg-brand-primary-light line-through" label="休み" />
                  <LegendChip className="bg-brand-primary-50" label="半休" />
                  <LegendChip className="bg-bg-muted" label="時間変更" />
                  <LegendChip className="ring-2 ring-inset ring-brand-primary" label="申請中" />
                  <LegendChip className="opacity-50 border border-border-default" label="勤務外" />
                </div>
                <p className="mt-2 px-1 text-xs text-text-muted tnum">
                  出勤 {summary.workDays} 日 ・ 休み {summary.offDays} 日
                </p>
              </>
            )}
          </Card>

          <div className="space-y-4">
            <Card className="p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="font-serif text-base font-bold text-text-primary">
                    {month.getFullYear()}年{month.getMonth() + 1}月の確定
                  </h2>
                  {confirmation ? (
                    <p className="mt-1 text-xs text-text-muted">
                      <Badge variant="success" className="mr-1">
                        確定済み
                      </Badge>
                      {new Date(confirmation.confirmed_at).toLocaleString('ja-JP')} に通知済み。
                      変更した場合は再確定で再通知できます。
                    </p>
                  ) : (
                    <p className="mt-1 text-xs text-text-muted">
                      未確定です。確定すると本人へ通知され、モバイルの出勤カレンダーに
                      確定バッジが付きます。
                    </p>
                  )}
                </div>
                <Button type="button" onClick={() => void handleConfirmMonth()} disabled={busy}>
                  <CalendarCheck className="mr-1 h-4 w-4" />
                  {confirmation ? '再確定して通知' : 'この月を確定して通知'}
                </Button>
              </div>
            </Card>

            <Card className="p-4">
              <h2 className="font-serif text-base font-bold text-text-primary">
                申請中の休み（{pendingItems.length}件）
              </h2>
              {pendingItems.length === 0 && (
                <p className="mt-2 text-sm text-text-muted">この月の未処理申請はありません</p>
              )}
              {pendingItems.length > 0 && (
                <ul className="mt-2 space-y-2">
                  {pendingItems.map((item) => (
                    <li
                      key={item.id}
                      className="flex items-center justify-between gap-3 rounded-md border border-border-default bg-bg-base p-2"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-text-primary">
                          {fmtJp(item.target_date!)}
                        </p>
                        {typeof item.payload?.note === 'string' && item.payload.note && (
                          <p className="truncate text-xs text-text-muted">{item.payload.note}</p>
                        )}
                      </div>
                      <div className="flex shrink-0 gap-2">
                        <Button
                          type="button"
                          size="sm"
                          onClick={() => void handleApprove(item)}
                          disabled={busy}
                        >
                          承認
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
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
            </Card>

            <Card className="p-4">
              <h2 className="font-serif text-base font-bold text-text-primary">
                登録済みの休み・時間変更（{monthOverrides.length}件）
              </h2>
              {monthOverrides.length === 0 && (
                <p className="mt-2 text-sm text-text-muted">この月の登録はありません</p>
              )}
              {monthOverrides.length > 0 && (
                <ul className="mt-2 space-y-2">
                  {monthOverrides.map((d) => (
                    <li
                      key={d.override!.id}
                      className="flex items-center justify-between gap-3 rounded-md border border-border-default bg-bg-base p-2"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-text-primary">
                          <Badge
                            variant={d.kind === 'off' ? 'destructive' : 'secondary'}
                            className="mr-2"
                          >
                            {d.override!.type}
                          </Badge>
                          {fmtJp(d.date)}
                          {d.override!.start_time && d.override!.end_time && (
                            <span className="ml-2 text-xs text-text-muted tnum">
                              {d.override!.start_time}〜{d.override!.end_time}
                            </span>
                          )}
                        </p>
                        {d.override!.note && (
                          <p className="truncate text-xs text-text-muted">{d.override!.note}</p>
                        )}
                      </div>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => void handleDayClick(dateOf(d.date))}
                        disabled={busy}
                      >
                        <Trash2 className="mr-1 h-3.5 w-3.5" />
                        取消
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </div>
      )}

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
            <Label htmlFor="reject-reason">却下理由（必須）</Label>
            <textarea
              id="reject-reason"
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
    </div>
  );
}
