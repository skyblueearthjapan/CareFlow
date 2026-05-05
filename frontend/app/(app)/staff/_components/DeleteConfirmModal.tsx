'use client';

/**
 * Tiny native-dialog–free confirm modal used by the staff detail page.
 *
 * No portal required — overlay just sits on top via fixed positioning.
 * Replace with a shared shadcn/ui Dialog once D5 lands.
 */
import { Button } from '@/components/ui/button';

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
  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md rounded-lg border border-border-default bg-bg-base p-5 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="font-serif text-lg font-bold text-text-primary">{title}</h2>
        {description && (
          <p className="mt-2 text-sm text-text-secondary">{description}</p>
        )}
        <div className="mt-4 flex justify-end gap-3">
          <Button variant="outline" onClick={onCancel} disabled={confirming}>
            キャンセル
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={confirming}>
            {confirming ? '削除中…' : '削除する'}
          </Button>
        </div>
      </div>
    </div>
  );
}
