'use client';

import { useQuery } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { fetcher } from '@/lib/api/fetcher';

interface StaffRow {
  id?: string | number;
  name?: string;
  role?: string;
  [key: string]: unknown;
}

type StaffResponse = StaffRow[] | { items?: StaffRow[] };

function normalizeStaff(data: StaffResponse | undefined): StaffRow[] {
  if (!data) return [];
  if (Array.isArray(data)) return data;
  return Array.isArray(data.items) ? data.items : [];
}

export default function StaffPage() {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const userId = session?.user?.id ?? null;

  const { data, isLoading, isError, error } = useQuery<StaffResponse>({
    queryKey: ['staff', userId],
    queryFn: () => fetcher<StaffResponse>('/api/v1/staff', { accessToken, refreshToken }),
    enabled: status === 'authenticated',
  });

  const rows = normalizeStaff(data);

  return (
    <section className="space-y-4">
      <header>
        <h1 className="font-serif text-2xl font-bold text-text-primary">スタッフマスタ</h1>
        <p className="text-sm text-text-secondary">スタッフ一覧 — D3 で実装予定</p>
      </header>

      <Card className="p-5">
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-2/3" />
          </div>
        ) : isError ? (
          <Alert variant="destructive">
            <AlertTitle>取得に失敗しました</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        ) : rows.length === 0 ? (
          <p className="text-sm text-text-muted">データなし</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border-default text-left text-text-secondary">
                <tr>
                  <th className="px-3 py-2 font-medium">ID</th>
                  <th className="px-3 py-2 font-medium">氏名</th>
                  <th className="px-3 py-2 font-medium">役割</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, idx) => (
                  <tr key={row.id ?? idx} className="border-b border-border-default last:border-0">
                    <td className="px-3 py-2 tnum">{String(row.id ?? '--')}</td>
                    <td className="px-3 py-2">{row.name ?? '--'}</td>
                    <td className="px-3 py-2 text-text-secondary">{row.role ?? '--'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {/* TODO: fetch from BACKEND_API_BASE_URL /api/v1/staff */}
    </section>
  );
}
