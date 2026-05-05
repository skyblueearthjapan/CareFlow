'use client';

/**
 * UserEditDialog — `/admin/users` 編集 (Wave 4-F).
 *
 * Editable fields: email / role / must_change_password. Staff linkage is
 * deliberately read-only here; staff master CRUD has its own page.
 */

import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useUpdateAdminUser } from '@/lib/queries/admin-users';
import {
  ADMIN_USER_ROLES,
  type AdminUserRead,
  type AdminUserRole,
  roleLabel,
} from '@/lib/schemas/admin-user';

interface Props {
  target: AdminUserRead | null;
  onClose: () => void;
}

export function UserEditDialog({ target, onClose }: Props) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<AdminUserRole>('staff');
  const [mustChange, setMustChange] = useState(false);

  useEffect(() => {
    if (target) {
      setEmail(target.email);
      setRole(target.role);
      setMustChange(target.must_change_password);
    }
  }, [target]);

  const update = useUpdateAdminUser({
    onSuccess: () => onClose(),
  });

  const submit = () => {
    if (!target) return;
    update.mutate({
      id: target.id,
      payload: {
        email: email !== target.email ? email : undefined,
        role: role !== target.role ? role : undefined,
        must_change_password:
          mustChange !== target.must_change_password ? mustChange : undefined,
      },
    });
  };

  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>ユーザー編集</DialogTitle>
          <DialogDescription>
            メール / ロール / 次回ログイン時パスワード変更フラグを更新できます。
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          className="space-y-3"
        >
          <div className="space-y-1.5">
            <Label htmlFor="edit-user-email">メール</Label>
            <Input
              id="edit-user-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="edit-user-role">ロール</Label>
            <select
              id="edit-user-role"
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
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input
              type="checkbox"
              checked={mustChange}
              onChange={(e) => setMustChange(e.target.checked)}
              className="h-4 w-4"
            />
            次回ログイン時にパスワード変更を必須にする
          </label>
          {update.isError && (
            <Alert variant="destructive">
              <AlertDescription>
                {update.error instanceof Error
                  ? update.error.message
                  : '更新に失敗しました'}
              </AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              キャンセル
            </Button>
            <Button type="submit" disabled={update.isPending}>
              {update.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
