'use client';

/**
 * UserEditDialog — `/admin/users` 編集 (Wave 4-F / P2 staff linkage).
 *
 * Editable fields: email / username / role / staff linkage /
 * must_change_password. Staff linkage can be set, changed, or cleared here
 * (P2 — the previous read-only restriction was removed). Clearing both email
 * and username would make the account unable to log in; the backend rejects it
 * with 422 and the UI warns ahead of time.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
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
import { useAdminUsers, useUpdateAdminUser } from '@/lib/queries/admin-users';
import { useStaffList } from '@/lib/queries/staff';
import {
  ADMIN_USER_ROLES,
  type AdminUserRead,
  type AdminUserRole,
  type AdminUserUpdate,
  roleLabel,
} from '@/lib/schemas/admin-user';

interface Props {
  target: AdminUserRead | null;
  onClose: () => void;
}

const NO_STAFF = '';

export function UserEditDialog({ target, onClose }: Props) {
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [role, setRole] = useState<AdminUserRole>('staff');
  const [staffId, setStaffId] = useState<string>(NO_STAFF);
  const [mustChange, setMustChange] = useState(false);

  // Track whether the current username was auto-filled from a staff code (same
  // logic as UserCreateDialog): allows re-selection to overwrite it.
  const autoFilledRef = useRef(false);

  useEffect(() => {
    if (target) {
      setEmail(target.email ?? '');
      setUsername(target.username ?? '');
      setRole(target.role);
      setStaffId(target.staff_id ?? NO_STAFF);
      setMustChange(target.must_change_password);
      autoFilledRef.current = false;
    }
  }, [target]);

  // Staff picker source + already-linked staff ids (exclude staff linked to
  // *other* accounts; the current account's own linkage stays selectable).
  const { data: staffList } = useStaffList({ limit: 500 });
  const { data: usersData } = useAdminUsers({ includeDeleted: false, limit: 200 });

  const linkedByOthers = useMemo(() => {
    const set = new Set<string>();
    for (const u of usersData?.items ?? []) {
      if (u.staff_id && u.id !== target?.id) set.add(u.staff_id);
    }
    return set;
  }, [usersData, target?.id]);

  const staffOptions = useMemo(
    () => (staffList ?? []).filter((s) => !s.deleted_at),
    [staffList],
  );

  const update = useUpdateAdminUser({
    onSuccess: () => onClose(),
  });

  // Auto-fill username from the selected staff code when the username field is
  // currently empty (or was itself auto-filled). Mirrors UserCreateDialog.
  const handleStaffChange = (nextStaffId: string) => {
    setStaffId(nextStaffId);
    const staff = staffOptions.find((s) => s.id === nextStaffId);
    if (staff?.code && (username.trim() === '' || autoFilledRef.current)) {
      setUsername(staff.code);
      autoFilledRef.current = true;
    } else if (!nextStaffId && autoFilledRef.current) {
      setUsername('');
      autoFilledRef.current = false;
    }
  };

  const handleUsernameChange = (value: string) => {
    autoFilledRef.current = false;
    setUsername(value);
  };

  const normalizedEmail = email.trim() === '' ? null : email.trim();
  const normalizedUsername = username.trim() === '' ? null : username.trim();
  const wouldLoseLogin = normalizedEmail === null && normalizedUsername === null;

  // When the role is being changed, enforce the same rules as the create dialog:
  // staff role → username required; admin/manager → email required.
  const roleChanged = target !== null && role !== target.role;
  const emailRequired = roleChanged && (role === 'admin' || role === 'manager');
  const usernameRequired = roleChanged && role === 'staff';
  const roleConstraintViolated =
    (emailRequired && normalizedEmail === null) ||
    (usernameRequired && normalizedUsername === null);

  const submitBlocked = wouldLoseLogin || roleConstraintViolated;

  const submit = () => {
    if (!target) return;
    const currentStaff = target.staff_id ?? null;
    const nextStaff = staffId === NO_STAFF ? null : staffId;
    const currentEmail = target.email ?? null;
    const currentUsername = target.username ?? null;

    const payload: AdminUserUpdate = {
      email: normalizedEmail !== currentEmail ? normalizedEmail : undefined,
      username: normalizedUsername !== currentUsername ? normalizedUsername : undefined,
      role: role !== target.role ? role : undefined,
      // Send null explicitly to unlink; undefined leaves it unchanged.
      staff_id: nextStaff !== currentStaff ? nextStaff : undefined,
      must_change_password:
        mustChange !== target.must_change_password ? mustChange : undefined,
    };
    update.mutate({ id: target.id, payload });
  };

  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>ユーザー編集</DialogTitle>
          <DialogDescription>
            メール / ログインID / ロール / スタッフ紐付け / パスワード変更フラグを更新できます。
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
          <div className="space-y-1.5">
            <Label htmlFor="edit-user-staff">スタッフ紐付け</Label>
            <select
              id="edit-user-staff"
              value={staffId}
              onChange={(e) => handleStaffChange(e.target.value)}
              className="h-10 w-full rounded-md border border-border-default bg-bg-base px-3 text-sm"
            >
              <option value={NO_STAFF}>（解除 / 紐付けなし）</option>
              {staffOptions.map((s) => {
                const taken = linkedByOthers.has(s.id);
                return (
                  <option key={s.id} value={s.id} disabled={taken}>
                    {s.code ? `${s.code} ` : ''}
                    {s.name}
                    {taken ? '（他アカウントに紐付け済み）' : ''}
                  </option>
                );
              })}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="edit-user-username">ログインID</Label>
            <Input
              id="edit-user-username"
              value={username}
              onChange={(e) => handleUsernameChange(e.target.value)}
              placeholder="例: S001"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="edit-user-email">メール</Label>
            <Input
              id="edit-user-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
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
          {wouldLoseLogin && (
            <Alert variant="destructive">
              <AlertDescription>
                メールとログインIDの両方が空です。このままでは本人がログインできなくなります。少なくとも一方を入力してください。
              </AlertDescription>
            </Alert>
          )}
          {!wouldLoseLogin && roleConstraintViolated && (
            <Alert variant="destructive">
              <AlertDescription>
                {emailRequired
                  ? 'ロールを管理者/マネージャーに変更するにはメールアドレスが必須です。'
                  : 'ロールをスタッフに変更するにはログインIDが必須です。'}
              </AlertDescription>
            </Alert>
          )}
          {update.isError && (
            <Alert variant="destructive">
              <AlertDescription>
                {update.error instanceof Error ? update.error.message : '更新に失敗しました'}
              </AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              キャンセル
            </Button>
            <Button type="submit" disabled={update.isPending || submitBlocked}>
              {update.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
