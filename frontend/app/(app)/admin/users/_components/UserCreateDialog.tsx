'use client';

/**
 * UserCreateDialog — `/admin/users` 新規作成 (Wave 4-F / P2 staff linkage).
 *
 * Two-phase dialog: form → result. The result phase shows the generated
 * temp_password exactly once so the admin can hand it to the new user out
 * of band; closing or refreshing the page loses it forever.
 *
 * Staff linkage: an optional staff picker links the account to a staff master
 * row. staff-role accounts log in with a `username` (auto-filled from the
 * staff code) instead of an email.
 */

import { useMemo, useRef, useState } from 'react';
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
import { useAdminUsers, useCreateAdminUser } from '@/lib/queries/admin-users';
import { useStaffList } from '@/lib/queries/staff';
import { isAdminRole } from '@/lib/rbac';
import {
  ADMIN_USER_ROLES,
  ADMIN_USER_ROLE_OPTIONS,
  type AdminUserCreate,
  type AdminUserCreateResponse,
  type AdminUserRole,
  roleLabel,
} from '@/lib/schemas/admin-user';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const NO_STAFF = '';

export function UserCreateDialog({ open, onOpenChange }: Props) {
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [role, setRole] = useState<AdminUserRole>('staff');
  const [staffId, setStaffId] = useState<string>(NO_STAFF);
  const [result, setResult] = useState<AdminUserCreateResponse | null>(null);

  // Track whether the current username value was auto-filled from a staff code.
  // If true, changing the staff selection overwrites it; manual edits clear the flag.
  const autoFilledRef = useRef(false);

  // Staff picker source + already-linked staff ids (to exclude taken staff).
  const { data: staffList } = useStaffList({ limit: 500 });
  const { data: usersData } = useAdminUsers({ includeDeleted: false, limit: 200 });

  const linkedStaffIds = useMemo(() => {
    const set = new Set<string>();
    for (const u of usersData?.items ?? []) {
      if (u.staff_id) set.add(u.staff_id);
    }
    return set;
  }, [usersData]);

  const staffOptions = useMemo(() => (staffList ?? []).filter((s) => !s.deleted_at), [staffList]);

  const create = useCreateAdminUser({
    onSuccess: (resp) => setResult(resp),
  });

  const reset = () => {
    setEmail('');
    setUsername('');
    setRole('staff');
    setStaffId(NO_STAFF);
    setResult(null);
    autoFilledRef.current = false;
    create.reset();
  };

  const handleClose = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };

  const handleStaffChange = (nextStaffId: string) => {
    setStaffId(nextStaffId);
    // Auto-fill (or overwrite a prior auto-fill) when a staff with a code is
    // selected. Manual edits set autoFilledRef=false and are never overwritten.
    const staff = staffOptions.find((s) => s.id === nextStaffId);
    if (staff?.code && (username.trim() === '' || autoFilledRef.current)) {
      setUsername(staff.code);
      autoFilledRef.current = true;
    } else if (!nextStaffId) {
      // Picker cleared — forget auto-fill only if it was auto-filled.
      if (autoFilledRef.current) {
        setUsername('');
        autoFilledRef.current = false;
      }
    }
  };

  const handleUsernameChange = (value: string) => {
    autoFilledRef.current = false;
    setUsername(value);
  };

  // Role-based required fields (mirrors backend 422 rules).
  const emailRequired = isAdminRole(role);
  const usernameRequired = role === 'staff';

  const canSubmit =
    !create.isPending &&
    (emailRequired ? email.trim() !== '' : true) &&
    (usernameRequired ? username.trim() !== '' : true) &&
    // At least one of email / username must be present regardless of role.
    (email.trim() !== '' || username.trim() !== '');

  const submit = () => {
    const payload: AdminUserCreate = {
      role,
      email: email.trim() !== '' ? email.trim() : undefined,
      username: username.trim() !== '' ? username.trim() : undefined,
      staff_id: staffId !== NO_STAFF ? staffId : undefined,
    };
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
              : 'ロールに応じてメールアドレス（管理者/マネージャー）またはログインID（スタッフ）を指定してください。仮パスワードはサーバー側で生成されます。'}
          </DialogDescription>
        </DialogHeader>

        {result ? (
          <div className="space-y-3">
            <div className="space-y-1 text-sm">
              <p>
                <span className="text-text-muted">メール: </span>
                <span className="font-medium">{result.user.email ?? '--'}</span>
              </p>
              <p>
                <span className="text-text-muted">ログインID: </span>
                <span className="font-medium">{result.user.username ?? '--'}</span>
              </p>
              <p>
                <span className="text-text-muted">ロール: </span>
                {roleLabel(result.user.role as AdminUserRole)}
              </p>
              {result.user.staff_name && (
                <p>
                  <span className="text-text-muted">スタッフ: </span>
                  <span className="font-medium">{result.user.staff_name}</span>
                </p>
              )}
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
              <Label htmlFor="new-user-role">ロール</Label>
              <select
                id="new-user-role"
                value={role}
                onChange={(e) => setRole(e.target.value as AdminUserRole)}
                className="h-10 w-full rounded-md border border-border-default bg-bg-base px-3 text-sm"
                autoFocus
              >
                {ADMIN_USER_ROLE_OPTIONS.map((r) => (
                  <option key={r} value={r}>
                    {roleLabel(r)}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-user-staff">スタッフ紐付け</Label>
              <select
                id="new-user-staff"
                value={staffId}
                onChange={(e) => handleStaffChange(e.target.value)}
                className="h-10 w-full rounded-md border border-border-default bg-bg-base px-3 text-sm"
              >
                <option value={NO_STAFF}>（紐付けなし）</option>
                {staffOptions.map((s) => {
                  const taken = linkedStaffIds.has(s.id);
                  return (
                    <option key={s.id} value={s.id} disabled={taken}>
                      {s.code ? `${s.code} ` : ''}
                      {s.name}
                      {taken ? '（紐付け済み）' : ''}
                    </option>
                  );
                })}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-user-username">
                ログインID{usernameRequired ? '（必須）' : '（任意）'}
              </Label>
              <Input
                id="new-user-username"
                value={username}
                onChange={(e) => handleUsernameChange(e.target.value)}
                placeholder="例: S001"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-user-email">
                メールアドレス{emailRequired ? '（必須）' : '（任意）'}
              </Label>
              <Input
                id="new-user-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
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
              <Button type="submit" disabled={!canSubmit}>
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
