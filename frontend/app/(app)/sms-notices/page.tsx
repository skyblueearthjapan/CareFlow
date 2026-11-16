'use client';

/**
 * `/sms-notices` — お知らせの記録 (予定のお知らせ・SMS)。管理者だけ。
 * 設計 docs/plans/sms-notify-design-2026-11-16.md §5-4。4 つの見方:
 *   送信の一覧 / 患者様ごと / 月のまとめ / 気になる番号
 *
 * 旗 sms_notify が ON の拠点が無いとき (GET /sms/status の enabled:false) は、
 * 中身を読まずに短い案内だけを出す。
 *
 * `?tab=messages|patients|summary|attention`・`?patient=<id>` (送信の一覧をその方で絞る)・
 * `?status=<状態>` (送信の一覧を状態で絞る・カンマで複数も可)・
 * `?undelivered=1` (届いていないものだけ。予定のお知らせの「届いていないお知らせ」から来る)。
 */
import { Suspense, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useSession } from 'next-auth/react';

import { RakusukeTitle } from '@/components/brand/Rakusuke';
import { Skeleton } from '@/components/ui/skeleton';
import { SmsAttentionTab } from '@/components/sms/records/SmsAttentionTab';
import { SmsMessagesTab } from '@/components/sms/records/SmsMessagesTab';
import { SmsPatientsTab } from '@/components/sms/records/SmsPatientsTab';
import { SmsSummaryTab } from '@/components/sms/records/SmsSummaryTab';
import { SMS_UNDELIVERED_FILTER } from '@/components/sms/smsLabels';
import { handleTabListKeyDown } from '@/components/sms/tabKeys';
import { useSmsStatus } from '@/lib/queries/sms';
import { isAdminRole } from '@/lib/rbac';

type SmsRecordsTab = 'messages' | 'patients' | 'summary' | 'attention';

const TABS: ReadonlyArray<{ key: SmsRecordsTab; label: string }> = [
  { key: 'messages', label: '送信の一覧' },
  { key: 'patients', label: '患者様ごと' },
  { key: 'summary', label: '月のまとめ' },
  { key: 'attention', label: '気になる番号' },
];

function parseTab(v: string | null): SmsRecordsTab {
  return v === 'patients' || v === 'summary' || v === 'attention' ? v : 'messages';
}

export default function SmsNoticesPage() {
  return (
    <Suspense
      fallback={
        <section className="space-y-4">
          <Skeleton className="h-12 w-1/2" />
          <Skeleton className="h-[400px] w-full" />
        </section>
      }
    >
      <SmsNoticesPageInner />
    </Suspense>
  );
}

function SmsNoticesPageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { data: session, status: sessionStatus } = useSession();
  const isAdmin = isAdminRole(session?.user?.role);
  const smsStatus = useSmsStatus(isAdmin);
  const tabParam = searchParams.get('tab');
  const patientParam = searchParams.get('patient') ?? '';
  const undeliveredParam = searchParams.get('undelivered');
  const statusParam =
    undeliveredParam === '1' || undeliveredParam === 'true'
      ? SMS_UNDELIVERED_FILTER
      : (searchParams.get('status') ?? '');
  const [tab, setTab] = useState<SmsRecordsTab>(() => parseTab(tabParam));

  // 管理者でなければ開かない (BE も 403 にする)。
  useEffect(() => {
    if (sessionStatus === 'authenticated' && !isAdmin) router.replace('/dashboard');
  }, [sessionStatus, isAdmin, router]);

  // 患者様の画面などから ?tab= / ?patient= 付きで来たときに合わせる。
  useEffect(() => {
    setTab(parseTab(tabParam));
  }, [tabParam, patientParam, statusParam]);

  const selectTab = (next: SmsRecordsTab) => {
    setTab(next);
    const params = new URLSearchParams(window.location.search);
    params.set('tab', next);
    window.history.replaceState(null, '', `${window.location.pathname}?${params.toString()}`);
  };

  const header = (
    <header>
      <RakusukeTitle
        pose="calendar"
        title="お知らせの記録"
        subtitle="患者様へ SMS で送った予定のお知らせを、あとから確かめます。"
      />
    </header>
  );

  if (sessionStatus === 'loading' || (isAdmin && smsStatus.isLoading)) {
    return (
      <section className="space-y-4">
        {header}
        <Skeleton className="h-[300px] w-full" />
      </section>
    );
  }
  if (!isAdmin) return null;

  if (smsStatus.data?.enabled !== true) {
    return (
      <section className="space-y-4">
        {header}
        <p
          className="rounded-md border border-border-default bg-bg-muted/40 p-4 text-sm text-text-primary"
          data-testid="sms-records-disabled"
        >
          予定のお知らせ（SMS）は、まだ使っていません。使い始めるときは「機能の切り替え」で
          「予定のお知らせ（SMS）」を ON にしてください。
        </p>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-3">
      {header}
      <div
        role="tablist"
        aria-label="お知らせの記録の見方"
        className="flex gap-1 border-b border-border-default"
      >
        {TABS.map((t) => {
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              type="button"
              role="tab"
              id={`sms-records-tab-${t.key}`}
              aria-selected={active}
              aria-controls={`sms-records-panel-${t.key}`}
              tabIndex={active ? 0 : -1}
              onClick={() => selectTab(t.key)}
              onKeyDown={(e) =>
                handleTabListKeyDown(
                  e,
                  TABS.map((x) => x.key),
                  tab,
                  selectTab,
                  (k) => `sms-records-tab-${k}`,
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
        id={`sms-records-panel-${tab}`}
        aria-labelledby={`sms-records-tab-${tab}`}
      >
        {tab === 'messages' ? (
          <SmsMessagesTab
            key={`${patientParam}|${statusParam}`}
            initialPatientId={patientParam}
            initialStatus={statusParam}
          />
        ) : tab === 'patients' ? (
          <SmsPatientsTab />
        ) : tab === 'summary' ? (
          <SmsSummaryTab />
        ) : (
          <SmsAttentionTab />
        )}
      </div>
    </section>
  );
}
