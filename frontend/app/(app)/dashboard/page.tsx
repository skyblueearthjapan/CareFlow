'use client';

import { useSession } from 'next-auth/react';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { RakusukeTitle } from '@/components/brand/Rakusuke';
import { EmptyState } from '@/components/dashboard/EmptyState';
import { KpiCard } from '@/components/dashboard/KpiCard';
import { TrendChart } from '@/components/dashboard/TrendChart';
import { StaffPerformanceSection } from '@/components/dashboard/performance/StaffPerformanceSection';
import { useDashboardKpi, useDashboardTrend, type DashboardKpi } from '@/lib/queries/dashboard';
import { isAdminRole } from '@/lib/rbac';

function formatPercent(rate: number): string {
  return `${Math.round(rate * 1000) / 10}%`;
}

/**
 * 管理者向け: 今日の運用の数字を小さく残す (設計 dashboard-staff-performance §5-6)。
 */
function OpsSummary({ kpi, isLoading }: { kpi: DashboardKpi | undefined; isLoading: boolean }) {
  const items: { label: string; value: string; warn?: boolean }[] = [
    { label: '今日の訪問', value: `${kpi?.today_visits ?? 0} 件` },
    { label: '今日の完了', value: `${kpi?.today_completed ?? 0} 件` },
    {
      label: '未割当',
      value: `${kpi?.today_unassigned ?? 0} 件`,
      warn: (kpi?.today_unassigned ?? 0) > 0,
    },
    {
      label: '重複',
      value: `${kpi?.today_overlapping ?? 0} 件`,
      warn: (kpi?.today_overlapping ?? 0) > 0,
    },
    { label: '今週合計', value: `${kpi?.this_week_visits ?? 0} 件` },
    {
      label: '今週完了率',
      value: kpi ? formatPercent(kpi.this_week_completion_rate) : '--',
    },
  ];
  return (
    <Card className="px-4 py-3" data-testid="ops-summary">
      <h2 className="mb-2 text-[13px] font-bold text-text-secondary">今日の運用</h2>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3 lg:grid-cols-6">
        {items.map((it) => (
          <div key={it.label} className="flex items-baseline justify-between gap-2">
            <dt className="text-xs text-text-muted">{it.label}</dt>
            <dd
              className={
                it.warn
                  ? 'text-sm font-bold tnum text-error'
                  : 'text-sm font-bold tnum text-text-primary'
              }
            >
              {isLoading ? <Skeleton className="h-4 w-10" /> : it.value}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

export default function DashboardPage() {
  const { data: session, status } = useSession();
  const isAdmin = isAdminRole(session?.user?.role);
  const sessionLoading = status === 'loading';
  const kpi = useDashboardKpi();
  // トレンドは staff の画面だけで使う (管理者の画面では呼ばない)。
  const trend = useDashboardTrend(7, { enabled: !sessionLoading && !isAdmin });

  const kpiLoading = kpi.isLoading;
  const trendItems = trend.data?.items ?? [];
  const trendHasData = trendItems.some((d) => d.total > 0);

  const kpiError = kpi.isError ? (
    <Alert variant="destructive">
      <AlertTitle>KPI の取得に失敗しました</AlertTitle>
      <AlertDescription>
        {kpi.error instanceof Error ? kpi.error.message : '不明なエラー'}
      </AlertDescription>
    </Alert>
  ) : null;

  // 権限が分かるまでは枠だけ出す (staff の画面が一瞬出てから切り替わるのを防ぐ)。
  if (sessionLoading) {
    return (
      <section className="space-y-5" aria-busy="true" data-testid="dashboard-loading">
        <header>
          <RakusukeTitle pose="wave" title="ダッシュボード" />
        </header>
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-72 w-full" />
      </section>
    );
  }

  if (isAdmin) {
    return (
      <section className="space-y-5">
        <header>
          <RakusukeTitle
            pose="wave"
            title="ダッシュボード"
            subtitle="スタッフごとの訪問の実績を、件数・時間・移動で見ます。"
          />
        </header>
        {kpiError}
        <StaffPerformanceSection
          opsSummary={<OpsSummary kpi={kpi.data} isLoading={kpiLoading} />}
        />
      </section>
    );
  }

  return (
    <section className="space-y-6">
      <header>
        <RakusukeTitle
          pose="wave"
          title="ダッシュボード"
          subtitle="本日の概要と直近 7 日間の訪問トレンド"
        />
      </header>

      {kpiError}

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
              pose="calendar"
              title="表示できるデータがありません"
              description="直近 7 日間に登録された訪問はまだありません。"
            />
          )}
        </div>
      </Card>
    </section>
  );
}
