'use client';

/**
 * Edit / delete dialog for an existing staff override.
 *
 * - PATCH on submit (`useUpdateOverride`)
 * - DELETE via in-dialog confirm step (`useDeleteOverride`); reuses the
 *   existing DeleteConfirmModal pattern but inlined here as a secondary
 *   sub-dialog so the user can cancel without losing edits.
 */
import { useState, type FormEvent } from 'react';

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
import {
  useDeleteOverride,
  useUpdateOverride,
} from '@/lib/queries/staff-overrides';
import {
  overrideTypeUsesTime,
  type OverrideRead,
} from '@/lib/schemas/staff-overrides';

import { DeleteConfirmModal } from '../../_components/DeleteConfirmModal';

import {
  OverrideForm,
  formStateToCreate,
  useOverrideFormState,
  validateOverrideForm,
  type OverrideFormState,
} from './OverrideForm';

interface OverrideEditDialogProps {
  open: boolean;
  staffId: string;
  override: OverrideRead | null;
  onOpenChange: (next: boolean) => void;
}

function overrideToFormState(o: OverrideRead): OverrideFormState {
  return {
    date: o.date,
    type: o.type,
    start_time: o.start_time ?? '',
    end_time: o.end_time ?? '',
    note: o.note ?? '',
  };
}

export function OverrideEditDialog({
  open,
  staffId,
  override,
  onOpenChange,
}: OverrideEditDialogProps) {
  const initial = override ? overrideToFormState(override) : null;

  // resetSignal combines open-state and override id so re-opening for a
  // different row re-seeds the form, but normal re-renders don't clobber edits.
  const resetSignal = `${open ? '1' : '0'}:${override?.id ?? ''}`;

  const { state, setState, errors, setErrors } = useOverrideFormState(
    initial ?? {
      date: '',
      type: '休み',
      start_time: '',
      end_time: '',
      note: '',
    },
    resetSignal,
  );

  const updateMutation = useUpdateOverride(staffId);
  const deleteMutation = useDeleteOverride(staffId);
  const [confirmDelete, setConfirmDelete] = useState(false);

  if (!override) return null;

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const result = validateOverrideForm(state);
    if (!result.ok || !result.data) {
      setErrors(result.errors);
      return;
    }
    setErrors({});
    // PATCH body mirrors create payload; backend tolerates partial vs full.
    const payload = formStateToCreate(state);
    if (!overrideTypeUsesTime(payload.type)) {
      payload.start_time = null;
      payload.end_time = null;
    }
    try {
      await updateMutation.mutateAsync({ id: override.id, payload });
      toast.success('休み・時間変更を更新しました');
      onOpenChange(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '不明なエラー';
      setErrors({ _form: msg });
      toast.error(`更新に失敗しました: ${msg}`);
    }
  };

  const handleDelete = async () => {
    try {
      await deleteMutation.mutateAsync(override.id);
      toast.success('休み・時間変更を削除しました');
      setConfirmDelete(false);
      onOpenChange(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '不明なエラー';
      toast.error(`削除に失敗しました: ${msg}`);
    }
  };

  const busy = updateMutation.isPending || deleteMutation.isPending;

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>休み・時間変更を編集</DialogTitle>
            <DialogDescription>
              内容を変更して保存、または「削除」で取り消せます。
            </DialogDescription>
          </DialogHeader>

          <form onSubmit={handleSubmit} className="space-y-4">
            <OverrideForm
              state={state}
              onChange={setState}
              errors={errors}
              disabled={busy}
            />

            <DialogFooter className="sm:justify-between">
              <Button
                type="button"
                variant="destructive"
                onClick={() => setConfirmDelete(true)}
                disabled={busy}
              >
                削除
              </Button>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => onOpenChange(false)}
                  disabled={busy}
                >
                  キャンセル
                </Button>
                <Button type="submit" disabled={busy}>
                  {updateMutation.isPending ? '保存中…' : '保存する'}
                </Button>
              </div>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <DeleteConfirmModal
        open={confirmDelete}
        title="この休み・時間変更を削除しますか？"
        description={`${override.date} (${override.type}) の登録を削除します。`}
        confirming={deleteMutation.isPending}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={handleDelete}
      />
    </>
  );
}
