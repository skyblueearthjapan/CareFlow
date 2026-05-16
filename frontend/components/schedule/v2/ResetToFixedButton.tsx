'use client';

/**
 * ResetToFixedButton — Wave 41 v2 (固定枠に戻す, 機能 D).
 *
 * 仕様書: ``docs/plans/auto-schedule-v2.md`` v0.2 §6, §13.6
 *
 * 動作:
 *   1. 「📌 固定枠に戻す」クリック → 確認ダイアログ
 *   2. OK → POST /reset-to-fixed (対象週の visits を全部 patient_fixed_visits から再生成)
 *   3. トーストで結果通知 + visits/courses cache invalidate (mutation hook 側で実施)
 *
 * 粒度: 1 週間単位 (Q4 確定).
 * 注意: 現在の変更は失われる旨を確認ダイアログで明示する.
 */
import * as React from 'react';
import { Loader2, Pin } from 'lucide-react';
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
import { useResetToFixedMutation } from '@/lib/queries/autoScheduleV2';

import { formatErr } from './_autoScheduleUtils';

export interface ResetToFixedButtonProps {
  isoYear: number;
  isoWeek: number;
  officeId: string | null;
  /** 他ボタンと縦の高さを揃えるための size prop. */
  size?: 'sm' | 'md' | 'lg' | 'icon';
  /** 他の mutation 進行中で disable させたいときに使う. */
  disabled?: boolean;
}

export function ResetToFixedButton({
  isoYear,
  isoWeek,
  officeId,
  size = 'sm',
  disabled = false,
}: ResetToFixedButtonProps) {
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const resetMut = useResetToFixedMutation();

  const handleClick = () => {
    if (resetMut.isPending) return;
    setConfirmOpen(true);
  };

  const handleConfirm = async () => {
    try {
      const res = await resetMut.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        office_ids: officeId ? [officeId] : [],
        // W41 v2 final cross-review (M-Codex-1): backend が confirm=True 必須.
        confirm: true,
      });
      toast.success(`${res.visits_regenerated} 件の visits を再生成しました`);
      setConfirmOpen(false);
    } catch (err) {
      toast.error(`固定枠に戻すのに失敗しました: ${formatErr(err)}`);
    }
  };

  return (
    <>
      <Button
        type="button"
        size={size}
        variant="outline"
        onClick={handleClick}
        disabled={disabled || resetMut.isPending}
        data-testid="reset-to-fixed-button"
      >
        {resetMut.isPending ? (
          <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
        ) : (
          <Pin className="mr-1 h-4 w-4" aria-hidden />
        )}
        固定枠に戻す
      </Button>

      <Dialog
        open={confirmOpen}
        onOpenChange={(o) => {
          if (!o && !resetMut.isPending) setConfirmOpen(false);
        }}
      >
        <DialogContent className="max-w-md" data-testid="reset-to-fixed-confirm">
          <DialogHeader>
            <DialogTitle>固定枠に戻しますか？</DialogTitle>
            <DialogDescription>
              対象週のスケジュールを全患者の固定枠 (patient_fixed_visits) から再生成します。
              <br />
              <span className="font-semibold text-warning">
                現在の変更 (ドラッグ移動・自動算出による変更等) は失われます。
              </span>
              <br />
              よろしいですか？
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setConfirmOpen(false)}
              disabled={resetMut.isPending}
            >
              キャンセル
            </Button>
            <Button
              type="button"
              onClick={handleConfirm}
              disabled={resetMut.isPending}
              data-testid="reset-to-fixed-confirm-ok"
            >
              {resetMut.isPending ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <Pin className="mr-1 h-4 w-4" aria-hidden />
              )}
              固定枠に戻す
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
