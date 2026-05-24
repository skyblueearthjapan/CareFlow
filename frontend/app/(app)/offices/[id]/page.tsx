'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useMemo } from 'react';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { RecordNavigator, type NavigatorRecord } from '@/components/RecordNavigator';
import { useOffice, useOffices } from '@/lib/queries/offices';
import { useCities } from '@/lib/queries/cities';

export default function OfficeDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;

  const { data: office, isLoading, isError, error } = useOffice(id);
  const { allCities } = useCities();
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

  const allowed = (office?.allowed_cities ?? []).map((cityId) => {
    const city = allCities.find((c) => c.id === cityId);
    return city ? `${city.prefecture} / ${city.name}` : cityId;
  });

  // Phase G-45: 稼働曜日表示 (太字=稼働, opacity-50=休業).
  const operatingSet = new Set<number>(office?.operating_weekdays ?? []);
  const weekdayCells = [
    { wd: 0, label: '月' },
    { wd: 1, label: '火' },
    { wd: 2, label: '水' },
    { wd: 3, label: '木' },
    { wd: 4, label: '金' },
    { wd: 5, label: '土' },
    { wd: 6, label: '日' },
  ];

  return (
    <section className="space-y-4">
      {/* grid 3 列 (1fr - auto - 1fr) で RecordNavigator を viewport 中央に固定. */}
      <header className="grid grid-cols-1 items-end gap-3 lg:grid-cols-[1fr_auto_1fr]">
        <div>
          <h1 className="font-serif text-2xl font-bold text-text-primary">
            拠点詳細 {office ? `- ${office.name}` : ''}
          </h1>
        </div>
        <div className="flex justify-center">
          {id && navRecords.length > 0 && (
            <RecordNavigator
              currentId={id}
              records={navRecords}
              hrefTemplate="/offices/{id}"
              entityLabel="拠点"
              truncated={navRecords.length >= 500}
            />
          )}
        </div>
        <div className="flex gap-2 lg:justify-end">
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
            <div className="sm:col-span-2">
              <dt className="text-xs font-medium text-text-secondary">稼働曜日</dt>
              <dd
                className="mt-1 flex flex-wrap gap-1.5 text-sm"
                data-testid="office-operating-weekdays"
              >
                {weekdayCells.map(({ wd, label }) => {
                  const open = operatingSet.has(wd);
                  return (
                    <span
                      key={wd}
                      className={
                        'inline-block min-w-[28px] rounded border px-2 py-0.5 text-center ' +
                        (open
                          ? 'border-brand-primary font-bold text-text-primary'
                          : 'border-border-default text-text-muted opacity-50')
                      }
                      data-testid={`office-operating-weekday-${wd}`}
                      data-state={open ? 'open' : 'closed'}
                    >
                      {label}
                    </span>
                  );
                })}
              </dd>
            </div>
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
