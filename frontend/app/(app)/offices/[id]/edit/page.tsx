'use client';

import { useParams, useRouter } from 'next/navigation';

import { OfficeForm } from '../../_components/OfficeForm';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { useOffice, useUpdateOffice } from '@/lib/queries/offices';
import type { OfficeUpdate } from '@/lib/schemas/office';

export default function EditOfficePage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? '';
  const router = useRouter();

  const { data: office, isLoading, isError, error } = useOffice(id);
  const update = useUpdateOffice(id);

  const handleSubmit = async (values: OfficeUpdate) => {
    await update.mutateAsync(values);
    router.push(`/offices/${id}`);
  };

  return (
    <section className="space-y-4">
      <header>
        <h1 className="font-serif text-2xl font-bold text-text-primary">
          拠点を編集 {office ? `- ${office.name}` : ''}
        </h1>
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
