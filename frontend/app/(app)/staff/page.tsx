'use client';

/**
 * Staff master — list view (Phase 3-7).
 *
 * Replaces the placeholder D3 stub with a working table backed by
 * `useStaffList`. Search (name / code), sex filter, and primary office filter
 * happen client-side because the dataset is small (< 500 rows).
 *
 * Detail / new / edit / delete UI live under the route segments below
 * (`./[id]`, `./new`, `./[id]/edit`).
 */
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useMemo, useState } from 'react';
import { Plus, Search } from 'lucide-react';

import { StaffExcelButtons } from '@/components/staff/StaffExcelButtons';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { useOffices } from '@/lib/queries/offices';
import { useStaffList } from '@/lib/queries/staff';
import {
  STAFF_SEX_VALUES,
  roleLabel,
  sexLabel,
  statusLabel,
  type StaffRead,
} from '@/lib/schemas/staff';

const PAGE_SIZE = 20;

type SexFilter = 'all' | (typeof STAFF_SEX_VALUES)[number];

function matchesSearch(row: StaffRead, term: string): boolean {
  if (!term) return true;
  const t = term.toLowerCase();
  return (
    row.name.toLowerCase().includes(t) ||
    (row.kana?.toLowerCase().includes(t) ?? false) ||
    (row.code?.toLowerCase().includes(t) ?? false)
  );
}

export default function StaffPage() {
  const { data: session } = useSession();
  const role = session?.user?.role;
  const canCreate = role === 'admin' || role === 'manager';

  const [search, setSearch] = useState('');
  const [sexFilter, setSexFilter] = useState<SexFilter>('all');
  const [officeFilter, setOfficeFilter] = useState<string>('all');
  const [page, setPage] = useState(0);

  // Fetch a generous slice; backend caps at 500 per request.
  const STAFF_LIMIT = 500;
  const { data, isLoading, isError, error } = useStaffList({ limit: STAFF_LIMIT, offset: 0 });
  const { allOffices } = useOffices();

  const allRows = data ?? [];

  // id -> name lookup driven by the offices master (W1-C). Falls back to the
  // raw UUID prefix if the office hasn't been fetched yet (e.g. soft-deleted
  // primary office).
  const officeNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const o of allOffices) {
      map.set(o.id, o.name);
    }
    return map;
  }, [allOffices]);

  const officeLabel = (officeId: string | null | undefined): string => {
    if (!officeId) return '--';
    return officeNameById.get(officeId) ?? `${officeId.slice(0, 8)}…`;
  };

  // Filter dropdown shows only offices actually referenced by the staff list,
  // labelled by master name where available.
  const officeOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const row of allRows) {
      if (row.primary_office_id && !seen.has(row.primary_office_id)) {
        seen.set(row.primary_office_id, officeLabel(row.primary_office_id));
      }
    }
    return Array.from(seen.entries());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allRows, officeNameById]);

  const filtered = useMemo(() => {
    return allRows.filter((row) => {
      if (!matchesSearch(row, search)) return false;
      if (sexFilter !== 'all' && row.sex !== sexFilter) return false;
      if (officeFilter !== 'all' && row.primary_office_id !== officeFilter) return false;
      return true;
    });
  }, [allRows, search, sexFilter, officeFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const paged = filtered.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  return (
    <section className="space-y-4">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-serif text-2xl font-bold text-text-primary">スタッフマスタ</h1>
          <p className="text-sm text-text-secondary">
            登録済み {allRows.length} 名 / 表示 {filtered.length} 名
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canCreate && <StaffExcelButtons />}
          {canCreate && (
            <Button asChild>
              <Link href="/staff/new">
                <Plus className="h-4 w-4" />
                新規スタッフ
              </Link>
            </Button>
          )}
        </div>
      </header>

      {allRows.length >= STAFF_LIMIT && (
        <Alert>
          <AlertTitle>表示件数の上限に達している可能性があります</AlertTitle>
          <AlertDescription>
            結果が {STAFF_LIMIT} 件に達しました。さらに該当するスタッフが存在する可能性があります。
            検索条件で絞り込むか、Wave 2 のページング機能をお待ちください。
          </AlertDescription>
        </Alert>
      )}

      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-[220px]">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
            <Input
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(0);
              }}
              placeholder="氏名 / カナ / コードで検索"
              className="pl-9"
              aria-label="検索"
            />
          </div>

          <select
            value={sexFilter}
            onChange={(e) => {
              setSexFilter(e.target.value as SexFilter);
              setPage(0);
            }}
            className="h-10 rounded-md border border-border-default bg-bg-base px-3 text-sm text-text-primary"
            aria-label="性別フィルタ"
          >
            <option value="all">性別 (すべて)</option>
            {STAFF_SEX_VALUES.map((s) => (
              <option key={s} value={s}>
                {sexLabel(s)}
              </option>
            ))}
          </select>

          <select
            value={officeFilter}
            onChange={(e) => {
              setOfficeFilter(e.target.value);
              setPage(0);
            }}
            className="h-10 rounded-md border border-border-default bg-bg-base px-3 text-sm text-text-primary"
            aria-label="拠点フィルタ"
          >
            <option value="all">拠点 (すべて)</option>
            {officeOptions.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
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
            <Skeleton className="h-8 w-2/3" />
          </div>
        ) : isError ? (
          <Alert variant="destructive">
            <AlertTitle>取得に失敗しました</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        ) : paged.length === 0 ? (
          <p className="text-sm text-text-muted">該当するスタッフがいません</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border-default text-left text-text-secondary">
                <tr>
                  <th className="px-3 py-2 font-medium">コード</th>
                  <th className="px-3 py-2 font-medium">氏名</th>
                  <th className="px-3 py-2 font-medium">カナ</th>
                  <th className="px-3 py-2 font-medium">性別</th>
                  <th className="px-3 py-2 font-medium">役割</th>
                  <th className="px-3 py-2 font-medium">状態</th>
                  <th className="px-3 py-2 font-medium">主拠点</th>
                  <th className="px-3 py-2 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {paged.map((row) => (
                  <tr
                    key={row.id}
                    className="border-b border-border-default last:border-0 hover:bg-bg-muted"
                  >
                    <td className="px-3 py-2 tnum text-text-secondary">{row.code ?? '--'}</td>
                    <td className="px-3 py-2 font-medium">{row.name}</td>
                    <td className="px-3 py-2 text-text-secondary">{row.kana ?? '--'}</td>
                    <td className="px-3 py-2">{sexLabel(row.sex)}</td>
                    <td className="px-3 py-2">{roleLabel(row.role)}</td>
                    <td className="px-3 py-2">{statusLabel(row.status)}</td>
                    <td className="px-3 py-2 text-text-secondary">
                      {officeLabel(row.primary_office_id)}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex gap-2">
                        <Link
                          href={`/staff/${row.id}`}
                          className="text-brand-primary hover:underline"
                        >
                          詳細
                        </Link>
                        {canCreate ? (
                          <Link
                            href={`/staff/${row.id}/edit`}
                            className="text-brand-primary hover:underline"
                          >
                            編集
                          </Link>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {filtered.length > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-text-secondary">
            {safePage + 1} / {totalPages} ページ
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={safePage === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              前へ
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={safePage >= totalPages - 1}
              onClick={() => setPage((p) => p + 1)}
            >
              次へ
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
