'use client';

/**
 * Edit dialog for the staff weekly-shift table (7 weekday rows).
 *
 * Submits one bulk PUT (`useUpdateStaffShifts`) and relies on the hook's
 * optimistic update for instant feedback. Validation happens via zod
 * (`shiftsBulkSchema`) — every is_on=true row needs start_time + end_time
 * with start < end.
 *
 * Naming: kept distinct from F5-Frontend-B's EventAddDialog / MentorAssignDialog
 * to avoid collisions in `staff/[id]/_components/`.
 */
import { useEffect, useState, type FormEvent } from 'react';

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
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { toast } from '@/components/ui/sonner';
import { useUpdateStaffShifts } from '@/lib/queries/staff-shifts';
import { WEEKDAY_LABELS } from '@/lib/schemas/staff';
import {
  buildEmptyShifts,
  shiftsBulkSchema,
  type StaffShiftItem,
} from '@/lib/schemas/staff-shifts';

interface ShiftsEditDialogProps {
  open: boolean;
  staffId: string;
  initialShifts: StaffShiftItem[] | undefined;
  onOpenChange: (next: boolean) => void;
}

/** 出勤ON時の既定勤務時間 (PO 要望 2026-08-22: 9:00〜18:00)。 */
export const DEFAULT_SHIFT_START = '09:00';
export const DEFAULT_SHIFT_END = '18:00';

const FULL_WEEKDAY_LABELS = [
  '月曜日',
  '火曜日',
  '水曜日',
  '木曜日',
  '金曜日',
  '土曜日',
  '日曜日',
] as const;

function normalize(initial: StaffShiftItem[] | undefined): StaffShiftItem[] {
  if (!initial || initial.length === 0) return buildEmptyShifts();
  // Sort by weekday so the table is deterministic regardless of API order.
  const byWeekday = new Map<number, StaffShiftItem>();
  initial.forEach((s) => byWeekday.set(s.weekday, s));
  return Array.from(
    { length: 7 },
    (_, weekday) =>
      byWeekday.get(weekday) ?? {
        weekday,
        is_on: false,
        start_time: null,
        end_time: null,
      },
  );
}

