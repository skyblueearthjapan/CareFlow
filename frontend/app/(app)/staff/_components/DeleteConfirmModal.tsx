'use client';

/**
 * Confirm modal used by the staff detail page for soft-delete.
 *
 * Built on the shared `<Dialog>` primitive (W1-F) so we get Radix's focus
 * trap + Esc-to-close + scroll-lock for free instead of the hand-rolled
 * overlay we shipped initially.
 */
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

interface DeleteConfirmModalProps {
  open: boolean;
  title: string;
  description?: string;
  confirming?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function DeleteConfirmModal({
  open,
  title,
  description,
  confirming,
  onCancel,
  onConfirm,
}: DeleteConfirmModalProps) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Radix fires onOpenChange(false) for Esc, overlay click, and the
        // built-in close button — funnel them all through `onCancel`.
        if (!next) onCancel();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={confirming}>
            キャンセル
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={confirming}>
            {confirming ? '削除中…' : '削除する'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
