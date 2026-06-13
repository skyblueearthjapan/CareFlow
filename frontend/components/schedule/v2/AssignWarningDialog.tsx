'use client';

/**
 * AssignWarningDialog — Phase G-89 (自動割付の事後警告ダイアログ).
 *
 * 直前 commit 748bf00 (Layer3 患者中心ローテ刷新) の続き。
 *
 * 「埋めて事後警告」 方針:
 *   人手不足でローテーション (= 同じ担当者を避ける) を維持できなくても、
 *   訪問は確保 (= コミットまで実行) し、 事後にこのダイアログで一覧表示する。
 *   ユーザーは見て必要なら担当 dropdown で手動変更する。
 *
 * 表示する警告は 2 種:
 *   🔴 ローテ衝突 (rotation_warnings): 直近担当者を再割り当てした
 *      = index0=連続 (= 前回と同じ担当) / index1=2個前 の Badge で連続性を表示。
 *   🟠 未割当 (unassigned_warnings): 担当を確保できなかったコース。
 *
 * 衝突ゼロなら呼び出し側でこのダイアログを出さず、 従来どおり success toast のみ。
 */
import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type {
  RotationConflictWarning,
  UnassignedCourseWarning,
} from '@/lib/queries/assign_staff_only';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

function fmtWeekday(weekday: number): string {
  return WEEKDAY_LABELS[weekday] ?? `?${weekday}`;
}

/** "09:00:00" → "09:00" (秒を落とす). null/undefined はダッシュ. */
function fmtTime(t: string | null | undefined): string {
  if (!t) return '—';
  return t.slice(0, 5);
}

/**
 * ローテ距離 Badge のラベルと variant を recent_index から導出する.
 *   index0 = 連続 (= 1 つ前と同じ) → 赤 (destructive)
 *   index1 = 2 個前と同じ (前回は別の人) → warning
 *   index2+ = 3 個前以上 → secondary
 */
function rotationBadge(w: RotationConflictWarning): {
  label: string;
  variant: 'destructive' | 'warning' | 'secondary';
} {
  if (w.is_consecutive || w.recent_index === 0) {
    return { label: '連続 (前回と同じ)', variant: 'destructive' };
  }
  if (w.recent_index === 1) {
    return { label: '2個前と同じ', variant: 'warning' };
  }
  return { label: `${w.recent_index + 1}個前と同じ`, variant: 'secondary' };
}

export interface AssignWarningDialogProps {
  open: boolean;
  onClose: () => void;
  rotationWarnings: RotationConflictWarning[];
  unassignedWarnings: UnassignedCourseWarning[];
}

export function AssignWarningDialog({
  open,
  onClose,
  rotationWarnings,
  unassignedWarnings,
}: AssignWarningDialogProps) {
  const hasRotation = rotationWarnings.length > 0;
  const hasUnassigned = unassignedWarnings.length > 0;

  return (
    <Dialog open={open} onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent
        className="max-h-[88vh] max-w-2xl overflow-y-auto"
        data-testid="assign-warning-dialog"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span aria-hidden>⚠️</span>
            自動割付の警告
          </DialogTitle>
          <DialogDescription>
            人手不足のため、ローテーション (同じ担当者を避ける) を維持できませんでした。
            訪問は確保していますが、内容をご確認のうえ、必要なら担当を手動で変更してください。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-1">
          {/* 🔴 ローテ衝突セクション */}
          {hasRotation ? (
            <section data-testid="assign-warning-rotation-section">
              <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-text-primary">
                <span aria-hidden>🔴</span>
                ローテーション衝突 ({rotationWarnings.length} 件)
              </h3>
              <ul className="space-y-1.5">
                {rotationWarnings.map((w, i) => {
                  const badge = rotationBadge(w);
                  return (
                    <li
                      key={`rot-${w.course_id}-${w.patient_id}-${i}`}
                      className="flex flex-wrap items-center gap-2 rounded border border-border-default bg-bg-base p-2 text-xs"
                      data-testid="assign-warning-rotation-item"
                    >
                      <span className="font-medium text-text-primary">
                        {w.patient_name ?? w.patient_id}
                      </span>
                      <span className="text-text-muted">
                        {w.course_code} / {fmtWeekday(w.weekday)} {fmtTime(w.visit_start_time)}
                      </span>
                      <span className="text-text-secondary">
                        担当: {w.staff_name ?? w.staff_id}
                      </span>
                      <Badge variant={badge.variant} className="ml-auto text-[10px]">
                        {badge.label}
                      </Badge>
                    </li>
                  );
                })}
              </ul>
            </section>
          ) : null}

          {/* 🟠 未割当セクション */}
          {hasUnassigned ? (
            <section data-testid="assign-warning-unassigned-section">
              <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-text-primary">
                <span aria-hidden>🟠</span>
                未割当 ({unassignedWarnings.length} 件)
              </h3>
              <ul className="space-y-1.5">
                {unassignedWarnings.map((w, i) => (
                  <li
                    key={`unassigned-${w.course_id}-${i}`}
                    className="flex flex-col gap-1 rounded border border-border-default bg-bg-base p-2 text-xs"
                    data-testid="assign-warning-unassigned-item"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-text-primary">{w.course_code}</span>
                      <span className="text-text-muted">
                        {fmtWeekday(w.weekday)} {fmtTime(w.visit_start_time)}
                      </span>
                      <Badge variant="warning" className="ml-auto text-[10px]">
                        担当者が確保できませんでした
                      </Badge>
                    </div>
                    {w.patient_names.length > 0 ? (
                      <div className="text-text-secondary">
                        対象患者: {w.patient_names.join(' / ')}
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {!hasRotation && !hasUnassigned ? (
            <div className="py-4 text-center text-xs text-text-muted">警告はありません。</div>
          ) : null}
        </div>

        <DialogFooter>
          <Button type="button" onClick={onClose} data-testid="assign-warning-dialog-close">
            閉じる
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
