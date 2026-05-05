'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { Bell, ChevronRight, CalendarCheck, CalendarRange } from 'lucide-react';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { toast } from '@/components/ui/sonner';
import { MobileSection } from '@/components/mobile/MobileSection';
import {
  currentMonthStartIso,
  currentWeekStartIso,
  nextMonthStartIso,
  todayIso,
  useMyVisits,
} from '@/lib/queries/me';

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
        <p className="mt-2 font-serif text-3xl font-bold tnum text-text-primary">
          {value}
        </p>
      )}
    </Card>
  );
}

export default function MobileHomePage() {
  const { data: session } = useSession();
  const userName = session?.user?.name ?? 'スタッフ';

  // Pull all my visits in one shot (capped at 500 by the fetcher) and slice
  // client-side for today / this-week / this-month counts. This keeps the API
  // surface tiny while we wait for backend aggregation endpoints.
  const { data: allVisits, isLoading, isError, error } = useMyVisits();

  const today = todayIso();
  const weekStart = currentWeekStartIso();
  const weekEnd = (() => {
    const d = new Date(`${weekStart}T00:00:00`);
    d.setDate(d.getDate() + 7);
    return d.toISOString().slice(0, 10);
  })();
  const monthStart = currentMonthStartIso();
  const monthEnd = nextMonthStartIso();

  const todayCount = allVisits?.filter((v) => v.visit_date === today).length ?? 0;
  const weekCount =
    allVisits?.filter((v) => v.visit_date >= weekStart && v.visit_date < weekEnd)
      .length ?? 0;
  const monthCount =
    allVisits?.filter(
      (v) => v.visit_date >= monthStart && v.visit_date < monthEnd,
    ).length ?? 0;

  // Notification badge — backend has no notifications endpoint yet.
  // TODO(W2-D): wire to /api/v1/notifications once the route lands.
  const unreadCount = 0;

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
          aria-label="通知 (準備中)"
          aria-disabled="true"
          onClick={() => {
            // Notification route ships with W2-D; until then make the dead-end
            // tap explicit instead of leaving the user staring at silence.
            toast.info('通知機能は準備中です');
          }}
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
              <p className="text-xs text-text-muted">
                {todayCount}件の予定
              </p>
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
    </MobileSection>
  );
}
