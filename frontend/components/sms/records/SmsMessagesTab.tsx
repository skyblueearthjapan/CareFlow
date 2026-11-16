'use client';

/**
 * お知らせの記録 — 「送信の一覧」(設計 §5-4 の 1)。1 行 = 1 通。
 * 期間・状態・種類・キャリア・患者様で絞る (すべてサーバーで絞る・1 ページ 50 件)。
 * 状態の「届いていないもの」はサーバーへ `undelivered=true` で渡す。`status` はカンマで複数も渡せる。
 * 模擬の窓口 (provider=mock) の行には「模擬（実際には送っていません）」を添える (決まり §13-4)。
 */
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { usePatients } from '@/lib/queries/patients';
import { useSmsMessages } from '@/lib/queries/sms';

import { SmsMessageDetailDialog } from '../SmsMessageDetailDialog';
import {
  SMS_CARRIER_OPTIONS,
  SMS_KIND_LABEL,
  SMS_MOCK_LABEL,
  SMS_STATUS_OPTIONS,
  SMS_UNDELIVERED_FILTER,
  formatJstDateTime,
  smsCarrierLabel,
  smsKindLabel,
  smsStatusLabel,
  smsStatusTone,
  smsStatusWithReason,
} from '../smsLabels';

const PAGE_SIZE = 50;

export interface SmsMessagesFilterState {
  date_from: string;
  date_to: string;
  status: string;
  kind: string;
  carrier: string;
  patient_id: string;
}

const selectCls =
  'h-9 rounded-md border border-border-default bg-bg-base px-2 text-sm text-text-primary';

