/**
 * Special-week list (Wave 3-E / W4-E).
 *
 * Filters: patient (Combobox) / status.
 * Roles: admin / manager のみアクセス可。「+ 新規登録」も同条件。
 */
'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useSession } from 'next-auth/react';

import { PatientCombobox } from '@/components/master/PatientCombobox';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useSpecialWeeks } from '@/lib/queries/special-weeks';
import { STATUS_OPTIONS } from '@/lib/schemas/special-week';

export default function SpecialWeeksPage() {
  const { data: session } = useSession();
  const role = session?.user?.role;
  const canAccess = role === 'admin' || role === 'manager';

  const [patientFilter, setPatientFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  const { data, isLoading, isError, error } = useSpecialWeeks({
    patient_id: patientFilter || undefined,
    status: statusFilter || undefined,
  });

  if (!canAccess) {
    return (
      <Alert variant="destructive">
        <AlertTitle>権限がありません</AlertTitle>
        <AlertDescription>
          特別訪問週間の閲覧は管理者またはマネージャーのみ実行できます。
        </AlertDescription>
      </Alert>
    );
  }

  const items = data ?? [];

  return (
    <section className="space-y-4">
      <header className="flex items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-2xl font-bold text-text-primary">
            特別訪問週間
          </h1>
          <p className="text-sm text-text-secondary">
            {items.length > 0 ? `全 ${items.length} 件` : '一時的な訪問パターン変更を管理します'}
          </p>
        </div>
        <Button asChild>
          <Link href="/special-weeks/new">+ 新規登録</Link>
        </Button>
      </header>

      <Card className="p-4">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-[1fr_220px]">
          <PatientCombobox
            value={patientFilter}
            onChange={setPatientFilter}
            placeholder="患者で絞り込み (未指定: 全件)"
          />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="flex h-10 w-full rounded-md border border-border-default bg-bg-base px-3 py-2 text-sm text-text-primary focus-visible:outline-none focus-visible:border-brand-primary focus-visible:ring-2 focus-visible:ring-brand-primary-light"
          >
            <option value="">ステータス: すべて</option>
            {STATUS_OPTIONS.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </div>
      </Card>

      <Card className="p-5">
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
        ) : items.length === 0 ? (
          <p className="text-sm text-text-muted">
            特別訪問週間が登録されていません。
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border-default text-left text-text-secondary">
                <tr>
                  <th className="px-3 py-2 font-medium">患者 ID</th>
                  <th className="px-3 py-2 font-medium">週開始</th>
                  <th className="px-3 py-2 font-medium">週終了</th>
                  <th className="px-3 py-2 font-medium">モード</th>
                  <th className="px-3 py-2 font-medium">ステータス</th>
                  <th className="px-3 py-2 font-medium">明細数</th>
                  <th className="px-3 py-2 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((sw) => (
                  <tr
                    key={sw.id}
                    className="border-b border-border-default last:border-0 hover:bg-bg-muted/30"
                  >
                    <td className="px-3 py-2 font-mono text-text-secondary tnum">
                      {sw.patient_id.slice(0, 8)}…
                    </td>
                    <td className="px-3 py-2 tnum">{sw.week_start}</td>
                    <td className="px-3 py-2 tnum">{sw.week_end}</td>
                    <td className="px-3 py-2 text-text-secondary">
                      {sw.apply_mode}
                    </td>
                    <td className="px-3 py-2 text-text-secondary">{sw.status}</td>
                    <td className="px-3 py-2 text-text-secondary tnum">
                      {sw.items?.length ?? 0}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex gap-2">
                        <Link
                          href={`/special-weeks/${sw.id}`}
                          className="text-brand-primary hover:underline"
                        >
                          詳細
                        </Link>
                        <Link
                          href={`/special-weeks/${sw.id}/edit`}
                          className="text-brand-primary hover:underline"
                        >
                          編集
                        </Link>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </section>
  );
}
