'use client';

/**
 * UnassignAllStaffButton — Phase G-17 「一斉スタッフ未割当」 (旧称: 一斉未割当).
 *
 * 動作:
 *   1. 「一斉スタッフ未割当」 クリック → 確認ダイアログ
 *   2. OK → POST /api/v1/schedule/v2/unassign-all-staff
 *      - 表示中の週の全 ``courses.assigned_staff_id`` を NULL に
 *      - 同週の ``visit_staff_assignments`` を物理 delete
 *      - course / visit 自体は残す
 *   3. 成功時: toast で件数表示 + visits / courses cache invalidate
 *   4. 失敗時: toast.error
 *
 * RBAC: admin / manager のみ表示 (BE 側で 403 も担保).
 * 粒度: 1 週間単位 (= 表示中の週).
 */
import * as React from 'react';
import { Loader2, UserX } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useUnassignAllStaffMutation } from '@/lib/queries/autoScheduleV2';

import { formatErr } from './_autoScheduleUtils';

export interface UnassignAllStaffButtonProps {
  isoYear: number;
  isoWeek: number;
  officeId: string | null;
  /** 他ボタンと縦の高さを揃えるための size prop. */
  size?: 'sm' | 'md' | 'lg' | 'icon';
  /** 他の mutation 進行中で disable させたいときに使う. */
  disabled?: boolean;
}

export function UnassignAllStaffButton({
  isoYear,
  isoWeek,
  officeId,
  size = 'sm',
  disabled = false,
}: UnassignAllStaffButtonProps) {
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const unassignMut = useUnassignAllStaffMutation();

  const handleClick = () => {
    if (unassignMut.isPending) return;
    setConfirmOpen(true);
  };

  const handleConfirm = async () => {
    try {
      const res = await unassignMut.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        office_ids: officeId ? [officeId] : [],
      });
      toast.success(
        `${res.courses_unassigned} コースの担当 + ${res.visit_assignments_removed} 件の訪問担当を解除しました`,
      );
      setConfirmOpen(false);
    } catch (err) {
      toast.error(`一斉スタッフ未割当に失敗しました: ${formatErr(err)}`);
    }
  };

  return (
    <>
      <Button
        type="button"
        size={size}
        variant="outline"
        onClick={handleClick}
        disabled={disabled || unassignMut.isPending}
        data-testid="unassign-all-staff-button"
      >
        {unassignMut.isPending ? (
          <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
        ) : (
          <UserX className="mr-1 h-4 w-4" aria-hidden />
        )}
        一斉スタッフ未割当
      </Button>

      <Dialog
        open={confirmOpen}
        onOpenChange={(o) => {
          if (!o && !unassignMut.isPending) setConfirmOpen(false);
        }}
      >
        <DialogContent className="max-w-md" data-testid="unassign-all-staff-confirm">
          <DialogHeader>
            <DialogTitle>
              {isoYear} 年 第 {isoWeek} 週 の担当を一括解除しますか?
            </DialogTitle>
            <DialogDescription>
              表示中の週の全コース担当と訪問担当 (visit_staff_assignments) を一括で解除します。
              <br />
              <span className="font-semibold text-warning">取り消せません。</span>
              <br />
              よろしいですか?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setConfirmOpen(false)}
              disabled={unassignMut.isPending}
            >
              キャンセル
            </Button>
            <Button
              type="button"
              onClick={handleConfirm}
              disabled={unassignMut.isPending}
              data-testid="unassign-all-staff-confirm-ok"
            >
              {unassignMut.isPending ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <UserX className="mr-1 h-4 w-4" aria-hidden />
              )}
              一斉スタッフ未割当
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
