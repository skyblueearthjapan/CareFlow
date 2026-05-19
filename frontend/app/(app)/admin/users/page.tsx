'use client';

/**
 * /admin/users — User master CRUD (Wave 4-F).
 *
 * Replaces the Phase-1 stub. Admin-only — non-admins are redirected to
 * `/dashboard` (sidebar already hides the link). Search by email substring,
 * filter by role, optionally surface soft-deleted accounts. Row actions:
 * 編集 / パスワードリセット / 削除.
 */

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { KeyRound, Pencil, Plus, Search, Trash2 } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import {
  useAdminUsers,
  useDeleteAdminUser,
  useResetAdminUserPassword,
} from '@/lib/queries/admin-users';
import {
  ADMIN_USER_ROLES,
  type AdminUserRead,
  type AdminUserRole,
  roleLabel,
} from '@/lib/schemas/admin-user';

import { UserCreateDialog } from './_components/UserCreateDialog';
import { UserEditDialog } from './_components/UserEditDialog';

type RoleFilter = 'all' | AdminUserRole;

export default function AdminUsersPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const isAdmin = session?.user?.role === 'admin';

  // Soft client-side guard; the API also enforces RBAC.
  useEffect(() => {
    if (status === 'authenticated' && !isAdmin) {
      router.replace('/dashboard');
    }
  }, [status, isAdmin, router]);

  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState<RoleFilter>('all');
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<AdminUserRead | null>(null);
  const [resetResult, setResetResult] = useState<{ email: string; tempPassword: string } | null>(
    null,
  );

  const { data, isLoading, isError, error } = useAdminUsers({
    role: roleFilter === 'all' ? undefined : roleFilter,
    q: search || undefined,
    includeDeleted,
    limit: 200,
  });

  const resetPw = useResetAdminUserPassword({
    onSuccess: (resp, userId) => {
      const target = data?.items.find((u) => u.id === userId);
      setResetResult({
        email: target?.email ?? userId,
        tempPassword: resp.temp_password,
      });
    },
  });

  const deleteUser = useDeleteAdminUser();

  const rows = useMemo(() => data?.items ?? [], [data]);

  if (status === 'loading' || (status === 'authenticated' && !isAdmin)) {
    return null;
  }

  return (
    <section className="space-y-4">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-serif text-2xl font-bold text-text-primary">ユーザー管理</h1>
          <p className="text-sm text-text-secondary">
            登録 {data?.total ?? 0} 名 / 表示 {rows.length} 名
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" />
          新規ユーザー
        </Button>
      </header>

      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-[220px]">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="メールアドレスで検索"
              className="pl-9"
              aria-label="検索"
            />
          </div>
          <select
            value={roleFilter}
            onChange={(e) => setRoleFilter(e.target.value as RoleFilter)}
            className="h-10 rounded-md border border-border-default bg-bg-base px-3 text-sm text-text-primary"
            aria-label="ロールフィルタ"
          >
            <option value="all">ロール (すべて)</option>
            {ADMIN_USER_ROLES.map((r) => (
              <option key={r} value={r}>
                {roleLabel(r)}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input
              type="checkbox"
              checked={includeDeleted}
              onChange={(e) => setIncludeDeleted(e.target.checked)}
              className="h-4 w-4"
            />
            削除済みも表示
          </label>
        </div>
      </Card>

      {resetResult && (
        <Alert>
          <AlertTitle>仮パスワードを発行しました</AlertTitle>
          <AlertDescription>
            <div className="space-y-2">
              <p>
                <span className="font-medium">{resetResult.email}</span> —
                次回ログイン時にパスワード変更が必須となります。
              </p>
              <code className="block rounded bg-bg-muted px-3 py-2 font-mono text-sm">
                {resetResult.tempPassword}
              </code>
              <Button variant="outline" size="sm" onClick={() => setResetResult(null)}>
                閉じる
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      )}

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
        ) : rows.length === 0 ? (
          <p className="text-sm text-text-muted">該当するユーザーがいません</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border-default text-left text-text-secondary">
                <tr>
                  <th className="px-3 py-2 font-medium">メール</th>
                  <th className="px-3 py-2 font-medium">ロール</th>
                  <th className="px-3 py-2 font-medium">スタッフ紐付け</th>
                  <th className="px-3 py-2 font-medium">パスワード変更要求</th>
                  <th className="px-3 py-2 font-medium">状態</th>
                  <th className="px-3 py-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                {rows.map((u) => {
                  const isSelf = session?.user?.id === u.id;
                  const isDeleted = !!u.deleted_at;
                  return (
                    <tr
                      key={u.id}
                      className="border-b border-border-default last:border-0 hover:bg-bg-muted"
                    >
                      <td className="px-3 py-2 font-medium">{u.email}</td>
                      <td className="px-3 py-2">{roleLabel(u.role)}</td>
                      <td className="px-3 py-2 text-text-secondary">
                        {u.staff_id ? `${u.staff_id.slice(0, 8)}…` : '--'}
                      </td>
                      <td className="px-3 py-2">{u.must_change_password ? '必須' : '任意'}</td>
                      <td className="px-3 py-2">
                        {isDeleted ? <span className="text-text-muted">削除済み</span> : '有効'}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={isDeleted}
                            onClick={() => setEditTarget(u)}
                            title="編集"
                          >
                            <Pencil className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={isDeleted}
                            onClick={() => resetPw.mutate(u.id)}
                            title="パスワードリセット"
                          >
                            <KeyRound className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={isDeleted || isSelf}
                            onClick={() => {
                              if (window.confirm(`${u.email} を削除します。よろしいですか？`)) {
                                deleteUser.mutate(u.id);
                              }
                            }}
                            title={isSelf ? '自分自身は削除できません' : '削除'}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <UserCreateDialog open={createOpen} onOpenChange={setCreateOpen} />
      <UserEditDialog target={editTarget} onClose={() => setEditTarget(null)} />
    </section>
  );
}
