'use client';

/**
 * Assign / change / clear the mentor for a staff member.
 *
 * Hooks:
 *   - `useStaffList()` for the searchable Combobox source.
 *   - `useUpdateMentor(staffId)` PUT (mentor_staff_id=null clears).
 *
 * Self-assignment is filtered out at the option level.
 */
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Combobox, type ComboboxOption } from '@/components/ui/combobox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useUpdateMentor } from '@/lib/queries/mentor';
import { useStaffList } from '@/lib/queries/staff';

interface MentorAssignDialogProps {
  staffId: string;
  /** Currently assigned mentor id (null/undefined = none). */
  currentMentorId?: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function MentorAssignDialog({
  staffId,
  currentMentorId,
  open,
  onOpenChange,
}: MentorAssignDialogProps) {
  const update = useUpdateMentor(staffId);
  const { data: staffList = [], isLoading } = useStaffList({ limit: 200 });
  const [selected, setSelected] = useState<string>('');

  // Bind / re-bind the combobox value whenever the dialog opens with a new
  // mentor id — guards against StrictMode double-mount drift too.
  useEffect(() => {
    if (open) setSelected(currentMentorId ?? '');
  }, [open, currentMentorId]);

  const options: ComboboxOption[] = useMemo(
    () =>
      staffList
        // Self cannot mentor self.
        .filter((s) => s.id !== staffId)
        // 在籍 (active) 以外 (休職 / 退職) は選択肢から除外
        .filter((s) => s.status === 'active')
        .map((s) => ({ value: s.id, label: s.name })),
    [staffList, staffId],
  );

  const isBusy = update.isPending;

  const onSave = async () => {
    try {
      await update.mutateAsync({
        mentor_staff_id: selected ? selected : null,
      });
      toast.success('同行スタッフを更新しました');
      onOpenChange(false);
    } catch (err) {
      toast.error(`更新に失敗しました: ${err instanceof Error ? err.message : '不明なエラー'}`);
    }
  };

  const onClear = async () => {
    try {
      await update.mutateAsync({ mentor_staff_id: null });
      toast.success('同行スタッフを解除しました');
      onOpenChange(false);
    } catch (err) {
      toast.error(`解除に失敗しました: ${err instanceof Error ? err.message : '不明なエラー'}`);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && isBusy) return;
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>同行スタッフを変更</DialogTitle>
          <DialogDescription>割り当てる同行スタッフを選択してください。</DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 py-2" role="group" aria-label="同行スタッフを選択">
          <Combobox
            options={options}
            value={selected}
            onChange={(v) => setSelected(v)}
            placeholder={isLoading ? '読み込み中…' : 'スタッフを検索して選択'}
            searchPlaceholder="氏名で検索…"
            emptyText="該当スタッフなし"
            disabled={isBusy || isLoading}
          />
        </div>

        <DialogFooter className="sm:justify-between">
          <Button
            type="button"
            variant="destructive"
            onClick={onClear}
            disabled={isBusy || !currentMentorId}
          >
            同行スタッフ解除
          </Button>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={isBusy}
            >
              キャンセル
            </Button>
            <Button
              type="button"
              onClick={onSave}
              disabled={isBusy || !selected || selected === currentMentorId}
            >
              {isBusy ? '保存中…' : '保存'}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
