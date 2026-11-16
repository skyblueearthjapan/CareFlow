'use client';

/**
 * 患者様の詳細の「お知らせの履歴」(設計 §5-4 の 2)。直近 5 件。
 * 管理者で、旗 sms_notify が ON のときだけ出す (呼ぶ側で管理者に絞る)。
 */
import Link from 'next/link';

import { Card } from '@/components/ui/card';
import { useSmsPatientRecent, useSmsStatus } from '@/lib/queries/sms';

import { formatJstDateTime, smsKindLabel, smsStatusTone, smsStatusWithReason } from './smsLabels';

export function SmsRecentCard({ patientId }: { patientId: string }) {
  const status = useSmsStatus(true);
  const enabled = status.data?.enabled === true;
  const q = useSmsPatientRecent(patientId, enabled, 5);

  if (!enabled) return null;
  const items = q.data ?? [];

  return (
    <Card className="space-y-3 p-5" data-testid="sms-recent-card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-serif text-lg font-bold text-text-primary">お知らせの履歴</h2>
        <Link
          href={`/sms-notices?tab=messages&patient=${encodeURIComponent(patientId)}`}
          className="text-sm text-brand-primary underline underline-offset-2"
        >
          すべて見る
        </Link>
      </div>
      {q.isLoading ? (
        <p className="text-sm text-text-muted">読み込み中…</p>
      ) : q.isError ? (
        <p className="text-sm text-error">お知らせの履歴を読み込めませんでした</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-text-muted">まだお知らせを送っていません</p>
      ) : (
        <ul className="divide-y divide-border-default">
          {items.map((m) => (
            <li key={m.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2 text-sm">
              <span className="tnum text-text-secondary">{formatJstDateTime(m.created_at)}</span>
              <span className="text-text-primary">{smsKindLabel(m.kind)}</span>
              <span className="text-text-secondary">
                {m.relation_label ?? '—'}
                {m.phone_last4 ? `（末尾 ${m.phone_last4}）` : ''}
              </span>
              <span
                className={`rounded-full border px-2 py-0.5 text-xs ${smsStatusTone(m.status)}`}
              >
                {smsStatusWithReason(m.status, m.skip_reason, true)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
