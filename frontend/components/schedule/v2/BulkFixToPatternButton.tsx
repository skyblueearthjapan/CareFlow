'use client';

/**
 * BulkFixToPatternButton — 全患者一括固定化ボタン (W9-FE2 Phase 4).
 *
 * 設計仕様書 `docs/plans/v2-api-contracts.md` §8.5 (from-week-bulk) に準拠。
 *
 * admin / manager のみ表示。クリック後に確認ダイアログを表示し、
 * 「実行」で POST /api/v1/patients/fixed-visits/from-week-bulk を呼ぶ。
 *
 * 確認ダイアログ:
 *   当該週 (YYYY-W##) の visits を全 active 患者の固定枠に書き戻します。
 *   既存の固定枠は上書きされます。
 *   ☐ 特別週として保存
 *   [キャンセル] [実行]
 */
import { useState } from 'react';
import { DatabaseZap } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useApplyFromWeekBulk } from '@/lib/queries/patient_fixed_visits';

// ─────────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────────

export interface BulkFixToPatternButtonProps {
  /** admin / manager のみ表示する。 */
  canEdit: boolean;
  isoYear: number;
  isoWeek: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export function BulkFixToPatternButton({ canEdit, isoYear, isoWeek }: BulkFixToPatternButtonProps) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [isSpecial, setIsSpecial] = useState(false);
  const { mutateAsync, isPending } = useApplyFromWeekBulk();

  // admin / manager のみ表示
  if (!canEdit) return null;

  const isoWeekLabel = `${isoYear}-W${String(isoWeek).padStart(2, '0')}`;

  const handleExecute = async () => {
    try {
      await mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        mode: isSpecial ? 'special' : 'normal',
      });
      // toast は useApplyFromWeekBulk 内で表示済み
      setDialogOpen(false);
      setIsSpecial(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '一括固定化に失敗しました');
    }
  };

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setIsSpecial(false);
    }
    setDialogOpen(next);
  };

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        onClick={() => setDialogOpen(true)}
        className="gap-1.5"
        aria-label="全患者の固定枠を一括保存"
      >
        <DatabaseZap className="h-4 w-4" aria-hidden />
        全患者固定枠を一括保存
      </Button>

      <Dialog open={dialogOpen} onOpenChange={handleOpenChange}>
        <DialogContent aria-describedby="bulk-fix-dialog-desc">
          <DialogHeader>
            <DialogTitle>全患者の固定枠を一括保存</DialogTitle>
            <DialogDescription id="bulk-fix-dialog-desc">
              当該週 ({isoWeekLabel}) の visits を全 active 患者の固定枠に書き戻します。
              既存の固定枠は上書きされます。
            </DialogDescription>
          </DialogHeader>

          {/* 特別週チェックボックス */}
          <div className="flex items-center gap-2 py-2">
            <Checkbox
              id="bulk-fix-special"
              checked={isSpecial}
              onCheckedChange={(checked) => setIsSpecial(checked === true)}
            />
            <label htmlFor="bulk-fix-special" className="cursor-pointer text-sm text-text-primary">
              特別週として保存
            </label>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={isPending}>
              キャンセル
            </Button>
            <Button onClick={() => void handleExecute()} disabled={isPending}>
              {isPending ? '実行中...' : '実行'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
