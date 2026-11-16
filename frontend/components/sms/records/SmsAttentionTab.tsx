'use client';

/**
 * お知らせの記録 — 「気になる番号」(設計 §5-4 の 4・決まり §10)。
 * 続けて届かない・使われていない・受信の設定で受け取れない番号。患者様の画面で連絡先を合わせる。
 */
import Link from 'next/link';

import { apiErrorMessage } from '@/lib/api/errorMessage';
import { useSmsAttentionNumbers } from '@/lib/queries/sms';

import { formatJstDateTime } from '../smsLabels';

export function SmsAttentionTab() {
  const q = useSmsAttentionNumbers(true);
  const items = q.data ?? [];

  return (
    <div className="space-y-3" data-testid="sms-attention-tab">
      <p className="text-sm text-text-secondary">
        お知らせが届かない番号です。患者様の画面で連絡先を確かめ、合わせてください。
      </p>
      {q.isError ? (
        <p className="text-sm text-red-700">読み込めませんでした: {apiErrorMessage(q.error)}</p>
      ) : q.isLoading ? (
        <p className="text-sm text-text-secondary">読み込んでいます…</p>
      ) : items.length === 0 ? (
        <p className="rounded border border-border-default p-4 text-sm text-text-secondary">
          気になる番号はありません。
        </p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border-default">
          <table className="w-full text-sm">
            <thead className="whitespace-nowrap bg-bg-muted text-left text-text-secondary">
              <tr>
                <th className="px-3 py-2">患者様</th>
                <th className="px-3 py-2">番号</th>
                <th className="px-3 py-2">気になること</th>
                <th className="px-3 py-2 text-right">回数</th>
                <th className="px-3 py-2">最後</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border-default">
              {items.map((it, i) => (
                <tr key={`${it.patient_id}-${it.phone_last4 ?? ''}-${it.kind}-${i}`}>
                  <td className="whitespace-nowrap px-3 py-2 font-bold text-text-primary">
                    {it.patient_name}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">
                    {it.relation_label ?? '—'}
                    {it.phone_last4 ? `（末尾 ${it.phone_last4}）` : ''}
                  </td>
                  <td className="px-3 py-2 text-amber-900">{it.kind_label}</td>
                  <td className="px-3 py-2 text-right tnum">{it.count}</td>
                  <td className="whitespace-nowrap px-3 py-2 tnum">
                    {formatJstDateTime(it.last_at)}
                  </td>
                  <td className="px-3 py-2">
                    <Link
                      href={`/patients/${it.patient_id}`}
                      className="whitespace-nowrap text-brand-primary underline underline-offset-2"
                    >
                      連絡先を確かめる
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
