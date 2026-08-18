'use client';

/**
 * /m/shifts — 出勤カレンダー (staff-shift-confirmation-design.md §4).
 *
 * 1か月分の出勤日/休みを読み取り専用で表示する。素材は既存 API
 * (週次シフト + 週次オーバーライド) を FE で畳み込み (lib/shift-calendar.ts)、
 * 本人の申請中 (pending staff_off) を輪郭で重畳する。
 * 月が管理者に確定されると確定バッジ (✓ M/d 確定) が付き、本人へは
 * ベル通知 (shift_confirmed) も届く。
 */
import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { CalendarHeart, ChevronLeft, ChevronRight } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import { Card } from '@/components/ui/card';
import { MobileSection } from '@/components/mobile/MobileSection';
import { cn } from '@/lib/utils';
import { usePendingRequests } from '@/lib/queries/pending_requests';
import { useShiftConfirmations } from '@/lib/queries/staff-shift-confirmations';
import { useStaffOverrides } from '@/lib/queries/staff-overrides';
import { useStaffShifts } from '@/lib/queries/staff-shifts';
import {
  buildShiftMonth,
  endOfMonth,
  fmtIsoLocal,
  startOfMonth,
  summarizeShiftMonth,
} from '@/lib/shift-calendar';

function LegendChip({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-text-muted">
      <span className={cn('inline-block h-3 w-3 rounded-full', className)} />
      {label}
    </span>
  );
}

