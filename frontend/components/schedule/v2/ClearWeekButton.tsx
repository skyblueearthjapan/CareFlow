'use client';

/**
 * ClearWeekButton — 「この週を空にする」 (青ピン廃止 BP-4 / PO 2026-10-12)。
 *
 * 打刻・実績・サインのある訪問と取消の記録だけを残し、それ以外の予定をすべて消す。
 * 押すと dry_run で件数を取り、確認ダイアログに「消す N 件・うちカイポケへ送った M 件・
 * 残す K 件」を落ち着いた文で出す。実行後は「元に戻す」リンクを出す。
 * 型から作り直したいときは「空にする」→「週を生成」。
 */
import { useMemo, useState } from 'react';
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
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { mondayOfIsoWeek } from '@/lib/format/isoWeek';
import { useInboundSnapshots } from '@/lib/queries/integrations';
import { useClearWeek, useUndoClearWeek, type ClearWeekResult } from '@/lib/queries/week_clear';

export interface ClearWeekButtonProps {
  canEdit: boolean;
  isoYear: number;
  isoWeek: number;
}

/** リロード後も「元に戻す」を出す期間。 */
const UNDO_WINDOW_MS = 24 * 60 * 60 * 1000;

export function ClearWeekButton({ canEdit, isoYear, isoWeek }: ClearWeekButtonProps) {
  const [preview, setPreview] = useState<ClearWeekResult | null>(null);
  const [undoId, setUndoId] = useState<string | null>(null);
  const clear = useClearWeek();
  const undo = useUndoClearWeek();
  const busy = clear.isPending || undo.isPending;
  const weekStart = useMemo(
    () => mondayOfIsoWeek(isoYear, isoWeek).toISOString().slice(0, 10),
    [isoYear, isoWeek],
  );
  const snapshots = useInboundSnapshots(weekStart);
  // 画面を開き直しても戻せるよう、この週の直近の「空にする」を保存から探す。
  const latestClear = snapshots.data?.snapshots.find(
    (s) =>
      s.kind === 'clear_week' &&
      !s.restoredAt &&
      Date.now() - new Date(s.createdAt).getTime() < UNDO_WINDOW_MS,
  );
  const effectiveUndoId = undoId ?? latestClear?.id ?? null;

  const handleClick = async () => {
    if (busy) return;
    try {
      const res = await clear.mutateAsync({ isoYear, isoWeek, dryRun: true });
      if (res.to_delete === 0) {
        toast.info('消す予定はありません');
        return;
      }
      setPreview(res);
    } catch (e) {
      toast.error(`件数の取得に失敗しました: ${apiErrorMessage(e)}`);
    }
  };

  const handleConfirm = async () => {
    try {
      const res = await clear.mutateAsync({ isoYear, isoWeek, dryRun: false });
      toast.success(`この週の予定 ${res.to_delete} 件を消しました`);
      setUndoId(res.snapshot_id ?? null);
      setPreview(null);
    } catch (e) {
      toast.error(`この週を空にできませんでした: ${apiErrorMessage(e)}`);
    }
  };

  const handleUndo = async () => {
    if (!effectiveUndoId) return;
    try {
      await undo.mutateAsync({ isoYear, isoWeek, snapshotId: effectiveUndoId });
      toast.success('元に戻しました');
      setUndoId(null);
    } catch (e) {
      toast.error(`元に戻せませんでした: ${apiErrorMessage(e)}`);
    }
  };

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => void handleClick()}
        disabled={!canEdit || busy}
        data-testid="clear-week-button"
      >
        {clear.isPending && preview === null ? (
          <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
        ) : null}
        この週を空にする
      </Button>
      {effectiveUndoId ? (
        <button
          type="button"
          className="text-[12px] text-brand-primary underline underline-offset-2 disabled:opacity-50"
          onClick={() => void handleUndo()}
          disabled={busy}
          data-testid="clear-week-undo"
        >
          元に戻す
        </button>
      ) : null}

      <Dialog
        open={preview !== null}
        onOpenChange={(o) => (!o && !busy ? setPreview(null) : undefined)}
      >
        <DialogContent className="max-w-md" data-testid="clear-week-dialog">
          <DialogHeader>
            <DialogTitle>この週を空にしますか？</DialogTitle>
            <DialogDescription data-testid="clear-week-text">
              {preview ? (
                <>
                  この週の予定 {preview.to_delete} 件を消します。
                  {preview.kaipoke_linked > 0
                    ? `うちカイポケへ送った訪問 ${preview.kaipoke_linked} 件は、次の送信で削除になります`
                    : null}
                  （残す {preview.kept_checked_in} 件: 打刻・実績あり）
                </>
              ) : null}
            </DialogDescription>
            <p className="text-sm text-text-secondary">
              消す前の状態は自動で保存され、あとから「元に戻す」で戻せます。
            </p>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setPreview(null)}
              disabled={busy}
              data-testid="clear-week-cancel"
            >
              キャンセル
            </Button>
            <Button
              type="button"
              onClick={() => void handleConfirm()}
              disabled={busy}
              data-testid="clear-week-confirm"
            >
              {clear.isPending ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
              ) : null}
              空にする
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
