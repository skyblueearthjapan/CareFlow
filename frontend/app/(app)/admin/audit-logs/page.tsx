'use client';

/**
 * /admin/audit-logs — Wave 4-F audit log viewer.
 *
 * Read-only listing of `audit_logs` rows. Filters: 期間 / actor user_id /
 * パス substring / HTTP method / status code. Body cell is already PII
 * redacted at write-time so this page can render the raw JSON safely.
 */

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { Filter } from 'lucide-react';

import { RakusukeNote } from '@/components/brand/Rakusuke';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { useAuditLogs } from '@/lib/queries/audit-logs';

const HTTP_METHODS = ['POST', 'PUT', 'PATCH', 'DELETE'] as const;
const ROLES = ['admin', 'manager', 'staff'] as const;
const PAGE_SIZE = 50;

function statusBadgeClass(code: number | null | undefined): string {
  if (code == null) return 'bg-bg-muted text-text-secondary';
  if (code >= 500) return 'bg-red-100 text-red-800';
  if (code >= 400) return 'bg-amber-100 text-amber-800';
  if (code >= 300) return 'bg-blue-100 text-blue-800';
  return 'bg-green-100 text-green-800';
}

export default function AdminAuditLogsPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const isAdmin = session?.user?.role === 'admin';

  useEffect(() => {
    if (status === 'authenticated' && !isAdmin) {
      router.replace('/dashboard');
    }
  }, [status, isAdmin, router]);

  const [pathQ, setPathQ] = useState('');
  const [method, setMethod] = useState<string>('');
  const [actorRole, setActorRole] = useState<string>('');
  const [statusCode, setStatusCode] = useState<string>('');
  const [page, setPage] = useState(0);

  const params = useMemo(
    () => ({
      path: pathQ || undefined,
      method: method || undefined,
      role: actorRole || undefined,
      statusCode: statusCode ? Number(statusCode) : undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    }),
    [pathQ, method, actorRole, statusCode, page],
  );

  const { data, isLoading, isError, error } = useAuditLogs(params);

  if (status === 'loading' || (status === 'authenticated' && !isAdmin)) {
    return null;
  }

  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="space-y-4">
      <header>
        <h1 className="font-serif text-2xl font-bold text-text-primary">監査ログ</h1>
        <p className="text-sm text-text-secondary">
          全 {total} 件 — POST/PUT/PATCH/DELETE のすべてが記録されます
        </p>
      </header>

      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <Filter className="h-4 w-4 text-text-muted" />
            <span className="text-sm text-text-secondary">フィルタ</span>
          </div>
          <Input
            value={pathQ}
            onChange={(e) => {
              setPathQ(e.target.value);
              setPage(0);
            }}
            placeholder="パス (例: /admin/users)"
            className="max-w-[280px]"
            aria-label="パスフィルタ"
          />
          <select
            value={method}
            onChange={(e) => {
              setMethod(e.target.value);
              setPage(0);
            }}
            className="h-10 rounded-md border border-border-default bg-bg-base px-3 text-sm"
            aria-label="HTTP メソッド"
          >
            <option value="">メソッド (すべて)</option>
            {HTTP_METHODS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <select
            value={actorRole}
            onChange={(e) => {
              setActorRole(e.target.value);
              setPage(0);
            }}
            className="h-10 rounded-md border border-border-default bg-bg-base px-3 text-sm"
            aria-label="アクターロール"
          >
            <option value="">アクター (すべて)</option>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <Input
            value={statusCode}
            onChange={(e) => {
              setStatusCode(e.target.value);
              setPage(0);
            }}
            placeholder="ステータス"
            type="number"
            className="max-w-[120px]"
            aria-label="ステータスコード"
          />
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
        ) : !data || data.items.length === 0 ? (
          <RakusukeNote
            pose="think"
            size="sm"
            title="該当するログがありません"
            comment="期間や条件を変えてみてください"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border-default text-left text-text-secondary">
                <tr>
                  <th className="px-3 py-2 font-medium">日時 (UTC)</th>
                  <th className="px-3 py-2 font-medium">アクター</th>
                  <th className="px-3 py-2 font-medium">ロール</th>
                  <th className="px-3 py-2 font-medium">メソッド</th>
                  <th className="px-3 py-2 font-medium">パス</th>
                  <th className="px-3 py-2 font-medium">ステータス</th>
                  <th className="px-3 py-2 font-medium">遅延</th>
                  <th className="px-3 py-2 font-medium">IP</th>
                  <th className="px-3 py-2 font-medium">Body (redacted)</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => (
                  <tr
                    key={row.id}
                    className="border-b border-border-default last:border-0 align-top hover:bg-bg-muted"
                  >
                    <td className="px-3 py-2 tnum text-text-secondary">{row.created_at}</td>
                    <td className="px-3 py-2 font-mono text-xs text-text-secondary">
                      {row.actor_user_id ? row.actor_user_id.slice(0, 8) : '--'}
                    </td>
                    <td className="px-3 py-2">{row.role ?? '--'}</td>
                    <td className="px-3 py-2 font-mono">{row.method ?? '--'}</td>
                    <td className="px-3 py-2 font-mono text-xs">{row.path ?? '--'}</td>
                    <td className="px-3 py-2">
                      <span
                        className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs ${statusBadgeClass(
                          row.status_code,
                        )}`}
                      >
                        {row.status_code ?? '--'}
                      </span>
                    </td>
                    <td className="px-3 py-2 tnum text-text-secondary">
                      {row.latency_ms != null ? `${row.latency_ms}ms` : '--'}
                    </td>
                    <td className="px-3 py-2 text-text-secondary">{row.ip_address ?? '--'}</td>
                    <td className="px-3 py-2 max-w-[360px]">
                      {row.request_body ? (
                        <pre className="whitespace-pre-wrap break-words rounded bg-bg-muted px-2 py-1 text-xs">
                          {JSON.stringify(row.request_body, null, 0)}
                        </pre>
                      ) : (
                        <span className="text-text-muted">--</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-text-secondary">
            {page + 1} / {totalPages} ページ
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              前へ
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages - 1}
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
