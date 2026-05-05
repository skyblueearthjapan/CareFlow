'use client';

/**
 * Confirmation + result modal for the allocation engine run.
 *
 * Flow: opens with a "実行しますか？" confirm → on confirm, fires
 * `useRunAllocate` → swaps to a result panel showing assigned/unassigned
 * counts. `mapping_phase === 'minimal'` raises a warning callout because
 * the W1-G full mapping (areas / shifts / weekly patterns / NG staff /
 * lat-lng distance) hasn't landed yet, so results are advisory.
 */
import { format } from 'date-fns';
import { ja } from 'date-fns/locale';
import { toast } from 'sonner';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { ApiError } from '@/lib/api-client';
import { useRunAllocate, type AllocateResponse } from '@/lib/queries/allocate';
import { addDays } from '@/components/schedule/WeekSelector';

interface AllocateRunDialogProps {
  open: boolean;
  weekStart: Date;
  onClose: () => void;
}

function formatIsoDate(d: Date): string {
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

export function AllocateRunDialog({
  open,
  weekStart,
  onClose,
}: AllocateRunDialogProps) {
  const mutation = useRunAllocate();
  const result: AllocateResponse | undefined = mutation.data;
  const isPending = mutation.isPending;
  const weekEnd = addDays(weekStart, 6);

  const handleClose = () => {
    if (isPending) return;
    mutation.reset();
    onClose();
  };

  const handleRun = async () => {
    try {
      await mutation.mutateAsync(formatIsoDate(weekStart));
      toast.success('割当を実行しました');
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        toast.error(
          '短時間に実行が集中しています。1分後に再試行してください',
        );
        return;
      }
      toast.error(
        `割当に失敗しました: ${err instanceof Error ? err.message : '不明なエラー'}`,
      );
    }
  };

  const errorMessage = (() => {
    const err = mutation.error;
    if (!err) return null;
    if (err instanceof ApiError && err.status === 429) {
      return '短時間に実行が集中しています。1分後に再試行してください';
    }
    return err instanceof Error ? err.message : '不明なエラー';
  })();

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) handleClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>割当を実行</DialogTitle>
          <DialogDescription>
            {format(weekStart, 'yyyy年M月d日', { locale: ja })} 〜{' '}
            {format(weekEnd, 'M月d日', { locale: ja })} の訪問を再割当します。
          </DialogDescription>
        </DialogHeader>

        {errorMessage ? (
          <Alert variant="destructive">
            <AlertTitle>割当に失敗しました</AlertTitle>
            <AlertDescription>{errorMessage}</AlertDescription>
          </Alert>
        ) : null}

        {result ? (
          <div className="space-y-3 py-2">
            <div className="grid grid-cols-3 gap-3 text-center">
              <div className="rounded-md border border-border-default p-3">
                <div className="text-xs text-text-muted">合計</div>
                <div className="tnum text-2xl font-semibold text-text-primary">
                  {result.summary.total}
                </div>
              </div>
              <div className="rounded-md border border-border-default p-3">
                <div className="text-xs text-text-muted">割当済</div>
                <div className="tnum text-2xl font-semibold text-success">
                  {result.summary.assigned}
                </div>
              </div>
              <div className="rounded-md border border-border-default p-3">
                <div className="text-xs text-text-muted">未割当</div>
                <div className="tnum text-2xl font-semibold text-error">
                  {result.summary.unassigned}
                </div>
              </div>
            </div>
            {result.summary.mapping_phase === 'minimal' ? (
              <Alert variant="warning">
                <AlertTitle>暫定マッピングで実行されました</AlertTitle>
                <AlertDescription>
                  週パターン / NGスタッフ / 緯度経度などの高度判定は無効です
                  (Phase W1-G で完全マッピングに切替予定)。結果は参考値として
                  ご確認ください。
                </AlertDescription>
              </Alert>
            ) : null}
          </div>
        ) : (
          <p className="py-2 text-sm text-text-secondary">
            既存の訪問データを元に、配置エンジンを実行します。
          </p>
        )}

        <DialogFooter>
          {result ? (
            <Button type="button" onClick={handleClose}>
              閉じる
            </Button>
          ) : (
            <>
              <Button
                type="button"
                variant="outline"
                onClick={handleClose}
                disabled={isPending}
              >
                キャンセル
              </Button>
              <Button type="button" onClick={handleRun} disabled={isPending}>
                {isPending ? '実行中…' : '実行'}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
