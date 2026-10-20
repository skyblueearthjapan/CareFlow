'use client';

/**
 * 確定のあとに出す結果 (設計 §2-4): カイポケへの休みの送信と、勤務表の予定版。
 * 送信を始めたら進み具合 (送信のジョブ) を読み、始めなかったら理由を出す。
 * 予定版を作れなかったら理由と「作る」(作り直しの API の plan) を出す。
 */
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { OutboundJobProgress, useOutboundJobState } from '@/components/leave/LeaveOutboundDialog';
import type { LeaveConfirmResult } from '@/lib/schemas/leave';

export interface LeaveConfirmOutcomeProps {
  result: LeaveConfirmResult;
  onBuildPlan: () => void;
  onDismiss: () => void;
}

export function LeaveConfirmOutcome({ result, onBuildPlan, onDismiss }: LeaveConfirmOutcomeProps) {
  const outbound = result.outbound;
  const sheet = result.kinmu_sheet;
  const jobState = useOutboundJobState(outbound?.started ? (outbound.job_id ?? null) : null);

  return (
    <Card className="space-y-2 p-3 text-sm" data-testid="confirm-outcome">
      <div className="flex items-center justify-between gap-2">
        <b className="text-text-primary">確定したあとの処理</b>
        <Button type="button" size="sm" variant="ghost" onClick={onDismiss}>
          閉じる
        </Button>
      </div>
      {outbound &&
        (outbound.started ? (
          <div className="space-y-1">
            <p className="text-text-secondary">
              カイポケへ休みを {outbound.count} 件送っています。
            </p>
            <OutboundJobProgress state={jobState} />
          </div>
        ) : (
          <p className="text-text-secondary">
            カイポケへは送りませんでした{outbound.reason ? `：${outbound.reason}` : ''}
          </p>
        ))}
      {sheet &&
        (sheet.created ? (
          <p className="text-text-secondary">
            勤務表の予定版を作りました
            {sheet.sheet?.url && (
              <>
                {' '}
                <a
                  href={sheet.sheet.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-brand-primary underline-offset-2 hover:underline"
                >
                  開く
                </a>
              </>
            )}
          </p>
        ) : (
          <div className="flex flex-wrap items-center gap-2" role="alert">
            <p className="text-warning-strong">
              勤務表の予定版を作れませんでした{sheet.reason ? `：${sheet.reason}` : ''}
            </p>
            <Button type="button" size="sm" variant="outline" onClick={onBuildPlan}>
              作る
            </Button>
          </div>
        ))}
    </Card>
  );
}
