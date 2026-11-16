'use client';

/**
 * 週の画面の上の段の［予定のお知らせ］(設計 §5-1)。
 * 管理者で、旗 sms_notify が ON の拠点があるとき (GET /sms/status の enabled) だけ出す。
 * お知らせ待ちの件数を印で出し、急ぎ (今日・明日の変更) があれば強い色にする。
 */
import { useState } from 'react';
import { MessageSquareText } from 'lucide-react';
import { useSession } from 'next-auth/react';

import { Button } from '@/components/ui/button';
import { isAdminRole } from '@/lib/rbac';
import { SMS_STATUS_POLL_MS, useSmsStatus } from '@/lib/queries/sms';

import { SmsNoticeDialog } from './SmsNoticeDialog';

export interface SmsNoticeButtonProps {
  /** 見ている週の月曜 (YYYY-MM-DD)。 */
  weekStart: string;
}

export function SmsNoticeButton({ weekStart }: SmsNoticeButtonProps) {
  const { data: session } = useSession();
  const isAdmin = isAdminRole(session?.user?.role);
  const status = useSmsStatus(isAdmin, { refetchInterval: SMS_STATUS_POLL_MS });
  const [open, setOpen] = useState(false);

  if (!isAdmin || status.data?.enabled !== true) return null;

  const pending = status.data.pending_count ?? 0;
  const urgent = status.data.urgent_count ?? 0;

  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={() => setOpen(true)}
        data-testid="sms-notice-button"
      >
        <MessageSquareText className="mr-1 h-4 w-4" aria-hidden />
        予定のお知らせ
        {pending > 0 ? (
          <span
            className={
              'ml-1.5 rounded-full px-1.5 text-xs font-bold leading-5 text-white tnum ' +
              (urgent > 0 ? 'bg-red-600' : 'bg-brand-primary')
            }
            data-testid="sms-notice-badge"
            data-urgent={urgent > 0 ? 'true' : 'false'}
          >
            <span aria-hidden>{pending > 99 ? '99+' : pending}</span>
            <span className="sr-only">
              お知らせ待ち {pending} 名{urgent > 0 ? `（うち急ぎ ${urgent} 名）` : ''}
            </span>
          </span>
        ) : null}
      </Button>
      {open ? (
        <SmsNoticeDialog
          open={open}
          onOpenChange={setOpen}
          weekStart={weekStart}
          status={status.data}
          initialTab={pending > 0 ? 'change' : 'week'}
        />
      ) : null}
    </>
  );
}
