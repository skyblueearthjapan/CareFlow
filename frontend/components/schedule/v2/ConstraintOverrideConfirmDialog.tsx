'use client';

/**
 * ConstraintOverrideConfirmDialog — NG スタッフ / 性別制限を「確認して通す」ダイアログ.
 *
 * 設計書 `docs/plans/patient-ng-staff-design.md` §7-2 (acknowledge 方式・BE が正):
 *   人手操作 (担当変更 / 訪問移動 / 配置 / 提案採用) の結果、担当が対象患者の
 *   NG スタッフ / 性別制限に抵触すると BE が 422 を返す:
 *     { detail: { code: 'constraint_confirmation_required', warnings: [...] } }
 *   FE は本ダイアログで内容を提示し、 OK なら
 *   `acknowledge_constraint_warnings: true` を足して再送する。
 *
 * 週 5 日分などの一括変更では、 呼び出し側が曜日ごとの警告をまとめて 1 回だけ
 * 開く (warnings に全件を積む) 想定。
 *
 * 文言 (title / description / confirmLabel) は optional props で差し替えられる。
 * 既定値はコース担当変更 (初出の呼び出し元) の文言で、省略時の挙動は不変。
 * 移動・配置・採用など経路ごとの言い回しは呼び出し側が渡す。
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

/** 既定文言 (コース担当変更). 省略時はこの 3 つが使われる = 後方互換. */
export const CONSTRAINT_CONFIRM_DEFAULT_TITLE = 'それでも割り当てますか？';
export const CONSTRAINT_CONFIRM_DEFAULT_DESCRIPTION = 'この担当変更は、次の制約に抵触します';
export const CONSTRAINT_CONFIRM_DEFAULT_LABEL = '割り当てる';

export interface ConstraintOverrideConfirmDialogProps {
  open: boolean;
  warnings: ConstraintWarning[];
  /** 適用中フラグ (= ボタン無効化). */
  applying?: boolean;
  /** 見出し。省略時「それでも割り当てますか？」. */
  title?: string;
  /**
   * 説明文の前半 (「〜次の制約に抵触します」相当)。後半の定型
   * 「内容を確認のうえ、管理者の判断として進める場合は…」は本体が付ける。
   */
  description?: string;
  /** 実行ボタンの文言。省略時「割り当てる」. */
  confirmLabel?: string;
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
  title = CONSTRAINT_CONFIRM_DEFAULT_TITLE,
  description = CONSTRAINT_CONFIRM_DEFAULT_DESCRIPTION,
  confirmLabel = CONSTRAINT_CONFIRM_DEFAULT_LABEL,
  onCancel,
  onConfirm,
}: ConstraintOverrideConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(o) => (!o ? onCancel() : undefined)}>
      <DialogContent className="max-w-md" data-testid="constraint-override-confirm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {description}。 内容を確認のうえ、 管理者の判断として進める場合は「{confirmLabel}
            」を押してください。
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
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
