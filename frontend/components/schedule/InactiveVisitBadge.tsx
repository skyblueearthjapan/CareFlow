/**
 * InactiveVisitBadge — 非稼働患者の予定に付ける小さなバッジ
 * (患者ステータス連動 Phase 3・design 2026-09-09 §3-4 / §6-C Q15)。
 *
 *   inactive      … 「入院中」等 (患者ステータスのラベル)。残骸 = 不整合の合図。
 *   status_cancel … 「取消（連動）」。トグル「非稼働を表示」が ON のときだけ出る。
 *
 * 文言は `visitDisplayBadgeLabel` (= `inactiveStatusLabel`) が唯一の出所で、
 * 呼び出し側はステータス値を列挙しない。
 */
import * as React from 'react';

import { cn } from '@/lib/utils';
import {
  visitDisplayBadgeLabel,
  type VisitDisplayKind,
  type VisitVisibilityInput,
} from '@/lib/schedule/visitVisibility';

export interface InactiveVisitBadgeProps {
  visit: VisitVisibilityInput;
  kind: VisitDisplayKind;
  className?: string;
  /** data-testid。既定は `inactive-visit-badge`。 */
  testId?: string;
}

export function InactiveVisitBadge({
  visit,
  kind,
  className,
  testId = 'inactive-visit-badge',
}: InactiveVisitBadgeProps) {
  const label = visitDisplayBadgeLabel(visit, kind);
  if (!label) return null;
  return (
    <span
      className={cn(
        'shrink-0 rounded px-1 text-[9px] font-bold no-underline',
        kind === 'status_cancel'
          ? 'bg-bg-muted text-text-muted'
          : 'bg-warning-bg text-warning-strong',
        className,
      )}
      data-testid={testId}
      title={
        kind === 'status_cancel'
          ? '患者ステータスの変更で取り消された予定です'
          : '稼働中でない患者様の予定が残っています（要確認）'
      }
    >
      {label}
    </span>
  );
}
