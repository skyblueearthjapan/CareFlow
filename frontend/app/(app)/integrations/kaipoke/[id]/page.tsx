'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useSession } from 'next-auth/react';

import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { SyncReportButton } from '@/components/integrations/SyncReportButton';
import { isReportableJob } from '@/lib/kaipokeOps';
import { useCancelKaipokeJob, useKaipokeJob } from '@/lib/queries/integrations';

export default function KaipokeJobDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'admin';

  const { data: job, isLoading, isError, error } = useKaipokeJob(id);
  const cancel = useCancelKaipokeJob();

  const canCancel = job && (job.status === 'pending' || job.status === 'running');

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="font-serif text-2xl font-bold text-text-primary">
          Kaipoke ジョブ詳細 {job ? `- ${job.job_type} / ${job.week_start}` : ''}
        </h1>
        <div className="flex gap-2">
          <Button variant="outline" asChild>
            <Link href="/integrations/kaipoke">一覧へ戻る</Link>
          </Button>
          {/* 完了した実書込ジョブは A4 の結果報告書を開ける */}
          {job && isReportableJob(job) && <SyncReportButton jobId={job.id} size="md" />}
          {isAdmin && id && canCancel && (
            <Button
              variant="destructive"
              onClick={() => cancel.mutate(id)}
              disabled={cancel.isPending}
            >
              キャンセル
            </Button>
          )}
        </div>
      </header>

      <Card className="p-5">
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-2/3" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : isError ? (
          <Alert variant="destructive">
            <AlertTitle>取得に失敗しました</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        ) : !job ? (
          <p className="text-sm text-text-muted">データが見つかりません</p>
        ) : (
          <div className="space-y-4">
            <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="種類" value={job.job_type} />
              <Field label="状態" value={job.status} />
              <Field label="週開始" value={job.week_start} />
              <Field label="作成日時" value={job.created_at} />
              <Field label="開始" value={job.started_at ?? '--'} />
              <Field label="完了" value={job.completed_at ?? '--'} />
              <Field label="パラメータ" value={JSON.stringify(job.params, null, 2)} wide pre />
              <Field
                label="結果サマリ"
                value={job.result_summary ? JSON.stringify(job.result_summary, null, 2) : '--'}
                wide
                pre
              />
            </dl>

            <section>
              <h2 className="mb-2 font-serif text-lg font-bold text-text-primary">
                items ({job.items.length})
              </h2>
              {job.items.length === 0 ? (
                <p className="text-sm text-text-muted">items はまだありません</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="border-b border-border-default text-left text-text-secondary">
                      <tr>
                        <th className="px-3 py-2 font-medium">seq</th>
                        <th className="px-3 py-2 font-medium">状態</th>
                        <th className="px-3 py-2 font-medium">content</th>
                        <th className="px-3 py-2 font-medium">error</th>
                      </tr>
                    </thead>
                    <tbody>
                      {job.items.map((item) => (
                        <tr key={item.id} className="border-b border-border-default last:border-0">
                          <td className="px-3 py-2 tnum">{item.seq}</td>
                          <td className="px-3 py-2">{item.status}</td>
                          <td className="px-3 py-2">
                            <pre className="max-w-xl overflow-x-auto whitespace-pre-wrap text-xs text-text-secondary">
                              {JSON.stringify(item.content, null, 2)}
                            </pre>
                          </td>
                          <td className="px-3 py-2 text-text-secondary">
                            {item.error_msg ?? '--'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </div>
        )}

        {cancel.isError && (
          <Alert variant="destructive" className="mt-4">
            <AlertTitle>キャンセルに失敗しました</AlertTitle>
            <AlertDescription>
              {cancel.error instanceof Error ? cancel.error.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        )}
      </Card>
    </section>
  );
}

function Field({
  label,
  value,
  wide,
  pre,
}: {
  label: string;
  value: string;
  wide?: boolean;
  pre?: boolean;
}) {
  return (
    <div className={wide ? 'sm:col-span-2' : ''}>
      <dt className="text-xs font-medium text-text-secondary">{label}</dt>
      <dd className="mt-1 text-sm text-text-primary">
        {pre ? (
          <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-bg-muted p-2 text-xs">
            {value}
          </pre>
        ) : (
          value
        )}
      </dd>
    </div>
  );
}
