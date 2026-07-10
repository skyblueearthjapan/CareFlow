'use client';

/**
 * Bottom-sheet style modal listing the current user's notifications.
 *
 * Dialog primitive is used because the design system has no dedicated
 * Sheet/Drawer component yet (Wave 4-D scope was tight). The CSS
 * positions the panel on the bottom edge of the viewport with a generous
 * tap target so it reads as a sheet on mobile.
 */
import { useEffect } from 'react';
import { Bell, Check } from 'lucide-react';

import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { RakusukeNote } from '@/components/brand/Rakusuke';
import { useMarkAllRead, useMarkRead, useNotifications } from '@/lib/queries/notifications';
import type { NotificationRead } from '@/lib/schemas/notification';

interface NotificationListSheetProps {
  open: boolean;
  onOpenChange: (next: boolean) => void;
}

function formatRelative(iso: string): string {
  // Rough "n分前" formatting — keeping logic local so we don't add a
  // date-fns dependency for one screen.
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

function NotificationRow({ item, onTap }: { item: NotificationRead; onTap: (id: string) => void }) {
  const unread = item.read_at == null;
  return (
    <button
      type="button"
      onClick={() => onTap(item.id)}
      className={cn(
        'flex w-full flex-col items-start gap-1 rounded-md border border-border-default p-3 text-left transition-colors',
        unread ? 'bg-bg-base' : 'bg-bg-muted text-text-secondary',
        'hover:bg-bg-muted',
      )}
    >
      <div className="flex w-full items-center justify-between gap-2">
        <span className="font-medium">{item.title}</span>
        {unread && (
          <Badge variant="info" className="shrink-0 text-[10px]">
            未読
          </Badge>
        )}
      </div>
      {item.body && (
        <p className="text-xs text-text-muted line-clamp-3 whitespace-pre-wrap">{item.body}</p>
      )}
      <span className="text-[10px] text-text-muted">{formatRelative(item.created_at)}</span>
    </button>
  );
}

export function NotificationListSheet({ open, onOpenChange }: NotificationListSheetProps) {
  const { data, isLoading, isError, refetch } = useNotifications({
    limit: 50,
  });
  const markRead = useMarkRead();
  const markAllRead = useMarkAllRead();

  // When the sheet is opened, force a fresh fetch so the user sees the
  // latest unread state — the 60s background poll could otherwise make
  // the list stale by up to a minute.
  useEffect(() => {
    if (open) void refetch();
  }, [open, refetch]);

  const items = data?.items ?? [];
  const unreadCount = items.filter((n) => n.read_at == null).length;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="bottom-0 left-0 right-0 top-auto translate-x-0 translate-y-0 max-w-full rounded-t-xl rounded-b-none border-x-0 border-b-0 max-h-[80vh] overflow-y-auto"
        aria-describedby="notif-sheet-desc"
      >
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <DialogTitle className="flex items-center gap-2">
              <Bell className="h-5 w-5 text-brand-primary" />
              通知
            </DialogTitle>
            {unreadCount > 0 && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => markAllRead.mutate()}
                disabled={markAllRead.isPending}
              >
                <Check className="mr-1 h-3 w-3" />
                すべて既読
              </Button>
            )}
          </div>
          <DialogDescription id="notif-sheet-desc" className="sr-only">
            通知一覧 (タップで既読化)
          </DialogDescription>

          {isLoading && (
            <div className="space-y-2">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          )}

          {isError && <p className="text-sm text-text-muted">通知の取得に失敗しました。</p>}

          {!isLoading && !isError && items.length === 0 && (
            <div className="py-6">
              <RakusukeNote
                pose="heart"
                title="通知はありません"
                comment="新しいお知らせが届いたら、ここに出ます"
              />
            </div>
          )}

          {items.length > 0 && (
            <div className="space-y-2">
              {items.map((n) => (
                <NotificationRow
                  key={n.id}
                  item={n}
                  onTap={(id) => {
                    if (n.read_at == null) markRead.mutate(id);
                  }}
                />
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
