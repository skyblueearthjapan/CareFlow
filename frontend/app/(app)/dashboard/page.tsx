'use client';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { EmptyState } from '@/components/dashboard/EmptyState';
import { KpiCard } from '@/components/dashboard/KpiCard';
import { TrendChart } from '@/components/dashboard/TrendChart';
import { useDashboardKpi, useDashboardTrend } from '@/lib/queries/dashboard';

function formatPercent(rate: number): string {
  return `${Math.round(rate * 1000) / 10}%`;
}

export default function DashboardPage() {
  const kpi = useDashboardKpi();
  const trend = useDashboardTrend(7);

  const kpiLoading = kpi.isLoading;
  const trendItems = trend.data?.items ?? [];
  const trendHasData = trendItems.some((d) => d.total > 0);

  return (
    <section className="space-y-6">
      <header>
        <h1 className="font-serif text-2xl font-bold text-text-primary">ダッシュボード</h1>
        <p className="mt-1 text-sm text-text-secondary">
          本日の概要と直近 7 日間の訪問トレンド
        </p>
      </header>

      {kpi.isError ? (
        <Alert variant="destructive">
          <AlertTitle>KPI の取得に失敗しました</AlertTitle>
          <AlertDescription>
            {kpi.error instanceof Error ? kpi.error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <KpiCard
          label="今日の訪問"
          value={kpi.data?.today_visits ?? 0}
          unit="件"
          isLoading={kpiLoading}
        />
        <KpiCard
          label="今日の完了"
          value={kpi.data?.today_completed ?? 0}
          unit="件"
          isLoading={kpiLoading}
          caption={
            kpi.data && kpi.data.today_visits > 0
              ? `完了率 ${formatPercent(kpi.data.today_completed / kpi.data.today_visits)}`
              : undefined
          }
        />
        <KpiCard
          label="未割当"
          value={kpi.data?.today_unassigned ?? 0}
          unit="件"
          isLoading={kpiLoading}
          deltaTone={kpi.data && kpi.data.today_unassigned > 0 ? 'down' : 'neutral'}
        />
        <KpiCard
          label="重複"
          value={kpi.data?.today_overlapping ?? 0}
          unit="件"
          isLoading={kpiLoading}
          deltaTone={kpi.data && kpi.data.today_overlapping > 0 ? 'down' : 'neutral'}
        />
        <KpiCard
          label="今週合計"
          value={kpi.data?.this_week_visits ?? 0}
          unit="件"
          isLoading={kpiLoading}
        />
        <KpiCard
          label="今週完了率"
          value={kpi.data ? formatPercent(kpi.data.this_week_completion_rate) : '--'}
          isLoading={kpiLoading}
        />
      </div>

      <Card className="p-5">
        <div className="flex items-baseline justify-between">
          <h2 className="font-serif text-lg font-bold text-text-primary">
            訪問トレンド (直近 7 日)
          </h2>
          {trend.data ? (
            <p className="text-xs text-text-muted tnum">
              {trend.data.start_date} ～ {trend.data.end_date}
            </p>
          ) : null}
        </div>

        <div className="mt-4">
          {trend.isLoading ? (
            <Skeleton className="h-[260px] w-full" />
          ) : trend.isError ? (
            <Alert variant="destructive">
              <AlertTitle>トレンドの取得に失敗しました</AlertTitle>
              <AlertDescription>
                {trend.error instanceof Error ? trend.error.message : '不明なエラー'}
              </AlertDescription>
            </Alert>
          ) : trendHasData ? (
            <TrendChart data={trendItems} />
          ) : (
            <EmptyState
              title="表示できるデータがありません"
              description="直近 7 日間に登録された訪問はまだありません。"
            />
          )}
        </div>
      </Card>
    </section>
  );
}
