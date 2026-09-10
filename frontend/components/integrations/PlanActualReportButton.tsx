'use client';
/**
 * 「📄 予実レポート」ボタン — 指定月の予実比較 (カイポケの予定 × 実績) を
 * A4 の独立 HTML で新しいタブに開く (read-only・RPA は回らない)。
 *
 * - `window.open` は click ハンドラ内で同期的に呼ぶ (ポップアップブロック回避)。
 *   'noopener' を features に付けると window.open が null を返すため付けず、
 *   遷移後に win.opener = null で切り離す (SyncReportButton と同じ)。
 * - 実績スナップショットが無い月は BE が 404 を返す → 「先に取得してください」と案内する。
 */
import { useCallback } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api-client';
import { planActualErrorDetail, usePlanActualReport } from '@/lib/queries/planActualReport';

export interface PlanActualReportButtonProps {
  /** 対象月 (YYYY-MM)。 */
  month: string;
  /** ボタンの見た目 (置き場所に合わせる)。 */
  size?: 'sm' | 'md';
  /** ラベル (既定「📄 予実レポート」)。 */
  label?: string;
  /** 追加クラス (履歴行など狭い場所で高さを詰める用)。 */
  className?: string;
}

/** 「9月」— YYYY-MM から月だけ取り出す (壊れた値はそのまま出す)。 */
function monthLabel(month: string): string {
  const m = /^\d{4}-(\d{2})$/.exec(month);
  return m ? `${Number(m[1])}月` : month;
}

/**
 * 現場向けの失敗文言。
 * 404 は BE が「予定CSVが無い / 実績CSVが無い」を撃ち分けるので、その detail を優先し、
 * detail が無いときだけ「先に取得してください」の定型文に落とす。
 */
export function planActualReportErrorMessage(e: unknown, month: string): string {
  if (e instanceof ApiError && e.status === 404) {
    return (
      planActualErrorDetail(e) ??
      `${monthLabel(month)}の実績データがまだありません。先に「実績を取得して比較」を実行してください`
    );
  }
  if (e instanceof ApiError && e.status === 403) return '管理者のみ開けます';
  return e instanceof Error ? e.message : '不明なエラー';
}

export function PlanActualReportButton({
  month,
  size = 'sm',
  label = '📄 予実レポート',
  className,
}: PlanActualReportButtonProps) {
  const { mutateAsync, isPending } = usePlanActualReport();

  const run = useCallback(async () => {
    const win = typeof window !== 'undefined' ? window.open('', '_blank') : null;
    // 開く先が無いなら BE でレポートを組み立てさせない (無駄な生成を避ける)。
    if (!win) {
      toast.warning('ポップアップがブロックされました。ブロックを解除してもう一度お試しください。');
      return;
    }
    try {
      const r = await mutateAsync({ month });
      const url = URL.createObjectURL(r.blob);
      win.location.href = url;
      try {
        win.opener = null;
      } catch {
        /* 一部ブラウザで読み取り専用 */
      }
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      win.close();
      toast.error(planActualReportErrorMessage(e, month));
    }
  }, [mutateAsync, month]);

  return (
    <Button
      type="button"
      variant="outline"
      size={size}
      onClick={run}
      disabled={isPending}
      title={`${monthLabel(month)}の予実比較（予定×実績）を印刷用 HTML（A4）で開きます（read-only）`}
      data-testid="plan-actual-report-button"
      className={className}
    >
      {isPending ? '作成中…' : label}
    </Button>
  );
}
