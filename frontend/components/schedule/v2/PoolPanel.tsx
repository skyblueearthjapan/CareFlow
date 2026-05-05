'use client';

/**
 * PoolPanel — 保留プール (W3-FE5).
 *
 * 設計 v0.9 §3.6.2 (Layer 1) の「未配置プール」。新規患者や時刻スロットから
 * 解除された患者をここに溜め、ドラッグでセルに配置する。配置済みカードを
 * 本パネルにドロップすると配置解除 (= weekly_pattern entry を削除予定として
 * 親 state に戻す) になる。
 *
 * 1 つの大きな drop target として `useDroppable` を取り、子に PatientCard を
 * 並べる。空のときも drop 可能であることを示すプレースホルダを描画する。
 */
import { useDroppable } from '@dnd-kit/core';
import { Inbox } from 'lucide-react';

import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';

export const POOL_DROPPABLE_ID = 'pool';

export interface PoolPanelProps {
  /** プール内のカード列。親が PatientCard を組み立てて渡す。 */
  children?: React.ReactNode;
  /** プール内の患者数。0 のときに案内文を出す。 */
  count: number;
  disabled?: boolean;
}

export function PoolPanel({ children, count, disabled = false }: PoolPanelProps) {
  const { isOver, setNodeRef } = useDroppable({
    id: POOL_DROPPABLE_ID,
    disabled,
    data: { kind: 'pool' },
  });

  return (
    <Card className="p-3">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="flex items-center gap-1 text-sm font-semibold text-text-primary">
          <Inbox className="h-4 w-4" aria-hidden />
          保留プール
        </h2>
        <span className="tnum text-xs text-text-muted">{count} 名</span>
      </div>
      <div
        ref={setNodeRef}
        className={cn(
          'min-h-[64px] rounded border border-dashed p-2 transition-colors',
          isOver && !disabled
            ? 'border-brand-primary/60 bg-brand-primary/10'
            : 'border-border-default bg-bg-muted/40',
        )}
        data-pool="true"
      >
        {count === 0 ? (
          <p className="py-3 text-center text-xs text-text-muted">
            プールは空です。配置を解除した患者がここに戻ります。
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-1 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
            {children}
          </div>
        )}
      </div>
    </Card>
  );
}
