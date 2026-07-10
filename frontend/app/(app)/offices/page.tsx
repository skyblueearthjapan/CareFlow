'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';

import { RakusukeNote, RakusukeTitle } from '@/components/brand/Rakusuke';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useOffices } from '@/lib/queries/offices';

const PAGE_SIZE = 20;

export default function OfficesPage() {
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);

  const { offices, isLoading, isError, error } = useOffices({ search });

  const totalPages = Math.max(1, Math.ceil(offices.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const paged = useMemo(
    () => offices.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE),
    [offices, currentPage],
  );

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <RakusukeTitle
          pose="joy"
          title="拠点マスタ"
          subtitle="事業所(拠点)一覧 / 担当エリア管理"
        />
        <Button asChild>
          <Link href="/offices/new">新規作成</Link>
        </Button>
      </header>

      <Card className="p-5">
        <div className="mb-4 max-w-sm">
          <Input
            placeholder="拠点名 / 住所で検索"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
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
        ) : paged.length === 0 ? (
          <RakusukeNote
            pose="think"
            title="該当する拠点はありません"
            comment="検索条件を変えてみてください"
          />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-border-default text-left text-text-secondary">
                  <tr>
                    <th className="px-3 py-2 font-medium">拠点名</th>
                    <th className="px-3 py-2 font-medium">住所</th>
                    <th className="px-3 py-2 font-medium">緯度</th>
                    <th className="px-3 py-2 font-medium">経度</th>
                    <th className="px-3 py-2 font-medium">稼働曜日</th>
                    <th className="px-3 py-2 font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {paged.map((o) => {
                    const opSet = new Set<number>(o.operating_weekdays ?? []);
                    return (
                      <tr key={o.id} className="border-b border-border-default last:border-0">
                        <td className="px-3 py-2">
                          <Link
                            href={`/offices/${o.id}`}
                            className="text-brand-primary hover:underline"
                          >
                            {o.name}
                          </Link>
                        </td>
                        <td className="px-3 py-2 text-text-secondary">{o.address ?? '--'}</td>
                        <td className="px-3 py-2 tnum">{o.lat ?? '--'}</td>
                        <td className="px-3 py-2 tnum">{o.lng ?? '--'}</td>
                        <td className="px-3 py-2">
                          <div
                            className="flex gap-1 text-xs"
                            data-testid={`office-list-operating-${o.id}`}
                          >
                            {[
                              { wd: 0, label: '月' },
                              { wd: 1, label: '火' },
                              { wd: 2, label: '水' },
                              { wd: 3, label: '木' },
                              { wd: 4, label: '金' },
                              { wd: 5, label: '土' },
                              { wd: 6, label: '日' },
                            ].map(({ wd, label }) => {
                              const open = opSet.has(wd);
                              return (
                                <span
                                  key={wd}
                                  className={
                                    open
                                      ? 'font-bold text-text-primary'
                                      : 'text-text-muted opacity-50'
                                  }
                                  data-state={open ? 'open' : 'closed'}
                                >
                                  {label}
                                </span>
                              );
                            })}
                          </div>
                        </td>
                        <td className="px-3 py-2">
                          <Link
                            href={`/offices/${o.id}/edit`}
                            className="text-brand-primary hover:underline"
                          >
                            編集
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {totalPages > 1 && (
              <div className="mt-4 flex items-center justify-between text-sm text-text-secondary">
                <span>
                  {currentPage} / {totalPages} ページ ({offices.length} 件)
                </span>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={currentPage <= 1}
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                  >
                    前へ
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={currentPage >= totalPages}
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  >
                    次へ
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </Card>
    </section>
  );
}
