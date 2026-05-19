'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { Bell, ChevronRight, CalendarCheck, CalendarRange } from 'lucide-react';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { MobileSection } from '@/components/mobile/MobileSection';
import {
  addDays,
  currentMonthStartIso,
  currentWeekStartIso,
  nextMonthStartIso,
  todayIso,
  useMyVisits,
} from '@/lib/queries/me';
import { useUnreadNotificationCount } from '@/lib/queries/notifications';
import { NotificationListSheet } from '@/app/(mobile)/m/_components/NotificationListSheet';

function StatCard({
  label,
  value,
  loading,
}: {
  label: string;
  value: number | string;
  loading?: boolean;
}) {
  return (
    <Card className="p-4">
      <p className="text-xs text-text-muted">{label}</p>
      {loading ? (
        <Skeleton className="mt-2 h-8 w-16" />
      ) : (
        <p className="mt-2 font-serif text-3xl font-bold tnum text-text-primary">{value}</p>
      )}
    </Card>
  );
}

export default function MobileHomePage() {
  const { data: session } = useSession();
  const userName = session?.user?.name ?? 'スタッフ';

  const today = todayIso();
  const weekStart = currentWeekStartIso();
  // Exclusive upper bound for week count (`< weekEnd`). Built from local
  // date components via `addDays` so it never round-trips through UTC and
  // therefore can't drift across midnight in non-UTC locales.
  const weekEnd = addDays(weekStart, 7);
  const monthStart = currentMonthStartIso();
  const monthEnd = nextMonthStartIso();

  // Forward `week_start` / `week_end` (inclusive) to the backend so the
  // server narrows the result set instead of relying on client-side slicing
  // of a 100-row page. Range covers the broadest window any of the three
  // counters need: month start (this month) → end of current ISO week.
  // Inclusive `week_end` is `weekEnd - 1` because `weekEnd` is the
  // exclusive next-Monday. We also extend the lower bound to the start of
  // the current month so the "this month" count is correct.
  const rangeStart = monthStart < weekStart ? monthStart : weekStart;
  // Inclusive end = max(monthEnd-1, weekEnd-1). `monthEnd` is the first day
  // of next month (exclusive); subtract a day for an inclusive bound.
  const inclusiveMonthEnd = addDays(monthEnd, -1);
  const inclusiveWeekEnd = addDays(weekEnd, -1);
  const rangeEnd = inclusiveMonthEnd > inclusiveWeekEnd ? inclusiveMonthEnd : inclusiveWeekEnd;

  const {
    data: allVisits,
    isLoading,
    isError,
    error,
  } = useMyVisits({
    weekStartFilter: rangeStart,
    weekEndFilter: rangeEnd,
  });

  const todayCount = allVisits?.filter((v) => v.visit_date === today).length ?? 0;
  const weekCount =
    allVisits?.filter((v) => v.visit_date >= weekStart && v.visit_date < weekEnd).length ?? 0;
  const monthCount =
    allVisits?.filter((v) => v.visit_date >= monthStart && v.visit_date < monthEnd).length ?? 0;

  // W4-D: live unread count from /api/v1/notifications (60s poll).
  const unreadCount = useUnreadNotificationCount();
  const [notifOpen, setNotifOpen] = useState(false);

  return (
    <MobileSection
      title={
        <span>
          こんにちは、
          <span className="text-brand-primary">{userName}</span> さん
        </span>
      }
      subtitle={today}
      action={
        <button
          type="button"
          aria-label={unreadCount > 0 ? `通知 (未読 ${unreadCount}件)` : '通知'}
          onClick={() => setNotifOpen(true)}
          className="relative inline-flex h-10 w-10 items-center justify-center rounded-full text-text-secondary hover:bg-bg-muted"
        >
          <Bell className="h-5 w-5" />
          {unreadCount > 0 && (
            <Badge
              variant="destructive"
              className="absolute -right-0.5 -top-0.5 h-4 min-w-[1rem] justify-center px-1 text-[10px]"
            >
              {unreadCount}
            </Badge>
          )}
        </button>
      }
    >
      {isError && (
        <Alert variant="destructive">
          <AlertTitle>取得に失敗しました</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-3 gap-3">
        <StatCard label="今日" value={todayCount} loading={isLoading} />
        <StatCard label="今週" value={weekCount} loading={isLoading} />
        <StatCard label="今月" value={monthCount} loading={isLoading} />
      </div>

      <div className="space-y-2">
        <Link
          href="/m/today"
          className="flex items-center justify-between rounded-lg border border-border-default bg-bg-base p-4 transition-colors hover:bg-bg-muted"
        >
          <div className="flex items-center gap-3">
            <CalendarCheck className="h-5 w-5 text-brand-primary" />
            <div>
              <p className="font-medium text-text-primary">今日の訪問へ</p>
              <p className="text-xs text-text-muted">{todayCount}件の予定</p>
            </div>
          </div>
          <ChevronRight className="h-4 w-4 text-text-muted" />
        </Link>

        <Link
          href="/m/this-week"
          className="flex items-center justify-between rounded-lg border border-border-default bg-bg-base p-4 transition-colors hover:bg-bg-muted"
        >
          <div className="flex items-center gap-3">
            <CalendarRange className="h-5 w-5 text-brand-primary" />
            <div>
              <p className="font-medium text-text-primary">今週の予定へ</p>
              <p className="text-xs text-text-muted">{weekCount}件</p>
            </div>
          </div>
          <ChevronRight className="h-4 w-4 text-text-muted" />
        </Link>
      </div>

      <NotificationListSheet open={notifOpen} onOpenChange={setNotifOpen} />
    </MobileSection>
  );
}
