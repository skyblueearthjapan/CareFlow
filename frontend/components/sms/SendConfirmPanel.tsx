'use client';

/**
 * 送る前の確かめ (週のお知らせ・変更のお知らせで共通)。
 * 人数・通数・見込みの金額を言い直し、「送ったあとは取り消せません」と添える。
 * 二重に押されないよう、送っているあいだは両方のボタンを止める。
 * 開いたら「戻る」に合わせる (Enter で思わず送らないように)。
 */
import { useEffect, useRef } from 'react';
import { Loader2 } from 'lucide-react';

import { Button } from '@/components/ui/button';

import { formatYen } from './smsLabels';

export interface SendConfirmPanelProps {
  people: number;
  messages: number;
  segments: number;
  yen: number;
  pending: boolean;
  /** 一覧を読み込んでいるあいだなど、「送る」だけを止めるとき。 */
  blocked?: boolean;
  /** 送信済みの方 (同じ内容をもう一度送る方) の人数。 */
  resendCount?: number;
  /** 前回届かなかった方の人数。 */
  notDeliveredCount?: number;
  onConfirm: () => void;
  onBack: () => void;
  testId?: string;
}

export function SendConfirmPanel({
  people,
  messages,
  segments,
  yen,
  pending,
  blocked = false,
  resendCount = 0,
  notDeliveredCount = 0,
  onConfirm,
  onBack,
  testId = 'sms-send-confirm',
}: SendConfirmPanelProps) {
  const backRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    backRef.current?.focus();
  }, []);

  return (
    <div
      role="alertdialog"
      aria-labelledby={`${testId}-title`}
      className="space-y-3 rounded-md border-2 border-brand-primary bg-bg-base p-4"
      data-testid={testId}
    >
      <p id={`${testId}-title`} className="text-base font-bold text-text-primary">
        この内容で送りますか？
      </p>
      <dl className="grid grid-cols-3 gap-2 text-sm">
        <div className="rounded border border-border-default p-2">
          <dt className="text-xs text-text-secondary">人数</dt>
          <dd className="text-lg font-bold text-text-primary" data-testid={`${testId}-people`}>
            {people} 名
          </dd>
        </div>
        <div className="rounded border border-border-default p-2">
          <dt className="text-xs text-text-secondary">通数</dt>
          <dd className="text-lg font-bold text-text-primary" data-testid={`${testId}-messages`}>
            {messages} 件（{segments} 通分）
          </dd>
        </div>
        <div className="rounded border border-border-default p-2">
          <dt className="text-xs text-text-secondary">見込みの金額</dt>
          <dd className="text-lg font-bold text-text-primary" data-testid={`${testId}-yen`}>
            {formatYen(yen)}
          </dd>
        </div>
      </dl>
      {resendCount > 0 ? (
        <p className="text-sm font-bold text-amber-900" data-testid={`${testId}-resend`}>
          送信済みの方を {resendCount} 名ふくみます（もう一度送ります）
        </p>
      ) : null}
      {notDeliveredCount > 0 ? (
        <p className="text-sm font-bold text-amber-900" data-testid={`${testId}-not-delivered`}>
          前回届かなかった方を {notDeliveredCount} 名ふくみます
        </p>
      ) : null}
      <p className="text-sm font-bold text-red-700">送ったあとは取り消せません。</p>
      <div className="flex justify-end gap-2">
        <Button
          ref={backRef}
          type="button"
          variant="outline"
          onClick={onBack}
          disabled={pending}
          data-testid={`${testId}-back`}
        >
          戻る
        </Button>
        <Button
          type="button"
          onClick={onConfirm}
          disabled={pending || blocked}
          data-testid={`${testId}-ok`}
        >
          {pending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden /> : null}
          送る
        </Button>
      </div>
    </div>
  );
}
