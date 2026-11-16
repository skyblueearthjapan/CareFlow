'use client';

/**
 * お知らせの記録 — 「患者様ごと」(設計 §5-4 の 2)。月ごとに、何回・何通分・最後に届いた日・
 * 届かなかった回数・お電話でお伝えした回数。
 */
import { useState } from 'react';
import Link from 'next/link';

import { apiErrorMessage } from '@/lib/api/errorMessage';
import { useSmsPatientSummary } from '@/lib/queries/sms';

import { currentJstMonth, formatJstDate } from '../smsLabels';

export function MonthPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm text-text-secondary">
      月
      <input
        type="month"
        className="h-9 rounded-md border border-border-default bg-bg-base px-2 text-sm text-text-primary"
        value={value}
        onChange={(e) => e.target.value && onChange(e.target.value)}
        data-testid="sms-month-picker"
      />
    </label>
  );
}

export function SmsPatientsTab() {
  const [month, setMonth] = useState(currentJstMonth());
  const q = useSmsPatientSummary(month, true);
  const items = q.data?.items ?? [];

  return (
    <div className="space-y-3" data-testid="sms-patients-tab">
      <MonthPicker value={month} onChange={setMonth} />
      {q.isError ? (
        <p className="text-sm text-red-700">読み込めませんでした: {apiErrorMessage(q.error)}</p>
      ) : q.isLoading ? (
        <p className="text-sm text-text-secondary">読み込んでいます…</p>
      ) : items.length === 0 ? (
        <p className="rounded border border-border-default p-4 text-sm text-text-secondary">
          この月のお知らせはありません。
        </p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border-default">
          <table className="w-full text-sm">
            <thead className="bg-bg-muted text-left text-text-secondary">
              <tr>
                <th className="px-3 py-2">患者様</th>
                <th className="px-3 py-2 text-right">回数</th>
                <th className="px-3 py-2 text-right">通数</th>
                <th className="px-3 py-2 text-right">届いた</th>
                <th className="px-3 py-2 text-right">届かなかった</th>
                <th className="px-3 py-2">最後に届いた日</th>
                <th className="px-3 py-2 text-right">お電話でお伝えした</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-default">
              {items.map((it) => (
                <tr key={it.patient_id}>
                  <td className="px-3 py-2">
                    <Link
                      href={`/sms-notices?tab=messages&patient=${it.patient_id}`}
                      className="font-bold text-text-primary underline-offset-2 hover:underline"
                    >
                      {it.patient_name}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-right tnum">{it.messages}</td>
                  <td className="px-3 py-2 text-right tnum">{it.segments}</td>
                  <td className="px-3 py-2 text-right tnum">{it.delivered}</td>
                  <td
                    className={
                      'px-3 py-2 text-right tnum ' + (it.failed > 0 ? 'font-bold text-red-700' : '')
                    }
                  >
                    {it.failed}
                  </td>
                  <td className="px-3 py-2">{formatJstDate(it.last_delivered_at)}</td>
                  <td className="px-3 py-2 text-right tnum">{it.told_by_phone}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
