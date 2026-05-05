'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { useOffice } from '@/lib/queries/offices';
import { useCities } from '@/lib/queries/cities';

export default function OfficeDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;

  const { data: office, isLoading, isError, error } = useOffice(id);
  const { allCities } = useCities();

  const allowed = (office?.allowed_cities ?? []).map((cityId) => {
    const city = allCities.find((c) => c.id === cityId);
    return city ? `${city.prefecture} / ${city.name}` : cityId;
  });

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="font-serif text-2xl font-bold text-text-primary">
          拠点詳細 {office ? `- ${office.name}` : ''}
        </h1>
        <div className="flex gap-2">
          <Button variant="outline" asChild>
            <Link href="/offices">一覧へ戻る</Link>
          </Button>
          {id && (
            <Button asChild>
              <Link href={`/offices/${id}/edit`}>編集</Link>
            </Button>
          )}
        </div>
      </header>

      <Card className="p-5">
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-2/3" />
            <Skeleton className="h-8 w-1/2" />
            <Skeleton className="h-24 w-full" />
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
          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="拠点名" value={office.name} />
            <Field label="住所" value={office.address ?? '--'} />
            <Field label="緯度" value={office.lat?.toString() ?? '--'} />
            <Field label="経度" value={office.lng?.toString() ?? '--'} />
            <Field
              label="担当エリア (cities)"
              value={allowed.length > 0 ? allowed.join(' / ') : '未設定'}
              wide
            />
            <Field label="メモ" value={office.note ?? '--'} wide />
          </dl>
        )}
      </Card>
    </section>
  );
}

function Field({ label, value, wide }: { label: string; value: string; wide?: boolean }) {
  return (
    <div className={wide ? 'sm:col-span-2' : ''}>
      <dt className="text-xs font-medium text-text-secondary">{label}</dt>
      <dd className="mt-1 text-sm text-text-primary">{value}</dd>
    </div>
  );
}
