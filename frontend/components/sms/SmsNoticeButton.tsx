'use client';

/**
 * 週の画面の上の段の［予定のお知らせ］(設計 §5-1)。
 * 管理者で、旗 sms_notify が ON の拠点があるとき (GET /sms/status の enabled) だけ出す。
 * お知らせ待ちの件数を印で出し、急ぎ (今日・明日の変更) があれば強い色にする。
 * 画面が ?sms=changes (お知らせ待ちのベルから) か ?sms=week で開かれたら、そのタブでダイアログを開き、
 * 読み込み直しで開き直さないよう URL から sms を外す。
 */
import { useEffect, useState } from 'react';
import { MessageSquareText } from 'lucide-react';
import { useSearchParams } from 'next/navigation';
import { useSession } from 'next-auth/react';

import { Button } from '@/components/ui/button';
import { isAdminRole } from '@/lib/rbac';
import { SMS_STATUS_POLL_MS, useSmsStatus } from '@/lib/queries/sms';

import { SmsNoticeDialog, type SmsNoticeTabKey } from './SmsNoticeDialog';

const TAB_BY_PARAM: Record<string, SmsNoticeTabKey> = { changes: 'change', week: 'week' };

export interface SmsNoticeButtonProps {
  /** 見ている週の月曜 (YYYY-MM-DD)。 */
  weekStart: string;
}

export function SmsNoticeButton({ weekStart }: SmsNoticeButtonProps) {
  const { data: session } = useSession();
  const isAdmin = isAdminRole(session?.user?.role);
  const status = useSmsStatus(isAdmin, { refetchInterval: SMS_STATUS_POLL_MS });
  const [open, setOpen] = useState(false);
  const [requestedTab, setRequestedTab] = useState<SmsNoticeTabKey | null>(null);
  const smsParam = useSearchParams()?.get('sms') ?? null;
  const enabled = isAdmin && status.data?.enabled === true;

  useEffect(() => {
    const tab = smsParam ? TAB_BY_PARAM[smsParam] : undefined;
    if (!enabled || !tab) return;
    setRequestedTab(tab);
    setOpen(true);
    const url = new URL(window.location.href);
    url.searchParams.delete('sms');
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
  }, [enabled, smsParam]);

  if (!isAdmin || status.data?.enabled !== true) return null;

  const pending = status.data.pending_count ?? 0;
  const urgent = status.data.urgent_count ?? 0;

  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={() => {
          setRequestedTab(null);
          setOpen(true);
        }}
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
          initialTab={requestedTab ?? (pending > 0 ? 'change' : 'week')}
        />
      ) : null}
    </>
  );
}