export function ShiftsEditDialog({
  open,
  staffId,
  initialShifts,
  onOpenChange,
}: ShiftsEditDialogProps) {
  const [rows, setRows] = useState<StaffShiftItem[]>(() => normalize(initialShifts));
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [formError, setFormError] = useState<string | null>(null);

  const mutation = useUpdateStaffShifts(staffId);

  // Reset local state whenever the dialog opens or fresh data arrives.
  useEffect(() => {
    if (open) {
      setRows(normalize(initialShifts));
      setErrors({});
      setFormError(null);
    }
  }, [open, initialShifts]);

  /** 月〜金 / 月〜土 を標準時間(09:00-18:00)で一括 ON。既に時刻が入っている日は保持。 */
  const applyPreset = (lastWeekday: 4 | 5) => {
    setRows((prev) =>
      prev.map((row) =>
        row.weekday <= lastWeekday
          ? {
              ...row,
              is_on: true,
              start_time: row.start_time ?? DEFAULT_SHIFT_START,
              end_time: row.end_time ?? DEFAULT_SHIFT_END,
            }
          : row,
      ),
    );
  };

  const updateRow = (idx: number, patch: Partial<StaffShiftItem>) => {
    setRows((prev) => {
      const next = [...prev];
      const current = next[idx];
      if (!current) return prev;
      const merged: StaffShiftItem = { ...current, ...patch };
      // Clear time fields when toggled off so the payload stays tidy.
      if (patch.is_on === false) {
        merged.start_time = null;
        merged.end_time = null;
      }
      // When toggled on, default to the standard working hours if blank
      // (09:00-18:00 — PO 要望 2026-08-22: 新規スタッフ登録時の手間削減).
      if (patch.is_on === true) {
        merged.start_time = merged.start_time ?? DEFAULT_SHIFT_START;
        merged.end_time = merged.end_time ?? DEFAULT_SHIFT_END;
      }
      next[idx] = merged;
      return next;
    });
  };

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setFormError(null);

    const parsed = shiftsBulkSchema.safeParse({ shifts: rows });
    if (!parsed.success) {
      const next: Record<number, string> = {};
      for (const issue of parsed.error.issues) {
        // path: ['shifts', idx, 'start_time' | 'end_time' | ...]
        const idx = typeof issue.path[1] === 'number' ? issue.path[1] : null;
        if (idx !== null) {
          next[idx] = issue.message;
        } else {
          setFormError(issue.message);
        }
      }
      setErrors(next);
      return;
    }

    try {
      await mutation.mutateAsync(parsed.data.shifts);
      toast.success('週間シフトを更新しました');
      onOpenChange(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '不明なエラー';
      setFormError(msg);
      toast.error(`更新に失敗しました: ${msg}`);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>週間シフトを編集</DialogTitle>
          <DialogDescription>
            勤務日のスイッチをオンにし、開始 / 終了時刻 (HH:MM) を入力してください。 7
            日分まとめて保存します。
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          {formError && (
            <Alert variant="destructive">
              <AlertTitle>入力エラー</AlertTitle>
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          )}

          <div className="flex flex-wrap items-center gap-2 text-xs text-text-secondary">
            <span>一括設定:</span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => applyPreset(4)}
              data-testid="shifts-preset-weekdays"
            >
              月〜金 {DEFAULT_SHIFT_START}〜{DEFAULT_SHIFT_END}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => applyPreset(5)}
              data-testid="shifts-preset-mon-sat"
            >
              月〜土 {DEFAULT_SHIFT_START}〜{DEFAULT_SHIFT_END}
            </Button>
            <span className="text-text-muted">（時刻は後から個別に変更できます）</span>
          </div>

          <table className="w-full text-sm">
            <thead className="border-b border-border-default text-left text-text-secondary">
              <tr>
                <th className="px-3 py-2 font-medium">曜日</th>
                <th className="px-3 py-2 font-medium">勤務</th>
                <th className="px-3 py-2 font-medium">開始</th>
                <th className="px-3 py-2 font-medium">終了</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, idx) => {
                const rowError = errors[idx];
                return (
                  <tr
                    key={row.weekday}
                    className="border-b border-border-default last:border-0 align-top"
                  >
                    <td className="px-3 py-2 font-medium">{WEEKDAY_LABELS[row.weekday]}</td>
                    <td className="px-3 py-2">
                      <Switch
                        checked={row.is_on}
                        onCheckedChange={(v) => updateRow(idx, { is_on: v })}
                        aria-label={`${FULL_WEEKDAY_LABELS[row.weekday]}を勤務日に設定`}
                      />
                    </td>
                    <td className="px-3 py-2">
                      <Input
                        type="time"
                        value={row.start_time ?? ''}
                        onChange={(e) =>
                          updateRow(idx, {
                            start_time: e.target.value || null,
                          })
                        }
                        disabled={!row.is_on}
                        aria-label={`${FULL_WEEKDAY_LABELS[row.weekday]}の開始時刻`}
                        className="w-32"
                      />
                    </td>
                    <td className="px-3 py-2">
                      <Input
                        type="time"
                        value={row.end_time ?? ''}
                        onChange={(e) =>
                          updateRow(idx, {
                            end_time: e.target.value || null,
                          })
                        }
                        disabled={!row.is_on}
                        aria-label={`${FULL_WEEKDAY_LABELS[row.weekday]}の終了時刻`}
                        className="w-32"
                      />
                      {rowError && <p className="mt-1 text-xs text-red-600">{rowError}</p>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={mutation.isPending}
            >
              キャンセル
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? '保存中…' : '保存する'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
