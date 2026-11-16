'use client';

/**
 * お知らせの記録 — 「月のまとめ」(設計 §5-4 の 3・決まり §6〜§7)。
 * 金額は見込み (届いた分の通数 × 11 円)。確かな額は KDDI の請求書で確かめる。
 * 送らずに済ませたもの (skipped) があるときは「作ったお知らせ N 件（うち送っていないもの M 件）」と出し、
 * 「送った件数」と食い違って見えないようにする。
 * 窓口が模擬 (status.provider === "mock") のときは、実際には送っていないと 1 行添える (決まり §13-4)。
 */
import { useState, type ReactNode } from 'react';
import { Download, Loader2 } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { useDownloadSmsSummaryExcel, useSmsStatus, useSmsSummary } from '@/lib/queries/sms';

import {
  SMS_CHANNEL_LABEL,
  SMS_SEGMENTS_LABEL,
  currentJstMonth,
  formatYen,
  formatYmdShort,
  smsCarrierLabel,
  smsKindLabel,
} from '../smsLabels';
import { MonthPicker } from './SmsPatientsTab';

function Tile({
  label,
  value,
  note,
  testId,
}: {
  label: string;
  value: ReactNode;
  note?: string;
  testId?: string;
}) {
  return (
    <div className="rounded-md border border-border-default p-3" data-testid={testId}>
      <p className="text-xs text-text-secondary">{label}</p>
      <p className="text-xl font-bold text-text-primary tnum">{value}</p>
      {note ? <p className="text-xs text-text-secondary">{note}</p> : null}
    </div>
  );
}

function SmallTable({
  title,
  head,
  rows,
  empty = 'ありません',
}: {
  title: string;
  head: string[];
  rows: ReactNode[][];
  empty?: string;
}) {
  return (
    <section className="space-y-1">
      <h3 className="text-base font-bold text-text-primary">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-sm text-text-secondary">{empty}</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border-default">
          <table className="w-full text-sm">
            <thead className="whitespace-nowrap bg-bg-muted text-left text-text-secondary">
              <tr>
                {head.map((h, i) => (
                  <th key={h} className={'px-3 py-1.5 ' + (i > 0 ? 'text-right' : '')}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border-default">
              {rows.map((r, i) => (
                <tr key={i}>
                  {r.map((c, j) => (
                    <td
                      key={j}
                      className={
                        'whitespace-nowrap px-3 py-1.5 ' + (j > 0 ? 'text-right tnum' : '')
                      }
                    >
                      {c}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function SmsSummaryTab() {
  const [month, setMonth] = useState(currentJstMonth());
  const q = useSmsSummary(month, true);
  const dl = useDownloadSmsSummaryExcel();
  const smsStatus = useSmsStatus(true);
  const s = q.data;

  const handleExport = async () => {
    if (dl.isPending) return;
    try {
      const blob = await dl.mutateAsync(month);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `お知らせの記録_${month}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (e) {
      toast.error(`Excel を出せませんでした: ${apiErrorMessage(e)}`);
    }
  };

  const rate =
    s?.totals.delivered_rate == null ? '—' : `${Math.round(s.totals.delivered_rate * 1000) / 10}%`;

  return (
    <div className="space-y-4" data-testid="sms-summary-tab">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <MonthPicker value={month} onChange={setMonth} />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void handleExport()}
          disabled={dl.isPending}
          data-testid="sms-summary-export"
        >
          {dl.isPending ? (
            <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <Download className="mr-1 h-4 w-4" aria-hidden />
          )}
          Excel に出す
        </Button>
      </div>

      {smsStatus.data?.provider === 'mock' ? (
        <p
          className="rounded-md border border-amber-300 bg-amber-50 p-2 text-sm text-amber-950"
          data-testid="sms-summary-mock-note"
        >
          いまは模擬の窓口です。記録だけで、実際には送っていません。
        </p>
      ) : null}

      {q.isError ? (
        <p className="text-sm text-red-700">読み込めませんでした: {apiErrorMessage(q.error)}</p>
      ) : !s ? (
        <p className="text-sm text-text-secondary">読み込んでいます…</p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 2xl:grid-cols-6">
            {s.totals.skipped > 0 ? (
              <Tile
                label="作ったお知らせ"
                value={`${s.totals.messages} 件`}
                note={`うち送っていないもの ${s.totals.skipped} 件`}
                testId="sms-summary-messages"
              />
            ) : (
              <Tile
                label="送った件数"
                value={`${s.totals.messages} 件`}
                testId="sms-summary-messages"
              />
            )}
            <Tile label="届いた割合" value={rate} note={`届いた ${s.totals.delivered} 件`} />
            <Tile label="届かなかった" value={`${s.totals.failed} 件`} />
            <Tile label="分からない・まだ" value={`${s.totals.unknown + s.totals.pending} 件`} />
            <Tile label="通数の合計" value={`${s.totals.segments} 通分`} />
            <Tile
              label="見込みの金額（届いた分）"
              value={formatYen(s.totals.est_yen)}
              note="確かな金額は請求書で確かめてください"
              testId="sms-summary-yen"
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <SmallTable
              title="種類別"
              head={['種類', '件数', '通数', '見込みの金額']}
              rows={s.by_kind.map((r) => [
                smsKindLabel(r.kind),
                r.messages,
                r.segments,
                formatYen(r.est_yen),
              ])}
            />
            <SmallTable
              title="キャリア別"
              head={['キャリア', '件数', '届いた', '届かなかった']}
              rows={s.by_carrier.map((r) => [
                smsCarrierLabel(r.carrier),
                r.messages,
                r.delivered,
                r.failed,
              ])}
            />
            <SmallTable
              title="届かなかった理由"
              head={['理由', '件数']}
              rows={s.by_fail_reason.map((r) => [r.label || '理由は分かりません', r.count])}
            />
            <SmallTable
              title="通数の分かれ方"
              head={['1 件あたり', '件数']}
              rows={s.by_segments.map((r) => [
                SMS_SEGMENTS_LABEL[r.segments] ?? `${r.segments} 通分`,
                r.messages,
              ])}
            />
            <section className="space-y-1">
              <h3 className="text-base font-bold text-text-primary">変更のお知らせ</h3>
              <p className="text-sm text-text-primary">
                {s.changes.count} 件（うち急ぎ {s.changes.urgent_count} 件）・
                気づいてからお伝えするまで{' '}
                {s.changes.median_minutes_to_tell == null
                  ? '—'
                  : `およそ ${Math.round(s.changes.median_minutes_to_tell)} 分（真ん中の値）`}
              </p>
              <ul className="flex flex-wrap gap-3 text-sm text-text-secondary">
                {s.changes.by_channel.map((c) => (
                  <li key={c.channel}>
                    {SMS_CHANNEL_LABEL[c.channel] ?? 'そのほか'}: {c.count} 件
                  </li>
                ))}
              </ul>
            </section>
            <SmallTable
              title="週ごと"
              head={['週の始まり', '件数', '届いた', '届かなかった']}
              rows={s.by_week.map((r) => [
                formatYmdShort(r.week_start),
                r.messages,
                r.delivered,
                r.failed,
              ])}
            />
          </div>
        </>
      )}
    </div>
  );
}
