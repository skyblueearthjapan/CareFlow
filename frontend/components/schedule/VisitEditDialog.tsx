'use client';

/**
 * Edit modal for a single visit. Built on the shared `<Dialog>` primitive
 * (W1-F) — Radix gives us focus trap / Esc-to-close / scroll-lock. The
 * delete affordance is admin-only and confirms via a nested dialog.
 */
import { useEffect, useState } from 'react';
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
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { useDeleteVisit, useUpdateVisit } from '@/lib/queries/visits';
import {
  emptyVisitFormValues,
  visitReadToFormValues,
  type VisitFormValues,
  type VisitRead,
  type VisitUpdate,
} from '@/lib/schemas/visit';
import type { StaffRead } from '@/lib/schemas/staff';
import { DeleteConfirmModal } from '@/app/(app)/staff/_components/DeleteConfirmModal';

interface VisitEditDialogProps {
  open: boolean;
  visit: VisitRead | null;
  staff: StaffRead[];
  /** Admin only — surfaces the delete button. */
  canDelete?: boolean;
  onClose: () => void;
}

export function VisitEditDialog({ open, visit, staff, canDelete, onClose }: VisitEditDialogProps) {
  const [values, setValues] = useState<VisitFormValues>(emptyVisitFormValues);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const updateMutation = useUpdateVisit();
  const deleteMutation = useDeleteVisit();

  // Reset form when a new visit is opened.
  useEffect(() => {
    if (visit) {
      setValues(visitReadToFormValues(visit));
    } else {
      setValues(emptyVisitFormValues);
    }
  }, [visit]);

  const staffOptions: ComboboxOption[] = staff.map((s) => ({
    value: s.id,
    label: s.name,
  }));

  const handleField = <K extends keyof VisitFormValues>(key: K, value: VisitFormValues[K]) => {
    setValues((prev) => ({ ...prev, [key]: value }));
  };

  const handleSubmit = async () => {
    if (!visit) return;
    const payload: VisitUpdate = {
      visit_date: values.visit_date,
      start_time: values.start_time,
      end_time: values.end_time,
      // Empty string → clear assignment server-side.
      primary_staff_id: values.primary_staff_id || null,
      note: values.note.trim() === '' ? null : values.note,
    };
    try {
      await updateMutation.mutateAsync({ id: visit.id, payload });
      toast.success('訪問を更新しました');
      onClose();
    } catch (err) {
      toast.error(`更新に失敗しました: ${err instanceof Error ? err.message : '不明なエラー'}`);
    }
  };

  const handleDelete = async () => {
    if (!visit) return;
    try {
      await deleteMutation.mutateAsync(visit.id);
      toast.success('訪問を削除しました');
      setConfirmingDelete(false);
      onClose();
    } catch (err) {
      toast.error(`削除に失敗しました: ${err instanceof Error ? err.message : '不明なエラー'}`);
    }
  };

  const isBusy = updateMutation.isPending || deleteMutation.isPending;

  return (
    <>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!next && !isBusy) onClose();
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>訪問の編集</DialogTitle>
            <DialogDescription>
              {visit?.patient_name ?? '(患者名未取得)'} の訪問内容を変更します。
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-4 py-2">
            <div className="grid gap-2">
              <Label htmlFor="visit-date">日付</Label>
              <Input
                id="visit-date"
                type="date"
                value={values.visit_date}
                onChange={(e) => handleField('visit_date', e.target.value)}
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label htmlFor="visit-start">開始時刻</Label>
                <Input
                  id="visit-start"
                  type="time"
                  value={values.start_time}
                  onChange={(e) => handleField('start_time', e.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="visit-end">終了時刻</Label>
                <Input
                  id="visit-end"
                  type="time"
                  value={values.end_time}
                  onChange={(e) => handleField('end_time', e.target.value)}
                />
              </div>
            </div>

            <div className="grid gap-2">
              <Label>担当スタッフ</Label>
              <Combobox
                options={staffOptions}
                value={values.primary_staff_id}
                onChange={(v) => handleField('primary_staff_id', v)}
                placeholder="未割当"
                emptyText="該当スタッフなし"
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="visit-note">メモ</Label>
              <Textarea
                id="visit-note"
                value={values.note}
                onChange={(e) => handleField('note', e.target.value)}
                rows={3}
              />
            </div>
          </div>

          <DialogFooter className="sm:justify-between">
            {canDelete ? (
              <Button
                type="button"
                variant="destructive"
                onClick={() => setConfirmingDelete(true)}
                disabled={isBusy}
              >
                削除
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button type="button" variant="outline" onClick={onClose} disabled={isBusy}>
                キャンセル
              </Button>
              <Button type="button" onClick={handleSubmit} disabled={isBusy}>
                {updateMutation.isPending ? '保存中…' : '保存'}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DeleteConfirmModal
        open={confirmingDelete}
        title="訪問を削除しますか？"
        description="この操作は元に戻せません (ソフト削除)。"
        confirming={deleteMutation.isPending}
        onCancel={() => setConfirmingDelete(false)}
        onConfirm={handleDelete}
      />
    </>
  );
}
