'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';

import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  KAIPOKE_JOB_STATUSES,
  KAIPOKE_JOB_TYPES,
  type KaipokeJobCreate,
  type KaipokeJobType,
} from '@/lib/schemas/integration';
import {
  useCreateKaipokeJob,
  useKaipokeJobs,
} from '@/lib/queries/integrations';

export default function KaipokeJobsPage() {
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'admin';

  const [weekStart, setWeekStart] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [typeFilter, setTypeFilter] = useState<string>('all');

  const { data: jobs, isLoading, isError, error } = useKaipokeJobs({
    weekStart: weekStart || undefined,
    status: statusFilter === 'all' ? undefined : statusFilter,
    type: typeFilter === 'all' ? undefined : typeFilter,
  });

  const create = useCreateKaipokeJob();

  const handleCreate = async (jobType: KaipokeJobType) => {
    const target = weekStart || new Date().toISOString().slice(0, 10);
    const payload: KaipokeJobCreate = {
      job_type: jobType,
      week_start: target,
      params: {},
    };
    await create.mutateAsync(payload);
  };

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-serif text-xl font-bold text-text-primary">Kaipoke ジョブ</h2>
          <p className="text-sm text-text-secondary">
            Playwright による fetch / push の実行ログ (Phase 5-2 で実行ロジック実装予定)
          </p>
        </div>
        {isAdmin && (
          <div className="flex gap-2">
            <Button onClick={() => handleCreate('fetch')} disabled={create.isPending}>
              fetch ジョブ登録
            </Button>
            <Button
              variant="outline"
              onClick={() => handleCreate('push')}
              disabled={create.isPending}
            >
              push ジョブ登録
            </Button>
          </div>
        )}
      </header>

      <Card className="p-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="text-sm">
            <span className="mb-1 block text-xs text-text-secondary">週開始日 (YYYY-MM-DD)</span>
            <Input
              type="date"
              value={weekStart}
              onChange={(e) => setWeekStart(e.target.value)}
            />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-text-secondary">ステータス</span>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">すべて</SelectItem>
                {KAIPOKE_JOB_STATUSES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-text-secondary">種類</span>
            <Select value={typeFilter} onValueChange={setTypeFilter}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">すべて</SelectItem>
                {KAIPOKE_JOB_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
        </div>
      </Card>

      <Card className="p-4">
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-3/4" />
          </div>
        ) : isError ? (
          <Alert variant="destructive">
            <AlertTitle>取得に失敗しました</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        ) : !jobs || jobs.length === 0 ? (
          <p className="text-sm text-text-muted">該当するジョブはありません</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border-default text-left text-text-secondary">
                <tr>
                  <th className="px-3 py-2 font-medium">種類</th>
                  <th className="px-3 py-2 font-medium">週</th>
                  <th className="px-3 py-2 font-medium">状態</th>
                  <th className="px-3 py-2 font-medium">開始</th>
                  <th className="px-3 py-2 font-medium">完了</th>
                  <th className="px-3 py-2 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id} className="border-b border-border-default last:border-0">
                    <td className="px-3 py-2">{job.job_type}</td>
                    <td className="px-3 py-2 tnum">{job.week_start}</td>
                    <td className="px-3 py-2">{job.status}</td>
                    <td className="px-3 py-2 text-text-secondary">{job.started_at ?? '--'}</td>
                    <td className="px-3 py-2 text-text-secondary">{job.completed_at ?? '--'}</td>
                    <td className="px-3 py-2">
                      <Link
                        href={`/integrations/kaipoke/${job.id}`}
                        className="text-brand-primary hover:underline"
                      >
                        詳細
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {create.isError && (
        <Alert variant="destructive">
          <AlertTitle>登録に失敗しました</AlertTitle>
          <AlertDescription>
            {create.error instanceof Error ? create.error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      )}
    </section>
  );
}
