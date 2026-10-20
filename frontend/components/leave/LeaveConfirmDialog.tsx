'use client';

/**
 * 「この月を確定して通知」のダイアログ (設計 §5・モックの確定のダイアログ)。
 *
 * 申請中の残り・休日数が足りない人・勤務すべき時間数 (未入力なら入力欄・前の月の値が初期値) を出し、
 * 「このまま確定する」も選べる。時間数を変えたら先に保存してから確定する。
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
import { Input } from '@/components/ui/input';
import { fmtMonthJp, fmtNum } from '@/lib/leave-sheet';
import type { LeaveMonthView } from '@/lib/schemas/leave';

export interface LeaveConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  month: string;
  view: LeaveMonthView;
  busy: boolean;
  /** requiredHours: 入力が変わっていれば保存する値 (変わっていなければ undefined)。 */
  onConfirm: (requiredHours: number | null | undefined) => void;
}

const MAX_HOURS = 744;

export function LeaveConfirmDialog({
  open,
  onOpenChange,
  month,
  view,
  busy,
  onConfirm,
}: LeaveConfirmDialogProps) {
  const initialHours = view.required_hours ?? view.previous_required_hours ?? null;
  const [hours, setHours] = useState(initialHours == null ? '' : String(initialHours));
  useEffect(() => {
    if (open) setHours(initialHours == null ? '' : String(initialHours));
  }, [open, initialHours]);

  const pending = view.pending.length;
  const shorts = view.staff.filter((s) => s.is_full_time && (s.shortfall ?? 0) > 0);
  const notCompared = !view.compare.last_compared_at;

  const trimmed = hours.trim();
  const parsed = trimmed === '' ? null : Number(trimmed);
  const hoursInvalid =
    parsed !== null && (!Number.isFinite(parsed) || parsed < 0 || parsed > MAX_HOURS);
  const hoursChanged = parsed !== (view.required_hours ?? null);
  const warn = pending > 0 || shorts.length > 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{fmtMonthJp(month)}を確定して通知</DialogTitle>
          <DialogDescription>
            確定すると、職員全員のスマホへ{fmtMonthJp(month)}の確定の知らせが届きます。
            確定した後も表で変えられます（変えたらもう一度確定して通知してください）。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 text-sm">
          {pending > 0 && (
            <div className="rounded-md bg-warning-bg px-3 py-2 text-warning-strong" role="alert">
              申請中が {pending} 件残っています。確定しても申請中のまま残ります。
            </div>
          )}
          {shorts.length > 0 && (
            <div className="rounded-md bg-warning-bg px-3 py-2 text-warning-strong" role="alert">
              休日が足りない人：
              {shorts.map((s) => `${s.name}（あと ${fmtNum(s.shortfall ?? 0)} 日）`).join('、')}
            </div>
          )}
          {!warn && (
            <div className="rounded-md bg-bg-muted px-3 py-2 text-text-secondary">
              申請はすべて処理済みで、全員の休日数が足りています。
            </div>
          )}
          {notCompared && (
            <p className="text-text-secondary">
              まだカイポケと比べていないので、休みはカイポケへ送りません。
            </p>
          )}
          <div className="space-y-1">
            <label htmlFor="leave-required-hours" className="block text-text-primary">
              常勤の従業者が勤務すべき時間数（時間）
            </label>
            <Input
              id="leave-required-hours"
              type="number"
              inputMode="decimal"
              min={0}
              max={MAX_HOURS}
              step={0.5}
              value={hours}
              onChange={(e) => setHours(e.target.value)}
              className="max-w-[160px]"
              aria-invalid={hoursInvalid}
            />
            <p className="text-xs text-text-muted">
              {view.required_hours == null
                ? view.previous_required_hours != null
                  ? `まだ入っていません。前の月の値（${fmtNum(view.previous_required_hours)} 時間）を入れてあります。`
                  : 'まだ入っていません。勤務表の右上に出ます。'
                : '勤務表の右上に出ます。'}
            </p>
            {hoursInvalid && (
              <p className="text-xs text-error">0〜{MAX_HOURS} の数を入れてください</p>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            戻って調整する
          </Button>
          <Button
            type="button"
            disabled={busy || hoursInvalid}
            onClick={() => onConfirm(hoursChanged ? parsed : undefined)}
          >
            {busy ? '確定しています…' : warn ? 'このまま確定する' : '確定して通知'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
