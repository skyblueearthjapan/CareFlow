'use client';

/**
 * お知らせの記録 — 1 通の詳細 (設計 §5-4 の 1)。
 * 本文・経過 (受け付けた → 届いた)・届かなかった理由・KDDI へ問い合わせるときの番号・番号の全桁。
 * 管理者だけの画面。中身はその場で見せるだけで、どこにも保存しない。
 */
import type { ReactNode } from 'react';

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { useSmsMessageDetail } from '@/lib/queries/sms';

import {
  SMS_MOCK_LABEL,
  formatJstDateTime,
  formatPhone,
  smsCarrierLabel,
  smsKindLabel,
  smsStatusLabel,
  smsStatusTone,
  smsStatusWithReason,
} from './smsLabels';

export interface SmsMessageDetailDialogProps {
  messageId: string | null;
  onClose: () => void;
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[9rem_1fr] gap-2 py-1.5 text-sm">
      <dt className="text-text-secondary">{label}</dt>
      <dd className="break-all text-text-primary">{children}</dd>
    </div>
  );
}

export function SmsMessageDetailDialog({ messageId, onClose }: SmsMessageDetailDialogProps) {
  const q = useSmsMessageDetail(messageId);
  const m = q.data;

  return (
    <Dialog open={messageId !== null} onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent className="max-w-3xl" data-testid="sms-message-detail">
        <DialogHeader>
          <DialogTitle>お知らせの詳細</DialogTitle>
          <DialogDescription className="text-sm">
            {m ? `${m.patient_name ?? '—'} 様・${smsKindLabel(m.kind)}` : '読み込んでいます…'}
          </DialogDescription>
        </DialogHeader>
        {q.isError ? (
          <p className="text-sm text-red-700">読み込めませんでした: {apiErrorMessage(q.error)}</p>
        ) : !m ? (
          <p className="text-sm text-text-secondary">読み込んでいます…</p>
        ) : (
          <div className="space-y-4">
            <dl className="divide-y divide-border-default">
              <Row label="状態">
                <span
                  className={`rounded-full border px-2 py-0.5 text-xs ${smsStatusTone(m.status)}`}
                >
                  {smsStatusWithReason(m.status, m.skip_reason)}
                </span>
                {m.provider === 'mock' ? (
                  <span
                    className="ml-1 rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs text-amber-900"
                    data-testid="sms-message-detail-mock"
                  >
                    {SMS_MOCK_LABEL}
                  </span>
                ) : null}
              </Row>
              <Row label="送り先">
                {m.relation_label ?? '—'}・{formatPhone(m.phone)}
              </Row>
              <Row label="作った日時">{formatJstDateTime(m.created_at)}</Row>
              <Row label="送った人">{m.created_by_name ?? '—'}</Row>
              <Row label="文字数・通数">
                {m.char_count} 文字・見込み {m.segments_est} 通分
                {m.split_num != null ? `（実際 ${m.split_num} 通分）` : ''}
              </Row>
              <Row label="キャリア">{smsCarrierLabel(m.carrier)}</Row>
              {m.status === 'failed' || m.fail_reason_raw || m.error_message ? (
                <Row label="届かなかった理由">
                  {m.error_message ?? '理由は分かりません'}
                  {m.fail_reason_raw ? (
                    <span className="ml-1 text-xs text-text-secondary">
                      （KDDI の値: {m.fail_reason_raw}）
                    </span>
                  ) : null}
                </Row>
              ) : null}
              <Row label="問い合わせ用の番号">
                <span className="font-mono text-xs">
                  やりとりの番号: {m.transaction_id ?? '—'}
                  <br />
                  受付の番号: {m.provider_msg_id ?? '—'}
                </span>
              </Row>
            </dl>

            <section className="space-y-1">
              <h3 className="text-base font-bold text-text-primary">本文</h3>
              <pre className="whitespace-pre-wrap break-words rounded border border-border-default bg-bg-muted/50 p-2 font-mono text-sm leading-relaxed text-text-primary">
                {m.body ?? '（本文は保存の期間を過ぎたため消しました）'}
              </pre>
            </section>

            <section className="space-y-1">
              <h3 className="text-base font-bold text-text-primary">経過</h3>
              {m.events.length === 0 ? (
                <p className="text-sm text-text-secondary">まだ記録はありません</p>
              ) : (
                <ol className="space-y-1 text-sm" data-testid="sms-message-events">
                  {m.events.map((e, i) => (
                    <li key={i} className="flex flex-wrap gap-x-3">
                      <span className="tnum text-text-secondary">{formatJstDateTime(e.at)}</span>
                      <span className="text-text-primary">{smsStatusLabel(e.status)}</span>
                      {e.detail ? <span className="text-text-secondary">{e.detail}</span> : null}
                    </li>
                  ))}
                </ol>
              )}
            </section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
