'use client';

/**
 * 連携センター — カイポケ ジョブセンター (K-3).
 *
 * カイポケ RPA (Playwright) をモニタリングしながら CareFlow の UI から
 * 操作する統合画面。旧 GAS サイドバーの上位互換:
 *   - 稼働状況 + ライブモニター (noVNC)
 *   - 操作メニュー (展開 / エクスポート / 差分) + 非常停止
 *   - 実行中ジョブのライブ進捗 + 実行ログ
 *   - 直近の実行結果 (成功/失敗/スキップ) + 差分プレビュー + ジョブ履歴
 */
import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  useKaipokeLive,
  useStartDiff,
  useStartExpand,
  useStartExport,
  useStopJob,
} from '@/lib/queries/integrations';

import { KaipokeJobsList } from '../_components/KaipokeJobsList';
import { CorrectionSheetView } from './CorrectionSheetView';
import { EmergencyStopButton } from './_components/EmergencyStopButton';
import { ExecutionLogViewer } from './_components/ExecutionLogViewer';
import { JobProgressCard, commandLabel } from './_components/JobProgressCard';
import { JobResultCard } from './_components/JobResultCard';
import { LiveMonitorCard } from './_components/LiveMonitorCard';
import { LiveStatusDot } from './_components/LiveStatusDot';
import { OperationMenuCard } from './_components/OperationMenuCard';

function defaultMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

export default function KaipokeIntegrationPage() {
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'admin';

  const [month, setMonth] = useState<string>(defaultMonth());

  const liveQuery = useKaipokeLive();
  const live = liveQuery.data;
  const expand = useStartExpand();
  const exportJob = useStartExport();
  const diff = useStartDiff();
  const stop = useStopJob();

  const running = Boolean(live?.running);
  const reachable = live?.reachable ?? true;
  const latestJob = live?.latestJob ?? null;
  const finishedJob =
    latestJob && !running && ['completed', 'failed', 'cancelled'].includes(latestJob.status)
      ? latestJob
      : null;

  const busy = running || expand.isPending || exportJob.isPending || diff.isPending;

  const lastError = useMemo(
    () => expand.error || exportJob.error || diff.error || stop.error || null,
    [expand.error, exportJob.error, diff.error, stop.error],
  );

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

      {/* 操作メニュー + 非常停止 */}
      <div className="space-y-3">
        <OperationMenuCard
          month={month}
          onMonthChange={setMonth}
          busy={busy}
          onExpand={() => expand.mutate({ month })}
          onExport={() => exportJob.mutate({ month, format: 'csv' })}
          onDiff={() => diff.mutate({ month })}
        />
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
        {lastError && (
          <Alert variant="destructive">
            <AlertTitle>操作に失敗しました</AlertTitle>
            <AlertDescription>
              {lastError instanceof Error ? lastError.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        )}
      </div>

      {/* ライブ進捗（実行中のみ） */}
      {running && live && <JobProgressCard live={live} />}

      {/* 実行ログ */}
      {live && live.logs.length > 0 && <ExecutionLogViewer lines={live.logs} />}

      {/* 直近の実行結果（完了時） */}
      {finishedJob && <JobResultCard job={finishedJob} />}

      {/* 差分プレビュー */}
      <CorrectionSheetView month={month} />

      {/* ジョブ履歴 */}
      <KaipokeJobsList />
    </section>
  );
}

function Header() {
  return (
    <header>
      <h1 className="font-serif text-2xl font-bold text-text-primary">
        連携センター — カイポケ ジョブセンター
      </h1>
      <p className="mt-1 text-sm text-text-secondary">
        カイポケの実ブラウザをライブで見守りながら、スケジュールの展開・エクスポート・差分適用を
        CareFlow から実行します。
      </p>
    </header>
  );
}
