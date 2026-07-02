'use client';

/**
 * DismissReasonDialog — 改善提案「見送り」の理由選択 + 可動域昇格確認ダイアログ (P2-C).
 *
 * フロー (設計 §3 / §1.2):
 *   1. 4 択ラジオで却下理由を選ぶ (その他は自由記述):
 *        day_immovable  この曜日は動かせない
 *        time_immovable この時刻は動かせない
 *        staff_relation スタッフとの関係
 *        other          その他 (+ 自由記述)
 *   2. reason=day_immovable / time_immovable のときのみ **昇格確認** を挟む:
 *        「◯曜の枠を[曜日固定/完全固定]として記録しますか？今後この種の提案は表示されません」
 *        - はい → promote_movability=true で POST (day_immovable→time_flexible /
 *                 time_immovable→locked を BE が反映).
 *        - いいえ → promote なし (promote_movability=false) で POST (却下のみ).
 *      staff_relation / other は昇格ステップを挟まず即 POST.
 *   3. 成功後は useDismissSuggestion が improvement-suggestions を invalidate してカード消滅.
 *
 * デザイン: Warm & Human トークンのみ.
 */
import * as React from 'react';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useDismissSuggestion } from '@/lib/queries/improvementSuggestions';
import type {
  ImprovementDismissReason,
  ImprovementSuggestion,
} from '@/lib/schemas/v2/improvementSuggestion';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

/** 4 択の表示ラベル (設計 §3). */
const REASON_OPTIONS: ReadonlyArray<{ value: ImprovementDismissReason; label: string }> = [
  { value: 'day_immovable', label: 'この曜日は動かせない' },
  { value: 'time_immovable', label: 'この時刻は動かせない' },
  { value: 'staff_relation', label: 'スタッフとの関係' },
  { value: 'other', label: 'その他' },
];

/** 昇格対象になる理由か (day_immovable / time_immovable のみ昇格確認を挟む). */
function isPromotable(reason: ImprovementDismissReason): boolean {
  return reason === 'day_immovable' || reason === 'time_immovable';
}

/** 昇格後の可動域ラベル (day_immovable→曜日固定 / time_immovable→完全固定). */
function promoteLabel(reason: ImprovementDismissReason): string {
  return reason === 'day_immovable' ? '曜日固定' : '完全固定';
}

export interface DismissReasonDialogProps {
  open: boolean;
  suggestion: ImprovementSuggestion | null;
  patientId: string;
  onClose: () => void;
  /** dismiss POST 成功後に親へ通知 (カードはキャッシュ invalidate で消えるが UI トリガに使う). */
  onDismissed?: () => void;
}

type Step = 'reason' | 'promote';

export function DismissReasonDialog({
  open,
  suggestion,
  patientId,
  onClose,
  onDismissed,
}: DismissReasonDialogProps) {
  const [step, setStep] = React.useState<Step>('reason');
  const [reason, setReason] = React.useState<ImprovementDismissReason>('day_immovable');
  const [note, setNote] = React.useState('');

  const dismissMut = useDismissSuggestion();

  // open のたびに初期化する.
  React.useEffect(() => {
    if (open) {
      setStep('reason');
      setReason('day_immovable');
      setNote('');
    }
  }, [open]);

  const doDismiss = React.useCallback(
    (promote: boolean) => {
      if (!suggestion) return;
      dismissMut.mutate(
        {
          patient_id: patientId,
          kind: suggestion.kind,
          target_weekday: suggestion.target_weekday,
          reason,
          reason_note: note.trim() ? note.trim() : null,
          promote_movability: promote,
        },
        {
          onSuccess: () => {
            toast.success('この提案を見送りました');
            onDismissed?.();
            onClose();
          },
          onError: () => toast.error('見送りの記録に失敗しました'),
        },
      );
    },
    [suggestion, patientId, reason, note, dismissMut, onDismissed, onClose],
  );

  const handleNext = React.useCallback(() => {
    // 昇格対象の理由は確認ステップへ. それ以外は即 POST (promote なし).
    if (isPromotable(reason)) {
      setStep('promote');
    } else {
      doDismiss(false);
    }
  }, [reason, doDismiss]);

  const weekdayLabel =
    suggestion != null ? (WEEKDAY_LABELS[suggestion.target_weekday] ?? '?') : '?';
  const isBusy = dismissMut.isPending;

  return (
    <Dialog open={open} onOpenChange={(o) => (!o && !isBusy ? onClose() : undefined)}>
      <DialogContent aria-describedby="dismiss-reason-desc" data-testid="dismiss-reason-dialog">
        <DialogHeader>
          <DialogTitle>
            {step === 'reason' ? '提案を見送る理由' : '可動域として記録しますか？'}
          </DialogTitle>
          <DialogDescription id="dismiss-reason-desc">
            {step === 'reason'
              ? '見送る理由を選んでください。今後の提案精度の向上に使われます。'
              : `${weekdayLabel}曜の枠を「${promoteLabel(reason)}」として記録すると、今後この種の提案は表示されません。`}
          </DialogDescription>
        </DialogHeader>

        {step === 'reason' ? (
          <div className="space-y-2" data-testid="dismiss-reason-options">
            {REASON_OPTIONS.map((opt) => (
              <label
                key={opt.value}
                className="flex items-center gap-2 rounded border border-border-default bg-bg-base px-3 py-2 text-sm text-text-primary"
              >
                <input
                  type="radio"
                  name="dismiss-reason"
                  value={opt.value}
                  checked={reason === opt.value}
                  onChange={() => setReason(opt.value)}
                  disabled={isBusy}
                  aria-label={opt.label}
                />
                <span>{opt.label}</span>
              </label>
            ))}
            {reason === 'other' ? (
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                disabled={isBusy}
                rows={2}
                placeholder="理由を記入してください (任意)"
                aria-label="見送り理由の自由記述"
                data-testid="dismiss-reason-note"
                className="w-full rounded border border-border-default bg-bg-base px-2 py-1 text-sm text-text-primary focus:border-brand-primary focus:outline-none"
              />
            ) : null}
          </div>
        ) : (
          <div
            className="rounded border border-warning/40 bg-warning/10 p-3 text-sm text-text-secondary"
            data-testid="dismiss-promote-confirm"
          >
            <p>
              <span className="font-medium text-text-primary">
                {weekdayLabel}曜の枠を「{promoteLabel(reason)}」
              </span>
              として記録します。今後この種の提案は表示されなくなります。
            </p>
          </div>
        )}

        <DialogFooter>
          {step === 'reason' ? (
            <>
              <Button type="button" variant="outline" onClick={onClose} disabled={isBusy}>
                キャンセル
              </Button>
              <Button
                type="button"
                onClick={handleNext}
                disabled={isBusy}
                data-testid="dismiss-reason-next"
              >
                {isBusy ? (
                  <>
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                    記録中…
                  </>
                ) : (
                  '見送る'
                )}
              </Button>
            </>
          ) : (
            <>
              <Button
                type="button"
                variant="outline"
                onClick={() => doDismiss(false)}
                disabled={isBusy}
                data-testid="dismiss-promote-no"
              >
                いいえ (今回のみ見送る)
              </Button>
              <Button
                type="button"
                onClick={() => doDismiss(true)}
                disabled={isBusy}
                data-testid="dismiss-promote-yes"
              >
                {isBusy ? (
                  <>
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                    記録中…
                  </>
                ) : (
                  `はい、${promoteLabel(reason)}として記録`
                )}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
