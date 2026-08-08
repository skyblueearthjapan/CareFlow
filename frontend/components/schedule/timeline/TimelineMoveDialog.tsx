'use client';

/**
 * TimelineMoveDialog — T-2 ②-b: タイムラインのカードドラッグ後に出す
 * 「この週だけ / 固定パターン (毎週の型)」二択確認ダイアログ。
 *
 * 二択 UI は共通部品 ChangeScopeChoice (設計書 §2.4 — 全変更操作の出口を統一)。
 * 既定 = 'week' (この週だけ・U-work の既定 B を踏襲)。
 *
 * 表示専用コンポーネントの原則: mutation は持たず onConfirm(scope) を返すだけ。
 * Panel 側で week=visit-move-week-only / pattern=移動+週→型同期 (既存の昇格と同義) を叩く。
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
import {
  ChangeScopeChoice,
  type ChangeScopeValue,
} from '@/components/schedule/v2/ChangeScopeChoice';

export interface TimelineMoveContext {
  patientName: string;
  /** 例: "稲毛A（田中 一郎）"。 */
  fromLabel: string;
  toLabel: string;
  weekdayLabel: string;
  /** 'HH:MM'。 */
  oldTimeHM: string;
  newTimeHM: string;
  durationMin: number;
  courseChanged: boolean;
  /**
   * 統合 (PO 決定 2026-08-09): 移動対象に完全固定 (movability='locked') を含む。
   * エンジンは動かさないが、人手は注意書きを見たうえで動かせる (確認して可)。
   */
  lockedNotice?: boolean;
}

export interface TimelineMoveDialogProps {
  open: boolean;
  context: TimelineMoveContext | null;
  busy?: boolean;
  onConfirm: (scope: ChangeScopeValue) => void;
  onClose: () => void;
}

export function TimelineMoveDialog({
  open,
  context,
  busy = false,
  onConfirm,
  onClose,
}: TimelineMoveDialogProps) {
  const [scope, setScope] = React.useState<ChangeScopeValue>('week');

  // open のたびに既定 (この週だけ) へリセット。
  React.useEffect(() => {
    if (open) setScope('week');
  }, [open]);

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o && !busy) onClose();
      }}
    >
      <DialogContent aria-describedby="timeline-move-desc" data-testid="timeline-move-dialog">
        <DialogHeader>
          <DialogTitle>訪問を移動</DialogTitle>
          <DialogDescription id="timeline-move-desc">
            {context ? (
              <>
                <span className="font-semibold text-text-primary">{context.patientName}</span>{' '}
                さんの訪問（{context.weekdayLabel}曜・{context.durationMin}分）を
                <span className="tnum mx-1 font-semibold text-text-primary">
                  {context.oldTimeHM}
                </span>
                →
                <span className="tnum mx-1 font-semibold text-text-primary">
                  {context.newTimeHM}
                </span>
                へ移動します。
                {context.courseChanged ? (
                  <>
                    コースも
                    <span className="mx-1 font-semibold text-text-primary">
                      {context.fromLabel}
                    </span>
                    →<span className="mx-1 font-semibold text-text-primary">{context.toLabel}</span>
                    に変わります。
                  </>
                ) : null}
              </>
            ) : null}
          </DialogDescription>
        </DialogHeader>

        {context?.lockedNotice ? (
          <div
            className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm font-medium text-red-700"
            data-testid="timeline-move-locked-notice"
          >
            ⚠ これは完全固定です。システムは動かしませんが、この操作では手動で移動します。
          </div>
        ) : null}

        <div className="py-2">
          <ChangeScopeChoice
            value={scope}
            onChange={setScope}
            disabled={busy}
            defaultNote="既定は「この週だけ」。あとからトーストの「毎週の型にも登録」でも昇格できます。"
          />
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onClose}
            disabled={busy}
            data-testid="timeline-move-cancel"
          >
            キャンセル
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={busy}
            onClick={() => onConfirm(scope)}
            data-testid="timeline-move-confirm"
          >
            {busy ? '移動中…' : '移動する'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
