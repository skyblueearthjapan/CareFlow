'use client';

/**
 * ダッシュボード (管理者) のカード「M月の休み：申請中 N 件・足りない人 N 人・カイポケで要対応 N 件」
 * (設計 §5-2)。押すと「休み・勤務表」をその月で開く。
 */
import Link from 'next/link';
import { ArrowRight, CalendarHeart } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { monthNumber, upcomingLeaveMonth } from '@/lib/leave-sheet';
import { useLeaveMonth } from '@/lib/queries/leave';

export function LeaveDashboardCard() {
  const month = upcomingLeaveMonth();
  const q = useLeaveMonth(month);
  const v = q.data;
  const shortCount = v ? v.staff.filter((s) => s.is_full_time && (s.shortfall ?? 0) > 0).length : 0;
  const kaipokeAction = v ? v.cells.filter((c) => c.send_status === 'kaipoke_action').length : 0;

  return (
    <Card
      className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
      data-testid="leave-dashboard-card"
    >
      <div className="flex items-center gap-2 text-sm text-text-primary">
        <CalendarHeart className="h-4 w-4 text-brand-primary" />
        <b>{monthNumber(month)}月の休み：</b>
        {q.isError ? (
          <span className="text-text-muted">読み込めませんでした</span>
        ) : !v ? (
          <Skeleton className="h-4 w-48" />
        ) : (
          <span>
            申請中 <b className="tnum">{v.pending.length}</b> 件・足りない人{' '}
            <b className={shortCount > 0 ? 'tnum text-warning-strong' : 'tnum'}>{shortCount}</b> 人
            {kaipokeAction > 0 && (
              <>
                ・カイポケで要対応 <b className="tnum text-error">{kaipokeAction}</b> 件
              </>
            )}
            {v.confirmation.confirmed && <span className="text-text-muted">（確定済み）</span>}
          </span>
        )}
      </div>
      <Button asChild size="sm" variant="outline">
        <Link href={`/admin/leave?month=${month}`}>
          休み・勤務表で開く
          <ArrowRight className="h-4 w-4" />
        </Link>
      </Button>
    </Card>
  );
}