export function SmsMessagesTab({
  initialPatientId = '',
  initialStatus = '',
}: {
  initialPatientId?: string;
  /** 状態で絞って開くとき (`undelivered` = 届いていないもの・カンマ区切りの状態も可)。 */
  initialStatus?: string;
}) {
  const [filter, setFilter] = useState<SmsMessagesFilterState>({
    date_from: '',
    date_to: '',
    status: initialStatus,
    kind: '',
    carrier: '',
    patient_id: initialPatientId,
  });
  const [page, setPage] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
  const undelivered = filter.status === SMS_UNDELIVERED_FILTER;
  const q = useSmsMessages(
    {
      ...filter,
      status: undelivered ? '' : filter.status,
      undelivered,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    },
    true,
  );
  // URL で渡された、選択肢に無い状態 (カンマ区切りなど) も選んだままに見せる。
  const customStatus =
    filter.status !== '' &&
    filter.status !== SMS_UNDELIVERED_FILTER &&
    !(SMS_STATUS_OPTIONS as readonly string[]).includes(filter.status)
      ? filter.status
      : null;
  const patients = usePatients({ limit: 500 });

  const set = (k: keyof SmsMessagesFilterState, v: string) => {
    setFilter((f) => ({ ...f, [k]: v }));
    setPage(0);
  };

  const total = q.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-3" data-testid="sms-messages-tab">
      <div className="flex flex-wrap items-end gap-3 rounded-md border border-border-default p-3">
        <label className="flex flex-col gap-1 text-sm text-text-secondary">
          期間（から）
          <input
            type="date"
            className={selectCls}
            value={filter.date_from}
            onChange={(e) => set('date_from', e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-text-secondary">
          期間（まで）
          <input
            type="date"
            className={selectCls}
            value={filter.date_to}
            onChange={(e) => set('date_to', e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-text-secondary">
          状態
          <select
            className={selectCls}
            value={filter.status}
            onChange={(e) => set('status', e.target.value)}
            data-testid="sms-filter-status"
          >
            <option value="">すべて</option>
            <option value={SMS_UNDELIVERED_FILTER}>届いていないもの</option>
            {customStatus ? (
              <option value={customStatus}>
                {customStatus
                  .split(',')
                  .map((x) => smsStatusLabel(x.trim(), true))
                  .join('・')}
              </option>
            ) : null}
            {SMS_STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {smsStatusLabel(s, true)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm text-text-secondary">
          種類
          <select
            className={selectCls}
            value={filter.kind}
            onChange={(e) => set('kind', e.target.value)}
          >
            <option value="">すべて</option>
            {Object.entries(SMS_KIND_LABEL).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm text-text-secondary">
          キャリア
          <select
            className={selectCls}
            value={filter.carrier}
            onChange={(e) => set('carrier', e.target.value)}
          >
            <option value="">すべて</option>
            {SMS_CARRIER_OPTIONS.map((c) => (
              <option key={c} value={c}>
                {smsCarrierLabel(c)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm text-text-secondary">
          患者様
          <select
            className={`${selectCls} min-w-[12rem]`}
            value={filter.patient_id}
            onChange={(e) => set('patient_id', e.target.value)}
            data-testid="sms-filter-patient"
          >
            <option value="">すべて</option>
            {filter.patient_id &&
            !(patients.data?.items ?? []).some((p) => p.id === filter.patient_id) ? (
              <option value={filter.patient_id}>選んだ患者様</option>
            ) : null}
            {(patients.data?.items ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {q.isError ? (
        <p className="text-sm text-red-700">読み込めませんでした: {apiErrorMessage(q.error)}</p>
      ) : q.isLoading ? (
        <p className="text-sm text-text-secondary">読み込んでいます…</p>
      ) : (q.data?.items.length ?? 0) === 0 ? (
        <p className="rounded border border-border-default p-4 text-sm text-text-secondary">
          この条件のお知らせはありません。
        </p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border-default">
          <table className="w-full text-sm" data-testid="sms-messages-table">
            <thead className="bg-bg-muted text-left text-text-secondary">
              <tr>
                <th className="px-3 py-2">日時</th>
                <th className="px-3 py-2">患者様</th>
                <th className="px-3 py-2">送り先</th>
                <th className="px-3 py-2">種類</th>
                <th className="px-3 py-2">状態</th>
                <th className="px-3 py-2 text-right">通数</th>
                <th className="px-3 py-2">キャリア</th>
                <th className="px-3 py-2">送った人</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-default">
              {q.data?.items.map((m) => (
                <tr
                  key={m.id}
                  className="cursor-pointer hover:bg-bg-muted/60"
                  onClick={() => setOpenId(m.id)}
                  data-testid={`sms-message-row-${m.id}`}
                >
                  <td className="whitespace-nowrap px-3 py-2 tnum">
                    {formatJstDateTime(m.created_at)}
                  </td>
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      className="text-left font-bold text-text-primary underline-offset-2 hover:underline"
                      onClick={(e) => {
                        e.stopPropagation();
                        setOpenId(m.id);
                      }}
                    >
                      {m.patient_name ?? '—'}
                    </button>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">
                    {m.relation_label ?? '—'}
                    {m.phone_last4 ? `（末尾 ${m.phone_last4}）` : ''}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">{smsKindLabel(m.kind)}</td>
                  <td className="px-3 py-2">
                    <span
                      className={`whitespace-nowrap rounded-full border px-2 py-0.5 text-xs ${smsStatusTone(m.status)}`}
                    >
                      {smsStatusWithReason(m.status, m.skip_reason, true)}
                    </span>
                    {m.provider === 'mock' ? (
                      <span
                        className="ml-1 whitespace-nowrap rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs text-amber-900"
                        data-testid={`sms-message-mock-${m.id}`}
                      >
                        {SMS_MOCK_LABEL}
                      </span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 text-right tnum">{m.split_num ?? m.segments_est}</td>
                  <td className="whitespace-nowrap px-3 py-2">{smsCarrierLabel(m.carrier)}</td>
                  <td className="whitespace-nowrap px-3 py-2">{m.created_by_name ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex items-center justify-between text-sm text-text-secondary">
        <span className="tnum">
          全 {total} 件・{page + 1} / {pages} ページ
        </span>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
          >
            前へ
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => p + 1)}
            disabled={page + 1 >= pages}
          >
            次へ
          </Button>
        </div>
      </div>

      <SmsMessageDetailDialog messageId={openId} onClose={() => setOpenId(null)} />
    </div>
  );
}