/** '2026-09-01' 確定行の表示用 'M/d' (ローカル)。 */
function fmtConfirmedAt(iso: string): string {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export default function MobileShiftsPage() {
  const { data: session } = useSession();
  const staffId = session?.user?.staffId ?? null;

  const [month, setMonth] = useState<Date>(() => startOfMonth(new Date()));
  const monthStart = useMemo(() => fmtIsoLocal(startOfMonth(month)), [month]);
  const monthEnd = useMemo(() => fmtIsoLocal(endOfMonth(month)), [month]);

  const shiftsQuery = useStaffShifts(staffId);
  const overridesQuery = useStaffOverrides(staffId, { from: monthStart, to: monthEnd });
  const confirmationsQuery = useShiftConfirmations(staffId, { from: monthStart, to: monthStart });
  // 本人の pending staff_off (staff の GET は自分軸に自動絞り込み)
  const pendingQuery = usePendingRequests({
    request_type: 'staff_off',
    status: 'pending',
    target_staff_id: staffId ?? undefined,
    target_date_from: monthStart,
    target_date_to: monthEnd,
    limit: 100,
  });

  const days = useMemo(
    () =>
      buildShiftMonth({
        month,
        shifts: shiftsQuery.data?.shifts ?? [],
        overrides: overridesQuery.data ?? [],
      }),
    [month, shiftsQuery.data, overridesQuery.data],
  );
  const summary = useMemo(() => summarizeShiftMonth(days), [days]);
  const confirmation = confirmationsQuery.data?.[0] ?? null;

  const pendingDates = useMemo(
    () =>
      new Set(
        (pendingQuery.data?.items ?? [])
          .filter((i) => i.status === 'pending' && i.target_date)
          .map((i) => i.target_date!),
      ),
    [pendingQuery.data],
  );

  const dateOf = (iso: string) => new Date(iso + 'T00:00:00');
  const offDates = days.filter((d) => d.kind === 'off').map((d) => dateOf(d.date));
  const partialDates = days.filter((d) => d.kind === 'partial').map((d) => dateOf(d.date));
  const customDates = days.filter((d) => d.kind === 'custom').map((d) => dateOf(d.date));
  const nonworkingDates = days.filter((d) => d.kind === 'nonworking').map((d) => dateOf(d.date));
  const requestedDates = [...pendingDates].map(dateOf);

  const changeMonth = (delta: number) => {
    setMonth((m) => new Date(m.getFullYear(), m.getMonth() + delta, 1));
  };

  return (
    <MobileSection
      pose="calendar"
      title="出勤カレンダー"
      subtitle="今月の出勤日とお休みを確認できます"
    >
      {!staffId && (
        <Alert variant="destructive">
          <AlertTitle>表示できません</AlertTitle>
          <AlertDescription>
            スタッフIDが紐付いていないアカウントです。管理者にご連絡ください。
          </AlertDescription>
        </Alert>
      )}

      <Card className="p-2">
        <div className="flex items-center justify-between px-2 pt-1">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="前の月"
            onClick={() => changeMonth(-1)}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <div className="text-center">
            <p className="font-serif text-base font-bold text-text-primary tnum">
              {month.getFullYear()}年{month.getMonth() + 1}月
            </p>
            {confirmation ? (
              <Badge variant="success" className="mt-0.5 text-[10px]">
                ✓ {fmtConfirmedAt(confirmation.confirmed_at)} 確定
              </Badge>
            ) : (
              <p className="text-[10px] text-text-muted">調整中（確定すると通知が届きます）</p>
            )}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="次の月"
            onClick={() => changeMonth(1)}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>

        <Calendar
          month={month}
          onMonthChange={(m) => setMonth(startOfMonth(m))}
          showOutsideDays={false}
          modifiers={{
            offday: offDates,
            partial: partialDates,
            customtime: customDates,
            nonworking: nonworkingDates,
            requested: requestedDates,
          }}
          modifiersClassNames={{
            offday: 'bg-brand-primary-light text-brand-primary line-through',
            partial: 'bg-brand-primary-50 text-brand-primary',
            customtime: 'bg-bg-muted text-text-primary underline decoration-dotted',
            nonworking: 'text-text-muted opacity-50',
            requested: 'ring-2 ring-inset ring-brand-primary',
          }}
          classNames={{
            day: 'h-11 w-11 p-0 font-normal rounded-md',
            head_cell: 'text-text-muted rounded-md w-11 font-normal text-[0.8rem]',
            nav: 'hidden',
            caption: 'hidden',
          }}
          className="mx-auto w-fit"
        />
        <div className="flex flex-wrap justify-center gap-3 border-t border-border-default px-2 py-2">
          <LegendChip className="bg-brand-primary-light line-through" label="休み" />
          <LegendChip className="bg-brand-primary-50" label="半休" />
          <LegendChip className="bg-bg-muted" label="時間変更" />
          <LegendChip className="ring-2 ring-inset ring-brand-primary" label="申請中" />
          <LegendChip className="bg-bg-base border border-border-default" label="出勤" />
        </div>
      </Card>

      <Card className="p-4">
        <div className="grid grid-cols-2 gap-3 text-center">
          <div>
            <p className="text-xs text-text-muted">出勤</p>
            <p className="font-serif text-2xl font-bold tnum text-text-primary">
              {summary.workDays}
              <span className="ml-0.5 text-sm font-normal">日</span>
            </p>
          </div>
          <div>
            <p className="text-xs text-text-muted">休み</p>
            <p className="font-serif text-2xl font-bold tnum text-brand-primary">
              {summary.offDays}
              <span className="ml-0.5 text-sm font-normal">日</span>
            </p>
          </div>
        </div>
        <p className="mt-2 text-center text-[10px] text-text-muted">
          出勤には時間変更・半休の日を含みます
        </p>
      </Card>

      <Link
        href="/m/leave"
        className="flex items-center justify-between rounded-lg border border-border-default bg-bg-base p-4 transition-colors hover:bg-bg-muted"
      >
        <div className="flex items-center gap-3">
          <CalendarHeart className="h-5 w-5 text-brand-primary" />
          <div>
            <p className="font-medium text-text-primary">休みを申請する</p>
            <p className="text-xs text-text-muted">カレンダーから選ぶだけ</p>
          </div>
        </div>
        <ChevronRight className="h-4 w-4 text-text-muted" />
      </Link>
    </MobileSection>
  );
}
