'use client';

/**
 * ScheduleChangeDialog — D&D 後の変更反映モード選択ダイアログ (W9-FE2 Phase 4).
 *
 * 設計仕様書 `docs/plans/v2-allocation-redesign.md` §3.5.8 (D&D ダイアログ) に準拠。
 *
 * ┌──────────────────────────────────┐
 * │ この変更をどう反映しますか?         │
 * │  ◉ 今週のみ反映 (固定枠は変更なし)  │
 * │  ○ 固定枠を変更 (翌週以降も反映)   │
 * │      ※特別週の固定枠を変更         │ ← isSpecialWeek=true のときのみ
 * │ [キャンセル]  [適用]               │
 * └──────────────────────────────────┘
 */
import { useState } from 'react';
import { toast } from 'sonner';

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { useFixOrPattern } from '@/lib/queries/schedule_fix_or_pattern';
import type { ScheduleFixMode } from '@/lib/schemas/v2/schedule_fix_or_pattern';

// ─────────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────────

export interface ScheduleChangeDialogProps {
  open: boolean;
  visitId: string;
  patientName: string;
  /** 当該週が special_week_active に含まれるか。true の場合に特別週の注記を表示。 */
  isSpecialWeek: boolean;
  newWeekday: number;
  newStartTime: string;
  newDurationMin: number;
  isoYear: number;
  isoWeek: number;
  onClose: () => void;
  onApplied: (mode: ScheduleFixMode) => void;
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export function ScheduleChangeDialog({
  open,
  visitId,
  patientName,
  isSpecialWeek,
  newWeekday,
  newStartTime,
  newDurationMin,
  isoYear,
  isoWeek,
  onClose,
  onApplied,
}: ScheduleChangeDialogProps) {
  const [selectedMode, setSelectedMode] = useState<ScheduleFixMode>('this_week_only');
  const { mutateAsync, isPending } = useFixOrPattern();

  const handleApply = async () => {
    try {
      await mutateAsync({
        mode: selectedMode,
        visit_id: visitId,
        new_weekday: newWeekday,
        new_start_time: newStartTime,
        new_duration_min: newDurationMin,
        iso_year: isoYear,
        iso_week: isoWeek,
      });
      toast.success(
        selectedMode === 'pattern_change' ? '固定枠を更新しました' : '今週分のみ反映しました',
      );
      onApplied(selectedMode);
      onClose();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '変更の適用に失敗しました');
    }
  };

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) onClose();
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent aria-describedby="schedule-change-dialog-desc">
        <DialogHeader>
          <DialogTitle>この変更をどう反映しますか?</DialogTitle>
          <DialogDescription id="schedule-change-dialog-desc">
            {patientName} の訪問スケジュールを変更します。
          </DialogDescription>
        </DialogHeader>

        {/* ラジオボタン群 */}
        <div className="space-y-3 py-2">
          {/* this_week_only */}
          <label className="flex cursor-pointer items-start gap-3">
            <input
              type="radio"
              name="schedule-change-mode"
              value="this_week_only"
              checked={selectedMode === 'this_week_only'}
              onChange={() => setSelectedMode('this_week_only')}
              className="mt-0.5"
            />
            <span className="text-sm">
              <span className="font-medium">今週のみ反映</span>
              <span className="ml-1 text-text-muted">(固定枠は変更なし)</span>
            </span>
          </label>

          {/* pattern_change */}
          <label className="flex cursor-pointer items-start gap-3">
            <input
              type="radio"
              name="schedule-change-mode"
              value="pattern_change"
              checked={selectedMode === 'pattern_change'}
              onChange={() => setSelectedMode('pattern_change')}
              className="mt-0.5"
            />
            <span className="text-sm">
              <span className="font-medium">固定枠を変更</span>
              <span className="ml-1 text-text-muted">(翌週以降も反映)</span>
              {isSpecialWeek && selectedMode === 'pattern_change' && (
                <span className="mt-0.5 block text-xs text-warning">※特別週の固定枠を変更</span>
              )}
            </span>
          </label>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isPending}>
            キャンセル
          </Button>
          <Button onClick={() => void handleApply()} disabled={isPending}>
            {isPending ? '適用中...' : '適用'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
