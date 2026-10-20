'use client';

/**
 * 「申請をまとめて承認」のダイアログ (モックの上の「申請をまとめて承認 N」)。
 * 外したものは申請中のまま残る。却下は 1 件ずつ (理由が要るため)。
 */
import { useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { fmtDayJp, LEAVE_LABEL } from '@/lib/leave-sheet';
import type { LeavePendingItem } from '@/lib/schemas/leave';

export interface BulkApproveDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  items: LeavePendingItem[];
  busy: boolean;
  onApprove: (ids: string[]) => void;
}

export function BulkApproveDialog({
  open,
  onOpenChange,
  items,
  busy,
  onApprove,
}: BulkApproveDialogProps) {
  const [checked, setChecked] = useState<Set<string>>(() => new Set());
  // 開いた時点の一覧で固定する (読み直しで並びやチェックが変わらないように)
  const [snapshot, setSnapshot] = useState<LeavePendingItem[]>([]);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  useEffect(() => {
    if (!open) return;
    setSnapshot(itemsRef.current);
    setChecked(new Set(itemsRef.current.map((i) => i.id)));
  }, [open]);

  const sorted = [...snapshot].sort(
    (a, b) => a.date.localeCompare(b.date) || a.staff_order - b.staff_order,
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>申請をまとめて承認</DialogTitle>
          <DialogDescription>
            外したものは申請中のまま残ります。却下は 1
            件ずつ（理由が要るため）表のマスから行います。
          </DialogDescription>
        </DialogHeader>
        <ul className="space-y-2">
          {sorted.map((it) => {
            const id = `bulk-${it.id}`;
            return (
              <li
                key={it.id}
                className="flex items-start gap-3 rounded-md border border-border-default p-2 text-sm"
              >
                <input
                  id={id}
                  type="checkbox"
                  className="mt-1"
                  checked={checked.has(it.id)}
                  onChange={(e) =>
                    setChecked((prev) => {
                      const next = new Set(prev);
                      if (e.target.checked) next.add(it.id);
                      else next.delete(it.id);
                      return next;
                    })
                  }
                />
                <label htmlFor={id} className="min-w-0 flex-1">
                  <span className="font-bold">{it.staff_name}</span> {fmtDayJp(it.date)}{' '}
                  {LEAVE_LABEL[it.override_type]}
                  {it.kind === 'change' && '（変更のお願い）'}
                  <span className="block text-xs text-text-muted">
                    {it.note ? `メモ：${it.note} ・ ` : ''}
                    {it.past_day
                      ? '休みの記録だけ'
                      : it.move_visit_count > 0
                        ? `訪問 ${it.move_visit_count} 件を担当なしへ`
                        : '移す訪問なし'}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            やめる
          </Button>
          <Button
            type="button"
            disabled={busy || checked.size === 0}
            onClick={() => onApprove(sorted.filter((i) => checked.has(i.id)).map((i) => i.id))}
          >
            チェックした申請を承認（{checked.size} 件）
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
