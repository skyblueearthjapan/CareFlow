'use client';

/**
 * 「事業所の休み」(年末年始など) を入れる・消すダイアログ (設計 §2-2・§5)。
 *
 * 事業所の休みは誰の休みにもならない (数えるのは必要休日数だけ)。休みにするときは
 * 「全員に公休を入れる」で、その日に勤務する人へ公休を入れる。
 */
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { fmtDayJp, fmtMonthJp } from '@/lib/leave-sheet';
import type { OfficeClosedDay } from '@/lib/schemas/leave';

export interface ClosedDaysDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  month: string;
  /** その月の初日と末日 'YYYY-MM-DD'。 */
  firstDay: string;
  lastDay: string;
  closedDays: OfficeClosedDay[];
  busy: boolean;
  /** その日に公休を入れる人の数 (勤務する曜日・休みも申請も無い人)。 */
  fillTargetCount: (date: string) => number;
  onAdd: (date: string, label: string) => void;
  onDelete: (date: string) => void;
  onFillAll: (date: string) => void;
}

export function ClosedDaysDialog({
  open,
  onOpenChange,
  month,
  firstDay,
  lastDay,
  closedDays,
  busy,
  fillTargetCount,
  onAdd,
  onDelete,
  onFillAll,
}: ClosedDaysDialogProps) {
  const [date, setDate] = useState('');
  const [label, setLabel] = useState('');
  const [fillFor, setFillFor] = useState<string | null>(null);

  const dateOk = date >= firstDay && date <= lastDay;
  const canAdd = dateOk && label.trim().length > 0 && !busy;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) setFillFor(null);
        onOpenChange(o);
      }}
    >
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{fmtMonthJp(month)}の事業所の休み</DialogTitle>
          <DialogDescription>
            年末年始など、事業所が休む日です。必要休日数に数えますが、それだけでは誰の休みにもなりません。
            休みにするときは「全員に公休を入れる」を押してください。
          </DialogDescription>
        </DialogHeader>

        {closedDays.length === 0 ? (
          <p className="text-sm text-text-secondary">この月の事業所の休みはありません。</p>
        ) : (
          <ul className="space-y-2">
            {closedDays.map((c) => {
              const targets = fillTargetCount(c.date);
              return (
                <li
                  key={c.date}
                  className="space-y-2 rounded-md border border-border-default p-3 text-sm"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      <b className="tnum">{fmtDayJp(c.date)}</b> {c.label}
                    </span>
                    <div className="flex gap-1">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={busy || targets === 0}
                        onClick={() => setFillFor(c.date)}
                      >
                        全員に公休を入れる
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        aria-label={`${fmtDayJp(c.date)} の事業所の休みを消す`}
                        onClick={() => onDelete(c.date)}
                      >
                        消す
                      </Button>
                    </div>
                  </div>
                  {fillFor === c.date && (
                    <div className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-warning-bg px-3 py-2 text-warning-strong">
                      <span>
                        {targets}{' '}
                        人に公休を入れます（勤務しない曜日の人・非常勤の人・休職中の人・申請中の人・休みが入っている人は除きます）
                      </span>
                      <div className="flex gap-1">
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          onClick={() => setFillFor(null)}
                        >
                          やめる
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          disabled={busy}
                          onClick={() => {
                            setFillFor(null);
                            onFillAll(c.date);
                          }}
                        >
                          入れる
                        </Button>
                      </div>
                    </div>
                  )}
                  {targets === 0 && (
                    <p className="text-xs text-text-muted">公休を入れる人はいません。</p>
                  )}
                </li>
              );
            })}
          </ul>
        )}

        <form
          className="flex flex-wrap items-end gap-2 border-t border-border-default pt-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (!canAdd) return;
            onAdd(date, label.trim());
            setDate('');
            setLabel('');
          }}
        >
          <label className="flex flex-col gap-1 text-sm text-text-secondary">
            日付
            <Input
              type="date"
              min={firstDay}
              max={lastDay}
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="w-[170px]"
              aria-label="事業所の休みの日付"
            />
          </label>
          <label className="flex min-w-[160px] flex-1 flex-col gap-1 text-sm text-text-secondary">
            名前
            <Input
              value={label}
              maxLength={64}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="例: 年末年始"
              aria-label="事業所の休みの名前"
            />
          </label>
          <Button type="submit" disabled={!canAdd}>
            入れる
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
