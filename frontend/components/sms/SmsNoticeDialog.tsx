'use client';

/**
 * 予定のお知らせ (SMS) のダイアログ。タブは「週のお知らせ」と「変更のお知らせ（N）」。
 * 設計 docs/plans/sms-notify-design-2026-11-16.md §5-1・§5-2。
 * 大きさと文字はモーダルの基準 (add-visit-anywhere-design §3-5) に合わせる。
 * 送っている・済みにしているあいだは閉じない (Esc・外側・×)。結果を見失わないため。
 * 窓口の設定に誤りがあるか足りない (status.provider_ok === false) ときは帯を出し、送れなくする
 * (決まり §13-6)。
 * 届いていないお知らせ (status.undelivered_count) があれば、お知らせの記録へのリンクを出す。
 * 縦の並びは「見出し・帯・タブ・一覧 (ここだけスクロール)・下の合計と送るボタン」。
 * 下の欄は一覧に重ならない (どの高さでも、一覧の文や済みにするフォームが下に隠れない)。
 */
import { useState } from 'react';
import Link from 'next/link';
import { useIsMutating } from '@tanstack/react-query';

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { SMS_SEND_MUTATION_KEY } from '@/lib/queries/sms';
import type { SmsStatus } from '@/lib/schemas/sms';

import { ChangeNoticeTab } from './ChangeNoticeTab';
import { WeekNoticeTab } from './WeekNoticeTab';
import { handleTabListKeyDown } from './tabKeys';

export type SmsNoticeTabKey = 'week' | 'change';

export interface SmsNoticeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 見ている週の月曜 (YYYY-MM-DD)。 */
  weekStart: string;
  status: SmsStatus | undefined;
  initialTab?: SmsNoticeTabKey;
}

export function SmsTestModeBanner({ status }: { status: SmsStatus | undefined }) {
  if (!status) return null;
  const isTest = status.send_mode === 'test';
  const isMock = status.provider === 'mock';
  if (!isTest && !isMock) return null;
  return (
    <div
      className="space-y-1 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950"
      data-testid="sms-test-banner"
    >
      {isTest ? (
        <p className="font-bold">
          試験の状態です。決めた番号（{status.test_allowlist_count ?? 0} 件）にだけ送ります
        </p>
      ) : null}
      {isMock ? <p>いまは記録するだけで、実際には送りません</p> : null}
    </div>
  );
}

/** 窓口の設定に誤りがあるか、足りないときの帯。 */
export function SmsProviderBanner({ status }: { status: SmsStatus | undefined }) {
  if (status?.provider_ok !== false) return null;
  return (
    <p
      role="alert"
      className="rounded-md border border-red-300 bg-red-50 p-3 text-sm font-bold text-red-800"
      data-testid="sms-provider-banner"
    >
      SMS の設定に誤りがあるか、足りないため、いまは送れません。管理者に確かめてください
    </p>
  );
}

/**
 * 届いていないお知らせ (直近 7 日) の件数。押すと記録の「送信の一覧」を届いていないもの
 * (`undelivered=1`・件数と同じ決まり) で絞って開く。
 */
export function SmsUndeliveredLink({ status }: { status: SmsStatus | undefined }) {
  const n = status?.undelivered_count ?? 0;
  if (n <= 0) return null;
  const href = '/sms-notices?tab=messages&undelivered=1';
  return (
    <Link
      href={href}
      className="inline-flex w-fit items-center rounded-md border border-red-300 bg-red-50 px-3 py-1.5 text-sm font-bold text-red-800 hover:bg-red-100"
      data-testid="sms-undelivered-link"
    >
      届いていないお知らせ {n} 件
    </Link>
  );
}

export function SmsNoticeDialog({
  open,
  onOpenChange,
  weekStart,
  status,
  initialTab = 'week',
}: SmsNoticeDialogProps) {
  const [tab, setTab] = useState<SmsNoticeTabKey>(initialTab);
  const pendingCount = status?.pending_count ?? 0;
  const busy = useIsMutating({ mutationKey: SMS_SEND_MUTATION_KEY }) > 0;
  const sendBlocked = status?.provider_ok === false;

  const handleOpenChange = (next: boolean) => {
    if (!next && busy) return;
    onOpenChange(next);
  };

  const tabs: ReadonlyArray<{ key: SmsNoticeTabKey; label: string }> = [
    { key: 'week', label: '週のお知らせ' },
    { key: 'change', label: `変更のお知らせ（${pendingCount}）` },
  ];

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className="flex max-h-[90dvh] max-w-5xl flex-col"
        data-testid="sms-notice-dialog"
        data-busy={busy ? 'true' : 'false'}
        onEscapeKeyDown={(e) => {
          if (busy) e.preventDefault();
        }}
        onInteractOutside={(e) => {
          if (busy) e.preventDefault();
        }}
      >
        <DialogHeader>
          <DialogTitle>予定のお知らせ（SMS）</DialogTitle>
          <DialogDescription className="text-sm">
            送る前に、どなたに・どの番号へ・どんな文面で送るかを確かめてください。
          </DialogDescription>
        </DialogHeader>

        <SmsProviderBanner status={status} />
        <SmsTestModeBanner status={status} />
        <SmsUndeliveredLink status={status} />

        {busy ? (
          <p role="status" className="text-sm text-text-secondary" data-testid="sms-dialog-busy">
            送っています。終わるまで、この画面は閉じられません。
          </p>
        ) : null}

        <div
          role="tablist"
          aria-label="お知らせの種類"
          className="flex gap-1 border-b border-border-default"
        >
          {tabs.map((t) => {
            const active = tab === t.key;
            return (
              <button
                key={t.key}
                type="button"
                role="tab"
                id={`sms-tab-${t.key}`}
                aria-selected={active}
                aria-controls={`sms-panel-${t.key}`}
                tabIndex={active ? 0 : -1}
                onClick={() => setTab(t.key)}
                onKeyDown={(e) =>
                  handleTabListKeyDown(
                    e,
                    tabs.map((x) => x.key),
                    tab,
                    setTab,
                    (k) => `sms-tab-${k}`,
                  )
                }
                className={[
                  '-mb-px border-b-[3px] px-4 pb-2.5 pt-2 text-sm',
                  active
                    ? 'border-brand-primary font-bold text-brand-primary-hover'
                    : 'border-transparent text-text-secondary hover:text-text-primary',
                ].join(' ')}
              >
                {t.label}
              </button>
            );
          })}
        </div>

        <div
          role="tabpanel"
          id={`sms-panel-${tab}`}
          aria-labelledby={`sms-tab-${tab}`}
          className="flex min-h-0 flex-1 flex-col"
        >
          {tab === 'week' ? (
            <WeekNoticeTab
              weekStart={weekStart}
              active={open}
              sendBlocked={sendBlocked}
              status={status}
            />
          ) : (
            <ChangeNoticeTab active={open} sendBlocked={sendBlocked} status={status} />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
