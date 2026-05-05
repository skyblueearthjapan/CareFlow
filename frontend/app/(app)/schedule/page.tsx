'use client';

import { useQuery } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { fetcher } from '@/lib/api/fetcher';

interface VisitRow {
  id?: string | number;
  // Backend `VisitRead` (snake_case) — see backend/app/schemas/visit.py.
  visit_date?: string;
  patient_name?: string | null;
  staff_name?: string | null;
  [key: string]: unknown;
}

type VisitsResponse = VisitRow[] | { items?: VisitRow[] };

function normalizeVisits(data: VisitsResponse | undefined): VisitRow[] {
  if (!data) return [];
  if (Array.isArray(data)) return data;
  return Array.isArray(data.items) ? data.items : [];
}

export default function SchedulePage() {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const userId = session?.user?.id ?? null;

  const { data, isLoading, isError, error } = useQuery<VisitsResponse>({
    queryKey: ['visits', userId],
    queryFn: () => fetcher<VisitsResponse>('/api/v1/visits', { accessToken, refreshToken }),
    enabled: status === 'authenticated',
  });

  const rows = normalizeVisits(data);

  return (
    <section className="space-y-4">
      <header>
        <h1 className="font-serif text-2xl font-bold text-text-primary">スケジュール</h1>
        <p className="text-sm text-text-secondary">週ビュー — D3 で実装予定</p>
      </header>

      <Card className="p-5">
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-1/2" />
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
                  <th className="px-3 py-2 font-medium">日付</th>
                  <th className="px-3 py-2 font-medium">患者</th>
                  <th className="px-3 py-2 font-medium">担当</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, idx) => (
                  <tr key={row.id ?? idx} className="border-b border-border-default last:border-0">
                    <td className="px-3 py-2 tnum">{row.visit_date ?? '--'}</td>
                    <td className="px-3 py-2">{row.patient_name ?? '--'}</td>
                    <td className="px-3 py-2 text-text-secondary">{row.staff_name ?? '--'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {/* TODO: fetch from BACKEND_API_BASE_URL /api/v1/visits (or /api/v1/schedule/weekly) */}
    </section>
  );
}
