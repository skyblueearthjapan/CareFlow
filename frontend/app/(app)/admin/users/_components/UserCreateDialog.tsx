'use client';

/**
 * UserCreateDialog — `/admin/users` 新規作成 (Wave 4-F).
 *
 * Two-phase dialog: form → result. The result phase shows the generated
 * temp_password exactly once so the admin can hand it to the new user out
 * of band; closing or refreshing the page loses it forever.
 */

import { useState } from 'react';
import { Copy, Loader2 } from 'lucide-react';

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useCreateAdminUser } from '@/lib/queries/admin-users';
import {
  ADMIN_USER_ROLES,
  type AdminUserCreate,
  type AdminUserCreateResponse,
  type AdminUserRole,
  roleLabel,
} from '@/lib/schemas/admin-user';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function UserCreateDialog({ open, onOpenChange }: Props) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<AdminUserRole>('staff');
  const [result, setResult] = useState<AdminUserCreateResponse | null>(null);

  const create = useCreateAdminUser({
    onSuccess: (resp) => setResult(resp),
  });

  const reset = () => {
    setEmail('');
    setRole('staff');
    setResult(null);
    create.reset();
  };

  const handleClose = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };

  const submit = () => {
    const payload: AdminUserCreate = { email, role };
    create.mutate(payload);
  };

  const copyTempPassword = async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.temp_password);
    } catch {
      // Clipboard API can fail in non-secure contexts; ignore silently.
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{result ? '仮パスワードを発行しました' : '新規ユーザー作成'}</DialogTitle>
          <DialogDescription>
            {result
              ? '次のパスワードは画面を閉じると二度と表示されません。安全な経路で本人へ伝達してください。'
              : 'メールアドレスとロールを指定してください。仮パスワードはサーバー側で生成されます。'}
          </DialogDescription>
        </DialogHeader>

        {result ? (
          <div className="space-y-3">
            <div className="space-y-1 text-sm">
              <p>
                <span className="text-text-muted">メール: </span>
                <span className="font-medium">{result.user.email}</span>
              </p>
              <p>
                <span className="text-text-muted">ロール: </span>
                {roleLabel(result.user.role as AdminUserRole)}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <code className="flex-1 rounded bg-bg-muted px-3 py-2 font-mono text-sm">
                {result.temp_password}
              </code>
              <Button
                variant="outline"
                size="sm"
                onClick={copyTempPassword}
                title="クリップボードにコピー"
              >
                <Copy className="h-4 w-4" />
              </Button>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => handleClose(false)}>
                閉じる
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
            className="space-y-3"
          >
            <div className="space-y-1.5">
              <Label htmlFor="new-user-email">メールアドレス</Label>
              <Input
                id="new-user-email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-user-role">ロール</Label>
              <select
                id="new-user-role"
                value={role}
                onChange={(e) => setRole(e.target.value as AdminUserRole)}
                className="h-10 w-full rounded-md border border-border-default bg-bg-base px-3 text-sm"
              >
                {ADMIN_USER_ROLES.map((r) => (
                  <option key={r} value={r}>
                    {roleLabel(r)}
                  </option>
                ))}
              </select>
            </div>
            {create.isError && (
              <Alert variant="destructive">
                <AlertDescription>
                  {create.error instanceof Error ? create.error.message : '作成に失敗しました'}
                </AlertDescription>
              </Alert>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => handleClose(false)}>
                キャンセル
              </Button>
              <Button type="submit" disabled={create.isPending || !email}>
                {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                作成
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
