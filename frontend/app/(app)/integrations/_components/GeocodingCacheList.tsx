'use client';

import { useState } from 'react';

import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { useGeocodingCache } from '@/lib/queries/integrations';

export function GeocodingCacheList() {
  const [search, setSearch] = useState('');
  const { data, isLoading, isError, error } = useGeocodingCache({
    q: search || undefined,
  });
  const rows = data?.items;
  const total = data?.total ?? 0;

  return (
    <section className="space-y-4">
      <header>
        <h2 className="font-serif text-xl font-bold text-text-primary">
          Geocoding キャッシュ
        </h2>
        <p className="text-sm text-text-secondary">
          住所 -&gt; 緯度経度 のキャッシュ (admin のみ閲覧)
        </p>
      </header>

      <Card className="p-4">
        <div className="mb-4 max-w-md">
          <Input
            placeholder="住所で検索"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

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
        ) : !rows || rows.length === 0 ? (
          <p className="text-sm text-text-muted">該当するキャッシュエントリはありません</p>
        ) : (
          <div className="overflow-x-auto">
            <p className="mb-2 text-xs text-text-muted">全 {total} 件</p>
            <table className="w-full text-sm">
              <thead className="border-b border-border-default text-left text-text-secondary">
                <tr>
                  <th className="px-3 py-2 font-medium">住所</th>
                  <th className="px-3 py-2 font-medium">緯度</th>
                  <th className="px-3 py-2 font-medium">経度</th>
                  <th className="px-3 py-2 font-medium">プロバイダ</th>
                  <th className="px-3 py-2 font-medium">取得日時</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-b border-border-default last:border-0">
                    <td className="px-3 py-2">{r.address}</td>
                    <td className="px-3 py-2 tnum">{r.lat}</td>
                    <td className="px-3 py-2 tnum">{r.lng}</td>
                    <td className="px-3 py-2 text-text-secondary">{r.provider}</td>
                    <td className="px-3 py-2 text-text-secondary">{r.looked_up_at}</td>
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
