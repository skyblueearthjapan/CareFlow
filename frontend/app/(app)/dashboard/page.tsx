'use client';

import { useQuery } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { fetcher } from '@/lib/api/fetcher';

interface HealthResponse {
  status?: string;
  [key: string]: unknown;
}

export default function DashboardPage() {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  // healthz is user-agnostic — no need to scope the cache key by user.id.
  const { data, isLoading, isError, error } = useQuery<HealthResponse>({
    queryKey: ['healthz'],
    queryFn: () => fetcher<HealthResponse>('/api/v1/healthz', { accessToken, refreshToken }),
    enabled: status === 'authenticated',
  });

  return (
    <section className="space-y-6">
      <header>
        <h1 className="font-serif text-2xl font-bold text-text-primary">ダッシュボード</h1>
        <p className="mt-1 text-sm text-text-secondary">本日の概要 — D3 で実装予定</p>
      </header>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <Card className="p-5">
          <p className="text-xs text-text-muted">本日の訪問</p>
          <p className="mt-2 font-serif text-3xl font-bold tnum">--</p>
        </Card>
        <Card className="p-5">
          <p className="text-xs text-text-muted">未割当</p>
          <p className="mt-2 font-serif text-3xl font-bold tnum">--</p>
        </Card>
        <Card className="p-5">
          <p className="text-xs text-text-muted">アラート</p>
          <p className="mt-2 font-serif text-3xl font-bold tnum">--</p>
        </Card>
      </div>

      <Card className="p-5">
        <h2 className="font-serif text-lg font-bold text-text-primary">バックエンド疎通</h2>
        <p className="mt-1 text-xs text-text-muted">
          GET /api/v1/healthz — TanStack Query 動作確認用
        </p>
        <div className="mt-4">
          {isLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : isError ? (
            <Alert variant="destructive">
              <AlertTitle>取得に失敗しました</AlertTitle>
              <AlertDescription>
                {error instanceof Error ? error.message : '不明なエラー'}
              </AlertDescription>
            </Alert>
          ) : (
            <pre className="overflow-x-auto rounded-md bg-bg-muted p-3 text-xs text-text-primary">
              {JSON.stringify(data ?? {}, null, 2)}
            </pre>
          )}
        </div>
      </Card>
      {/* TODO: fetch from BACKEND_API_BASE_URL /api/v1/dashboard */}
    </section>
  );
}
