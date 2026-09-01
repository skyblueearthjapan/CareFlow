'use client';
/**
 * 「突合レポート」ボタン — らく助×カイポケの週突合を A4 の独立 HTML で新しいタブに開く
 * (read-only・PO 要望 2026-09-01「差分確認/突き合わせの際に HTML が出てくるとらく」)。
 *
 * - カイポケ側は保存済みの最新スナップショット (RPA は回さない・即応答)。
 *   鮮度はレポート冒頭に取得時刻として明示される。
 * - `window.open` は click ハンドラ内で同期的に呼ぶ (ポップアップブロック回避)。
 *   'noopener' を features に付けると window.open が null を返すため付けず、
 *   遷移後に win.opener = null で切り離す (FeasibilityCheckButton と同じ・レビュー NEW-1)。
 */
import { useCallback, useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { useReconcileReport, type ReconcileReport } from '@/lib/queries/reconcileReport';

export interface ReconcileReportButtonProps {
  /** 週の月曜 (YYYY-MM-DD)。 */
  weekStart: string;
  /** 月曜から何日分 (既定 7)。 */
  days?: number;
  /** admin のみ表示 (RBAC)。 */
  canEdit: boolean;
  /** ボタンの見た目 (置き場所に合わせる)。 */
  size?: 'sm' | 'md';
}

export function ReconcileReportButton({
  weekStart,
  days,
  canEdit,
  size = 'sm',
}: ReconcileReportButtonProps) {
  const mut = useReconcileReport();
  const [last, setLast] = useState<ReconcileReport | null>(null);

  const run = useCallback(async () => {
    const win = typeof window !== 'undefined' ? window.open('', '_blank') : null;
    try {
      const r = await mut.mutateAsync({ weekStart, days });
      setLast(r);
      if (win && r.html) {
        const url = URL.createObjectURL(new Blob([r.html], { type: 'text/html;charset=utf-8' }));
        win.location.href = url;
        try {
          win.opener = null;
        } catch {
          /* 一部ブラウザで読み取り専用 */
        }
        window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      } else if (win) {
        win.close();
      }
      const ng =
        (r.counts['相違'] ?? 0) + (r.counts['らく助のみ'] ?? 0) + (r.counts['カイポケのみ'] ?? 0);
      toast[ng > 0 ? 'warning' : 'success'](
        `突合レポート: 一致 ${r.counts['一致'] ?? 0} / 相違系 ${ng} 件（全 ${r.total} 件）`,
      );
    } catch (e) {
      win?.close();
      toast.error(
        `突合レポートの生成に失敗しました: ${e instanceof Error ? e.message : '不明なエラー'}`,
      );
    }
  }, [mut, weekStart, days]);

  if (!canEdit) return null;

  const ngCount = last
    ? (last.counts['相違'] ?? 0) +
      (last.counts['らく助のみ'] ?? 0) +
      (last.counts['カイポケのみ'] ?? 0)
    : null;

  return (
    <span className="inline-flex items-center gap-1.5">
      <Button
        type="button"
        variant="outline"
        size={size}
        onClick={run}
        disabled={mut.isPending}
        title="らく助×カイポケの週突合を印刷用 HTML で開く（カイポケ側は保存済みスナップショット・read-only）"
        data-testid="reconcile-report-button"
      >
        {mut.isPending ? '突合中…' : '🔍 突合レポート'}
      </Button>
      {ngCount != null ? (
        <span
          className={
            ngCount > 0
              ? 'rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-bold text-red-700'
              : 'rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-bold text-emerald-700'
          }
          data-testid="reconcile-report-badge"
        >
          {ngCount > 0 ? `相違 ${ngCount}` : '全一致'}
        </span>
      ) : null}
    </span>
  );
}
