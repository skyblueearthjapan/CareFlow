'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { signOut, useSession } from 'next-auth/react';
import {
  Bell,
  BellOff,
  Check,
  Heart,
  KeyRound,
  LogOut,
  Menu,
  ScrollText,
  Settings,
  ShieldCheck,
  User,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Skeleton } from '@/components/ui/skeleton';
import { useMarkAllRead, useMarkRead, useNotifications } from '@/lib/queries/notifications';
import { roleLabel } from '@/lib/schemas/staff';
import type { NotificationRead } from '@/lib/schemas/notification';
import { cn } from '@/lib/utils';

interface HeaderProps {
  title?: string;
  onToggleSidebar: () => void;
}

export function Header({ title = 'CareFlow', onToggleSidebar }: HeaderProps) {
  return (
    <header className="flex h-[60px] items-center justify-between border-b border-border-default bg-bg-base px-4">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={onToggleSidebar} aria-label="サイドバーを開閉">
          <Menu className="h-5 w-5" strokeWidth={1.75} />
        </Button>
      </div>

      <div className="flex items-center gap-2">
        <div className="mr-2 flex items-center gap-2">
          <Heart className="h-6 w-6 text-brand-primary" strokeWidth={1.75} />
          <span className="font-serif text-lg font-bold text-text-primary">{title}</span>
        </div>
        <NotificationButton />
        <UserMenuButton />
        <SchedulingSettingsButton />
        <AdminUsersButton />
        <AuditLogsButton />
      </div>
    </header>
  );
}

// ============================================================
// Notification button + popover
// ============================================================

