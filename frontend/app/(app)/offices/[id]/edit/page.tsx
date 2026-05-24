'use client';

import { useParams, useRouter } from 'next/navigation';
import { useMemo } from 'react';

import { OfficeForm } from '../../_components/OfficeForm';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { RecordNavigator, type NavigatorRecord } from '@/components/RecordNavigator';
import { useOffice, useOffices, useUpdateOffice } from '@/lib/queries/offices';
import type { OfficeUpdate } from '@/lib/schemas/office';

export default function EditOfficePage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? '';
  const router = useRouter();

  const { data: office, isLoading, isError, error } = useOffice(id);
  const update = useUpdateOffice(id);
  const officesQuery = useOffices({ limit: 500 });

  const navRecords = useMemo<NavigatorRecord[]>(
    () =>
      (officesQuery.allOffices ?? []).map((o) => ({
        id: o.id,
        code: o.code ?? null,
        name: o.name,
        kana: undefined,
      })),
    [officesQuery.allOffices],
  );

  const handleSubmit = async (values: OfficeUpdate) => {
    await update.mutateAsync(values);
    router.push(`/offices/${id}`);
  };

  return (
    <section className="space-y-4">
      {/* grid 3 列 (1fr - auto - 1fr) で RecordNavigator を viewport 中央に固定. */}
      <header className="grid grid-cols-1 items-end gap-3 lg:grid-cols-[1fr_auto_1fr]">
        <div>
          <h1 className="font-serif text-2xl font-bold text-text-primary">
            拠点を編集 {office ? `- ${office.name}` : ''}
          </h1>
        </div>
        <div className="flex justify-center">
          {id && navRecords.length > 0 && (
            <RecordNavigator
              currentId={id}
              records={navRecords}
              hrefTemplate="/offices/{id}/edit"
              entityLabel="拠点"
              truncated={navRecords.length >= 500}
            />
          )}
        </div>
        <div />
      </header>

      <Card className="p-5">
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-1/2" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : isError ? (
          <Alert variant="destructive">
            <AlertTitle>取得に失敗しました</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        ) : !office ? (
          <p className="text-sm text-text-muted">データが見つかりません</p>
        ) : (
          <OfficeForm
            initial={office}
            onSubmit={handleSubmit}
            submitting={update.isPending}
            error={update.error}
            submitLabel="更新"
          />
        )}
      </Card>
    </section>
  );
}
