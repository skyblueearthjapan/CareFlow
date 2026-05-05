'use client';

/**
 * Add dialog for staff overrides (休み / 時間変更 / 午前休 / 午後休).
 *
 * POSTs a single OverrideCreate payload via `useCreateOverride`. The hook
 * invalidates every range under ['staff-overrides', staffId] so the parent
 * list refreshes automatically.
 */
import type { FormEvent } from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/components/ui/sonner';
import { useCreateOverride } from '@/lib/queries/staff-overrides';

import {
  OverrideForm,
  emptyOverrideForm,
  useOverrideFormState,
  validateOverrideForm,
} from './OverrideForm';

interface OverrideAddDialogProps {
  open: boolean;
  staffId: string;
  onOpenChange: (next: boolean) => void;
}

export function OverrideAddDialog({
  open,
  staffId,
  onOpenChange,
}: OverrideAddDialogProps) {
  const { state, setState, errors, setErrors } = useOverrideFormState(
    emptyOverrideForm(),
    open,
  );

  const mutation = useCreateOverride(staffId);

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const result = validateOverrideForm(state);
    if (!result.ok || !result.data) {
      setErrors(result.errors);
      return;
    }
    setErrors({});
    try {
      await mutation.mutateAsync(result.data);
      toast.success('休み・時間変更を登録しました');
      onOpenChange(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '不明なエラー';
      setErrors({ _form: msg });
      toast.error(`登録に失敗しました: ${msg}`);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>休み・時間変更を追加</DialogTitle>
          <DialogDescription>
            指定日の通常シフトを上書きします。種別が「時間変更」の場合のみ
            時刻を入力してください。
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <OverrideForm
            state={state}
            onChange={setState}
            errors={errors}
            disabled={mutation.isPending}
          />

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
              {mutation.isPending ? '登録中…' : '登録する'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
