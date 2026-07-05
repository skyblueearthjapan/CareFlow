'use client';

/**
 * カイポケ連携 — CareFlow の週次スケジュールをカイポケへ反映する統合画面。
 *
 * 構成:
 *   - 稼働状況 + ライブモニター (noVNC 埋め込み)
 *   - 週次反映ワークフロー (①展開 →②差分 →③確認(コース別週ビュー) →④反映 を集約)
 *   - ライブ進捗 / 実行ログ / 直近結果 / ジョブ履歴
 */
import Link from 'next/link';
import { useSession } from 'next-auth/react';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { useKaipokeLive, useStopJob } from '@/lib/queries/integrations';

import { KaipokeJobsList } from '../_components/KaipokeJobsList';
import { EmergencyStopButton } from './_components/EmergencyStopButton';
import { ExecutionLogViewer } from './_components/ExecutionLogViewer';
import { JobProgressCard, commandLabel } from './_components/JobProgressCard';
import { JobResultCard } from './_components/JobResultCard';
import { LiveMonitorCard } from './_components/LiveMonitorCard';
import { LiveStatusDot } from './_components/LiveStatusDot';
import { WeeklyApplyPanel } from './_components/WeeklyApplyPanel';

export default function KaipokeIntegrationPage() {
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'admin';

  const liveQuery = useKaipokeLive();
  const live = liveQuery.data;
  const stop = useStopJob();

  const running = Boolean(live?.running);
  const reachable = live?.reachable ?? true;
  const latestJob = live?.latestJob ?? null;
  const finishedJob =
    latestJob && !running && ['completed', 'failed', 'cancelled'].includes(latestJob.status)
      ? latestJob
      : null;

  if (!isAdmin) {
    return (
      <section className="space-y-6">
        <Header />
        <Alert>
          <AlertTitle>管理者専用</AlertTitle>
          <AlertDescription>カイポケ連携の操作は管理者のみ利用できます。</AlertDescription>
        </Alert>
      </section>
    );
  }

  const statusTone = !reachable ? 'error' : running ? 'running' : 'idle';

  return (
    <section className="space-y-6">
      <Header />

      {/* 稼働状況 + ライブモニター */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-serif text-lg font-bold text-text-primary">稼働状況</h2>
            <LiveStatusDot
              tone={statusTone}
              label={!reachable ? '到達不可' : running ? '実行中' : '待機中'}
            />
          </div>
          {liveQuery.isLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : !reachable ? (
            <Alert variant="destructive">
              <AlertTitle>kaipoke-api に到達できません</AlertTitle>
              <AlertDescription className="break-all">
                {live?.error ?? '接続を確認してください'}
              </AlertDescription>
            </Alert>
          ) : (
            <dl className="grid grid-cols-2 gap-y-2 text-sm">
              <dt className="text-text-secondary">現在の状態</dt>
              <dd className="text-text-primary">
                {running ? (commandLabel(live?.command) ?? '実行中') : '待機中'}
              </dd>
              <dt className="text-text-secondary">直近ジョブ</dt>
              <dd className="text-text-primary">
                {latestJob ? `${latestJob.job_type} / ${latestJob.status}` : 'なし'}
              </dd>
            </dl>
          )}
        </Card>

        <LiveMonitorCard
          monitorUrl={live?.monitorUrl}
          running={running}
          reachable={reachable}
          commandLabel={commandLabel(live?.command)}
        />
      </div>

      {/* ライブ進捗ゲージ（実行中のみ）— モニターの直下に置き、週ビューが開いても
          スクロールせずモニターと一緒に見える位置に保つ */}
      {running && live && <JobProgressCard live={live} />}

      {/* 実行中ジョブの非常停止 */}
      {running && latestJob && (
        <div className="flex items-center justify-between rounded-lg border border-border-warning bg-warning-bg px-4 py-3">
          <p className="text-sm text-warning-strong">
            ジョブが実行中です。必要な場合は安全に停止できます。
          </p>
          <EmergencyStopButton
            pending={stop.isPending}
            onConfirm={() => stop.mutate(latestJob.id)}
          />
        </div>
      )}

      {/* 週次反映ワークフロー（①展開 →②差分 →③確認 →④反映 を集約） */}
      <WeeklyApplyPanel busy={running} />

      {/* 実行ログ */}
      {live && live.logs.length > 0 && <ExecutionLogViewer lines={live.logs} />}

      {/* 直近の実行結果（完了時） */}
      {finishedJob && <JobResultCard job={finishedJob} />}

      {/* ジョブ履歴 */}
      <KaipokeJobsList />

      {/* 管理ユーティリティへの導線 */}
      <p className="text-xs text-text-muted">
        Geocoding キャッシュは{' '}
        <Link className="text-brand-primary hover:underline" href="/integrations">
          連携ユーティリティ
        </Link>{' '}
        から。
      </p>
    </section>
  );
}

function Header() {
  return (
    <header>
      <h1 className="font-serif text-2xl font-bold text-text-primary">カイポケ連携</h1>
      <p className="mt-1 text-sm text-text-secondary">
        カイポケの実ブラウザをライブで見守りながら、CareFlow
        の週次スケジュールをカイポケへ反映します。
      </p>
    </header>
  );
}
