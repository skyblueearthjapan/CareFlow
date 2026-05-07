'use client';

/**
 * PairRoleEditor — 補佐ペアの主担当 (primary) / 補佐 (support) 切替 UI (W15-FE D-4).
 *
 * staff_companion_assignments の中で当該 (trainee, weekday, part) の row を
 * 主担当 (★) / 補佐 (☆) で切り替える。BE は W15 P1 で pair_role に対応済み。
 *
 * 本コンポーネントは「単一エントリの pair_role を切替」UI のみを提供する。
 * 一覧取得 / bulk PUT は呼び出し側 (ScheduleUnifiedView) が担当する。
 *
 * UI:
 *   ★ (primary) / ☆ (support) のトグルボタン
 *   クリックで反転 → onChange callback (親が PUT bulk を発火)
 */
import { Star } from 'lucide-react';

import { type PairRole } from '@/lib/schemas/v2/staff_companion_assignment';
import { cn } from '@/lib/utils';

export interface PairRoleEditorProps {
  /** 現在の pair_role (null = 未設定 → primary 扱い). */
  value: PairRole | null;
  onChange: (next: PairRole) => void;
  canEdit: boolean;
  /** ボタンのサイズ (compact = 16px, default = 20px). */
  size?: 'compact' | 'default';
}

export function PairRoleEditor({
  value,
  onChange,
  canEdit,
  size = 'compact',
}: PairRoleEditorProps) {
  const effective: PairRole = value ?? 'primary';
  const isPrimary = effective === 'primary';
  const next: PairRole = isPrimary ? 'support' : 'primary';

  const iconSize = size === 'compact' ? 'h-3 w-3' : 'h-4 w-4';

  return (
    <button
      type="button"
      onClick={() => canEdit && onChange(next)}
      disabled={!canEdit}
      className={cn(
        'inline-flex items-center justify-center rounded border px-1 py-0.5 transition-colors',
        isPrimary
          ? 'border-warning/60 bg-warning/10 text-warning'
          : 'border-border-default bg-bg-base text-text-muted',
        canEdit ? 'cursor-pointer hover:bg-bg-muted' : 'cursor-default opacity-70',
      )}
      aria-label={isPrimary ? '主担当 (クリックで補佐に切替)' : '補佐 (クリックで主担当に切替)'}
      title={isPrimary ? '主担当 (primary)' : '補佐 (support)'}
      data-pair-role={effective}
    >
      <Star className={cn(iconSize, isPrimary ? 'fill-warning' : '')} aria-hidden />
    </button>
  );
}
