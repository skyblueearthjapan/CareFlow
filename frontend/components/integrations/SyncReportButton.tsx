'use client';
/**
 * 「📄 レポート」ボタン — 完了した連携ジョブの結果を A4 縦の独立 HTML で新しいタブに開く
 * (sync-result-report-design.md §5・PO 要望 2026-09-03「完了履歴にボタンを置き、押せばすぐ開く」)。
 *
 * - 保存済みの job / job_items を読むだけ (RPA は回らない・read-only)。
 * - `window.open` は click ハンドラ内で同期的に呼ぶ (ポップアップブロック回避)。
 *   'noopener' を features に付けると window.open が null を返すため付けず、
 *   遷移後に win.opener = null で切り離す (ReconcileReportButton と同じ)。
 * - 表示条件 (対象 op か・完了しているか) は呼び出し側が `isReportableJob` で判定する。
 */
import { useCallback } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api-client';
import { useSyncReport } from '@/lib/queries/syncReport';

export interface SyncReportButtonProps {
  /** 対象ジョブ ID。 */
  jobId: string;
  /** ボタンの見た目 (置き場所に合わせる)。 */
  size?: 'sm' | 'md';
  /** ラベル (既定「📄 レポート」)。 */
  label?: string;
  /** 追加クラス (履歴行など狭い場所で高さを詰める用)。 */
  className?: string;
}

/** ApiError のステータスから現場向けの文言を作る。 */
function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 422) return 'このジョブは報告書の対象外です';
    if (e.status === 404) return 'ジョブが見つかりません';
    if (e.status === 403) return '管理者のみ開けます';
  }
  return e instanceof Error ? e.message : '不明なエラー';
}

export function SyncReportButton({
  jobId,
  size = 'sm',
  label = '📄 レポート',
  className,
}: SyncReportButtonProps) {
  const { mutateAsync, isPending } = useSyncReport();

  const run = useCallback(async () => {
    const win = typeof window !== 'undefined' ? window.open('', '_blank') : null;
    // 開く先が無いなら BE でレポートを組み立てさせない (無駄な生成を避ける)。
    if (!win) {
      toast.warning('ポップアップがブロックされました。ブロックを解除してもう一度お試しください。');
      return;
    }
    try {
      const r = await mutateAsync({ jobId });
      const url = URL.createObjectURL(new Blob([r.html], { type: 'text/html;charset=utf-8' }));
      win.location.href = url;
      try {
        win.opener = null;
      } catch {
        /* 一部ブラウザで読み取り専用 */
      }
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      win.close();
      toast.error(`レポートの生成に失敗しました: ${errorMessage(e)}`);
    }
  }, [mutateAsync, jobId]);

  return (
    <Button
      type="button"
      variant="outline"
      size={size}
      onClick={run}
      disabled={isPending}
      title="この連携ジョブの結果を印刷用 HTML（A4 縦）で開きます（read-only）"
      data-testid="sync-report-button"
      className={className}
    >
      {isPending ? '作成中…' : label}
    </Button>
  );
}
