'use client';

/**
 * 連携センター - kaipoke 操作画面 (Wave 4-A).
 *
 * Replaces the read-only stub with the live control panel:
 *   - Live status (kaipoke /status + DB last job)
 *   - Action buttons: expand / export / diff / apply (admin only)
 *   - Differential preview (CorrectionSheetView)
 *   - Job history (KaipokeJobsList) below
 */
import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';

import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  useIntegrationJobs,
  useKaipokeStatus,
  useStartDiff,
  useStartExpand,
  useStartExport,
  useStopJob,
} from '@/lib/queries/integrations';

import { KaipokeJobsList } from '../_components/KaipokeJobsList';
import { CorrectionSheetView } from './CorrectionSheetView';

function defaultMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

export default function KaipokeIntegrationPage() {
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'admin';

  const [month, setMonth] = useState<string>(defaultMonth());

  const statusQuery = useKaipokeStatus();
  const recentJobs = useIntegrationJobs(10);
  const expand = useStartExpand();
  const exportJob = useStartExport();
  const diff = useStartDiff();
  const stop = useStopJob();

  const runningJob = statusQuery.data?.runningJob ?? null;
  const loginRemain = statusQuery.data?.loginRemainSec ?? null;
  const loginWarn = typeof loginRemain === 'number' && loginRemain < 300;

  const lastError = useMemo(() => {
    return expand.error || exportJob.error || diff.error || stop.error || null;
  }, [expand.error, exportJob.error, diff.error, stop.error]);

  return (
    <section className="space-y-6">
      <header>
        <h1 className="font-serif text-2xl font-bold text-text-primary">連携センター - Kaipoke</h1>
        <p className="text-sm text-text-secondary">
          kaipoke-api (Playwright) を経由したスケジュール展開 / エクスポート / 差分 /
          適用を実行します
        </p>
      </header>

      <Card className="p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-serif text-lg font-bold text-text-primary">稼働状況</h2>
            {statusQuery.isLoading ? (
              <Skeleton className="mt-2 h-5 w-40" />
            ) : statusQuery.isError ? (
              <p className="text-sm text-text-warning">kaipoke-api に到達できません</p>
            ) : (
              <ul className="mt-1 text-sm text-text-secondary">
                <li>到達: {statusQuery.data?.reachable ? 'OK' : 'NG'}</li>
                <li>
                  ログイン残り:{' '}
                  <span className={loginWarn ? 'text-text-warning' : ''}>
                    {loginRemain !== null && loginRemain !== undefined
                      ? `${loginRemain} 秒`
                      : '不明'}
                  </span>
                </li>
                <li>最終同期: {statusQuery.data?.lastSyncAt ?? '--'}</li>
                <li>
                  実行中ジョブ:{' '}
                  {runningJob
                    ? `${runningJob.job_type} / ${runningJob.status} (${runningJob.id.slice(0, 8)})`
                    : 'なし'}
                </li>
              </ul>
            )}
          </div>
          {isAdmin && runningJob && (
            <Button
              variant="destructive"
              onClick={() => stop.mutate(runningJob.id)}
              disabled={stop.isPending}
            >
              現在のジョブを停止
            </Button>
          )}
        </div>
      </Card>

      <Card className="p-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <label className="text-sm">
            <span className="mb-1 block text-xs text-text-secondary">対象月 (YYYY-MM)</span>
            <Input type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
          </label>
          {isAdmin && (
            <div className="flex flex-wrap items-end gap-2">
              <Button
                onClick={() => expand.mutate({ month })}
                disabled={expand.isPending || !month}
              >
                スケジュール展開 (Expand)
              </Button>
              <Button
                variant="outline"
                onClick={() => exportJob.mutate({ month, format: 'csv' })}
                disabled={exportJob.isPending || !month}
              >
                CSV エクスポート
              </Button>
              <Button
                variant="outline"
                onClick={() => diff.mutate({ month })}
                disabled={diff.isPending || !month}
              >
                差分計算
              </Button>
            </div>
          )}
        </div>
        {lastError && (
          <Alert variant="destructive" className="mt-3">
            <AlertTitle>操作に失敗しました</AlertTitle>
            <AlertDescription>
              {lastError instanceof Error ? lastError.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        )}
      </Card>

      <CorrectionSheetView month={month} />

      <Card className="p-4">
        <h2 className="mb-2 font-serif text-lg font-bold text-text-primary">直近ジョブ (10件)</h2>
        {recentJobs.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : recentJobs.isError ? (
          <Alert variant="destructive">
            <AlertTitle>取得に失敗</AlertTitle>
            <AlertDescription>
              {recentJobs.error instanceof Error ? recentJobs.error.message : 'エラー'}
            </AlertDescription>
          </Alert>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-border-default text-left text-text-secondary">
              <tr>
                <th className="px-2 py-1 font-medium">種類</th>
                <th className="px-2 py-1 font-medium">op</th>
                <th className="px-2 py-1 font-medium">状態</th>
                <th className="px-2 py-1 font-medium">作成</th>
              </tr>
            </thead>
            <tbody>
              {recentJobs.data?.items.map((job) => (
                <tr key={job.id} className="border-b border-border-default last:border-0">
                  <td className="px-2 py-1">{job.job_type}</td>
                  <td className="px-2 py-1 text-text-secondary">
                    {((job.params as Record<string, unknown>)?.op as string) ?? '--'}
                  </td>
                  <td className="px-2 py-1">{job.status}</td>
                  <td className="px-2 py-1 text-text-secondary">{job.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <KaipokeJobsList />
    </section>
  );
}
