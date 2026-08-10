'use client';

/**
 * ConstraintOverrideConfirmDialog — 手動でのコース担当変更を「確認して通す」ダイアログ.
 *
 * 設計書 `docs/plans/patient-ng-staff-design.md` §7-2 (acknowledge 方式・BE が正):
 *   PATCH /api/v1/courses/{id} で assigned_staff_id を変えたとき、 新担当が
 *   コース所属患者の NG スタッフ / 性別制限に抵触すると BE が 422 を返す:
 *     { detail: { code: 'constraint_confirmation_required', warnings: [...] } }
 *   FE は本ダイアログで内容を提示し、 OK なら
 *   `acknowledge_constraint_warnings: true` を足して再送する。
 *
 * 週 5 日分などの一括変更では、 呼び出し側が曜日ごとの警告をまとめて 1 回だけ
 * 開く (warnings に全件を積む) 想定。
 *
 * ブロックではなく確認: ピンモデルの「エンジンだけ縛り、人手は自由（ただし見える化）」
 * に整合する (week_pinned の絶対ブロック 422 とは別物)。
 */
import * as React from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { ConstraintWarning } from '@/lib/schemas/patient_ng_staff';

export interface ConstraintOverrideConfirmDialogProps {
  open: boolean;
  warnings: ConstraintWarning[];
  /** 適用中フラグ (= ボタン無効化). */
  applying?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

/** 1 件の警告文言 (NG スタッフ / 性別制限)。 */
function warningText(w: ConstraintWarning): string {
  const staff = w.staff_name ?? 'このスタッフ';
  const patient = w.patient_name ?? '対象の患者';
  if (w.kind === 'ng_staff') {
    const note = w.note ? `（メモ: ${w.note}）` : '';
    return `${staff}さんは${patient}様のNGスタッフです${note}`;
  }
  return `${staff}さんは${patient}様の性別制限に適合しません`;
}

export function ConstraintOverrideConfirmDialog({
  open,
  warnings,
  applying = false,
  onCancel,
  onConfirm,
}: ConstraintOverrideConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(o) => (!o ? onCancel() : undefined)}>
      <DialogContent className="max-w-md" data-testid="constraint-override-confirm">
        <DialogHeader>
          <DialogTitle>それでも割り当てますか？</DialogTitle>
          <DialogDescription>
            この担当変更は、次の制約に抵触します。 内容を確認のうえ、
            管理者の判断として進める場合は「割り当てる」を押してください。
          </DialogDescription>
        </DialogHeader>

        <ul className="space-y-1">
          {warnings.map((w, i) => (
            <li
              key={`${w.patient_id}-${w.staff_id}-${w.kind}-${i}`}
              className="rounded border border-warning/40 bg-warning/5 px-2 py-1 text-xs text-text-secondary"
              data-testid="constraint-override-warning-row"
              data-kind={w.kind}
            >
              <span aria-hidden>⚠ </span>
              {warningText(w)}
            </li>
          ))}
        </ul>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={onCancel}
            disabled={applying}
            data-testid="constraint-override-cancel"
          >
            やめる
          </Button>
          <Button
            type="button"
            onClick={onConfirm}
            disabled={applying}
            data-testid="constraint-override-ok"
          >
            割り当てる
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