function NotificationButton() {
  const [open, setOpen] = useState(false);
  const { data, isLoading, isError, refetch } = useNotifications({ limit: 30 });
  const markRead = useMarkRead();
  const markAllRead = useMarkAllRead();

  // 開いたタイミングで最新化（バックグラウンドポーリングは 60 秒間隔のため）
  useEffect(() => {
    if (open) void refetch();
  }, [open, refetch]);

  const items = data?.items ?? [];
  const unreadCount = items.filter((n) => n.read_at == null).length;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="通知" className="relative">
          <Bell className="h-5 w-5" strokeWidth={1.75} />
          {unreadCount > 0 && (
            <span
              aria-label={`未読 ${unreadCount} 件`}
              className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-error px-1 text-[10px] font-bold leading-none text-white"
            >
              {unreadCount > 99 ? '99+' : unreadCount}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 max-w-[calc(100vw-2rem)] p-0">
        <div className="flex items-center justify-between border-b border-border-default px-3 py-2">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Bell className="h-4 w-4 text-brand-primary" strokeWidth={1.75} />
            通知
          </div>
          {unreadCount > 0 && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => markAllRead.mutate()}
              disabled={markAllRead.isPending}
              className="h-7 px-2 text-xs"
            >
              <Check className="mr-1 h-3 w-3" />
              すべて既読
            </Button>
          )}
        </div>

        <div className="max-h-96 overflow-y-auto p-2">
          {isLoading && (
            <div className="space-y-2">
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
            </div>
          )}

          {isError && (
            <p className="px-2 py-4 text-sm text-text-muted">通知の取得に失敗しました。</p>
          )}

          {!isLoading && !isError && items.length === 0 && (
            <div className="flex flex-col items-center justify-center gap-2 py-8 text-text-muted">
              <BellOff className="h-6 w-6" />
              <p className="text-sm">通知はありません</p>
            </div>
          )}

          {items.length > 0 && (
            <ul className="space-y-1">
              {items.map((n) => (
                <li key={n.id}>
                  <NotificationRow
                    item={n}
                    onTap={(id) => {
                      if (n.read_at == null) markRead.mutate(id);
                    }}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function NotificationRow({ item, onTap }: { item: NotificationRead; onTap: (id: string) => void }) {
  const unread = item.read_at == null;
  return (
    <button
      type="button"
      onClick={() => onTap(item.id)}
      className={cn(
        'flex w-full flex-col items-start gap-1 rounded-md border border-border-default p-2 text-left transition-colors',
        unread ? 'bg-bg-base' : 'bg-bg-muted text-text-secondary',
        'hover:bg-bg-muted',
      )}
    >
      <div className="flex w-full items-center justify-between gap-2">
        <span className="text-sm font-medium">{item.title}</span>
        {unread && (
          <Badge variant="info" className="shrink-0 text-[10px]">
            未読
          </Badge>
        )}
      </div>
      {item.body && (
        <p className="line-clamp-3 whitespace-pre-wrap text-xs text-text-muted">{item.body}</p>
      )}
      <span className="text-[10px] text-text-muted">{formatRelative(item.created_at)}</span>
    </button>
  );
}

function formatRelative(iso: string): string {
  const now = Date.now();
  const t = new Date(iso).getTime();
  const diffSec = Math.max(0, Math.floor((now - t) / 1000));
  if (diffSec < 60) return 'たった今';
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}分前`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour}時間前`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 30) return `${diffDay}日前`;
  return iso.slice(0, 10);
}

// ============================================================
// User menu button + popover
// ============================================================

function UserMenuButton() {
  const [open, setOpen] = useState(false);
  const { data: session, status } = useSession();
  const user = session?.user;
  const role = user?.role;
  const initial = (user?.name ?? user?.email ?? '?').slice(0, 1).toUpperCase();

  const handleSignOut = () => {
    setOpen(false);
    void signOut({ callbackUrl: '/login' });
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="ユーザーメニュー">
          <User className="h-5 w-5" strokeWidth={1.75} />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 p-0">
        {status === 'loading' || !user ? (
          <div className="space-y-2 p-4">
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-4 w-48" />
          </div>
        ) : (
          <>
            <div className="flex items-center gap-3 border-b border-border-default p-4">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand-primary/15 font-serif text-base font-bold text-brand-primary">
                {initial}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-text-primary">
                  {user.name ?? '--'}
                </p>
                <p className="truncate text-xs text-text-muted">{user.email ?? '--'}</p>
              </div>
              {role && (
                <Badge variant="secondary" className="shrink-0">
                  {roleLabel(role)}
                </Badge>
              )}
            </div>
            <div className="p-2">
              <Button
                type="button"
                variant="ghost"
                className="w-full justify-start text-sm"
                asChild
              >
                <Link href="/settings/password" onClick={() => setOpen(false)}>
                  <KeyRound className="mr-2 h-4 w-4" strokeWidth={1.75} />
                  パスワード変更
                </Link>
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="w-full justify-start text-sm"
                onClick={handleSignOut}
              >
                <LogOut className="mr-2 h-4 w-4" strokeWidth={1.75} />
                ログアウト
              </Button>
            </div>
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}

// ============================================================
// Admin: Users management quick-access button
// ============================================================

function AdminUsersButton() {
  const { data: session } = useSession();
  if (session?.user?.role !== 'admin') return null;
  return (
    <Button variant="ghost" size="icon" asChild aria-label="ユーザー管理" title="ユーザー管理">
      <Link href="/admin/users">
        <ShieldCheck className="h-5 w-5" strokeWidth={1.75} />
      </Link>
    </Button>
  );
}

// ============================================================
// 監査ログ — Sidebar から移設したクイックアクセス
// 申請履歴はサイドバーの連携の下に移設済 (Sidebar.tsx 参照)
// ============================================================

// ============================================================
// 最適化ルール設定 (Phase G-88) — admin/manager のみ
// ============================================================

function SchedulingSettingsButton() {
  const { data: session } = useSession();
  const role = session?.user?.role;
  if (role !== 'admin' && role !== 'manager') return null;
  return (
    <Button
      variant="ghost"
      size="icon"
      asChild
      aria-label="最適化ルール設定"
      title="最適化ルール設定"
    >
      <Link href="/settings/scheduling">
        <Settings className="h-5 w-5" strokeWidth={1.75} />
      </Link>
    </Button>
  );
}

function AuditLogsButton() {
  const { data: session } = useSession();
  if (session?.user?.role !== 'admin') return null;
  return (
    <Button variant="ghost" size="icon" asChild aria-label="監査ログ" title="監査ログ">
      <Link href="/admin/audit-logs">
        <ScrollText className="h-5 w-5" strokeWidth={1.75} />
      </Link>
    </Button>
  );
}
