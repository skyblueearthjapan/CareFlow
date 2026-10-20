'use client';

/**
 * 勤務表 (Google スプレッドシート) を作り直す・未作成なら作る (設計 §7-2)。理由は必須。
 * 前の版は残る。確定版は実績を記録した月だけ (呼び出し側が押せなくする)。
 */
import { useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { toast } from '@/components/ui/sonner';
import { fmtMonthJp } from '@/lib/leave-sheet';
import { leaveErrorMessage, useRebuildKinmuSheet } from '@/lib/queries/leave';

const KIND_LABEL = { plan: '予定版', final: '確定版' } as const;

export interface KinmuRebuildDialogProps {
  /** 開く勤務表の種類 (null で閉じる)。 */
  kind: 'plan' | 'final' | null;
  onClose: () => void;
  month: string;
}

export function KinmuRebuildDialog({ kind, onClose, month }: KinmuRebuildDialogProps) {
  const rebuild = useRebuildKinmuSheet();
  const [reason, setReason] = useState('');
  useEffect(() => {
    if (kind) setReason('');
  }, [kind]);
  const label = kind ? KIND_LABEL[kind] : '';

  const submit = () => {
    if (!kind || !reason.trim()) return;
    rebuild
      .mutateAsync({ month, kind, reason: reason.trim() })
      .then((r) => {
        onClose();
        if (r.created) toast.success(`勤務表の${label}を作りました`);
        else
          toast.error(`勤務表の${label}を作れませんでした`, {
            description: r.reason ?? undefined,
          });
      })
      .catch((e: unknown) =>
        toast.error(leaveErrorMessage(e, `勤務表の${label}を作れませんでした`)),
      );
  };

  return (
    <Dialog
      open={kind !== null}
      onOpenChange={(o) => {
        // 作っている間は閉じない (Esc・外側・×)
        if (!o && !rebuild.isPending) onClose();
      }}
    >
      <DialogContent
        onEscapeKeyDown={(e) => rebuild.isPending && e.preventDefault()}
        onInteractOutside={(e) => rebuild.isPending && e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>
            {fmtMonthJp(month)}の勤務表（{label}）を作る
          </DialogTitle>
          <DialogDescription>
            新しい版として作ります。前の版は消えません。作る理由を入れてください（記録に残ります）。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1">
          <label htmlFor="kinmu-rebuild-reason" className="block text-sm text-text-primary">
            作る理由<span className="text-error">*</span>
          </label>
          <Textarea
            id="kinmu-rebuild-reason"
            rows={3}
            maxLength={500}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="例: Google ドライブにつないだので作る／休みを合わせたので作り直す"
          />
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} disabled={rebuild.isPending}>
            やめる
          </Button>
          <Button
            type="button"
            disabled={rebuild.isPending || reason.trim().length === 0}
            onClick={submit}
          >
            {rebuild.isPending ? '作っています…' : '作る'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
