'use client';
/**
 * 「📄 A4 で出力」ボタン — 訪問記録 1 件を A4 縦の独立 HTML で新しいタブに開く
 * （設計 §11-3。`components/integrations/SyncReportButton.tsx` の複製）。
 *
 * - 保存済みの記録を読んで BE が組み立てるだけ（AI は回らない・read-only）。
 * - `window.open` は click ハンドラ内で**同期的に**呼ぶ（ポップアップブロック回避）。
 *   'noopener' を features に付けると `window.open` が null を返すため付けず、
 *   遷移後に `win.opener = null` で切り離す（SyncReportButton と同じ）。
 * - 開く先が無いなら BE にレポートを作らせない（無駄な生成を避ける）。
 * - 権限（admin / 本人）の判定は呼び出し側が行い、`disabled` で渡す
 *   （RBAC は「全ロール同一表示・権限外は disabled」＝ PO 決定）。
 */
import { useCallback } from 'react';

import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/sonner';
import { ApiError } from '@/lib/api-client';
import { useRecordReport } from '@/lib/queries/visit-recordings';

export interface RecordReportButtonProps {
  /** 対象の記録 ID。 */
  recordingId: string;
  /** ボタンの見た目（置き場所に合わせる）。 */
  size?: 'sm' | 'md';
  /** ラベル（既定「📄 A4 で出力」）。 */
  label?: string;
  /** 権限・読込中などで押させない場合。 */
  disabled?: boolean;
  /** 押せない理由（`title` にそのまま出す）。 */
  title?: string;
  className?: string;
}

/** ApiError のステータスから現場向けの文言を作る。 */
function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 404) return '記録が見つかりません';
    if (e.status === 403) return '管理者と本人のみ出力できます';
    if (e.status === 410) return 'この記録は削除済みです';
    if (e.status === 422) return 'この記録はまだ出力できません（要約の完了をお待ちください）';
  }
  return e instanceof Error ? e.message : '不明なエラー';
}

export function RecordReportButton({
  recordingId,
  size = 'sm',
  label = '📄 A4 で出力',
  disabled = false,
  title,
  className,
}: RecordReportButtonProps) {
  const { mutateAsync, isPending } = useRecordReport();

  const run = useCallback(async () => {
    const win = typeof window !== 'undefined' ? window.open('', '_blank') : null;
    if (!win) {
      toast.warning('ポップアップがブロックされました。ブロックを解除してもう一度お試しください。');
      return;
    }
    try {
      const html = await mutateAsync({ recordingId });
      const url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
      win.location.href = url;
      try {
        win.opener = null;
      } catch {
        /* 一部ブラウザで読み取り専用 */
      }
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      win.close();
      toast.error(`A4 の出力に失敗しました: ${errorMessage(e)}`);
    }
  }, [mutateAsync, recordingId]);

  return (
    <Button
      type="button"
      variant="outline"
      size={size}
      onClick={() => void run()}
      disabled={disabled || !recordingId || isPending}
      title={title ?? 'この訪問記録を印刷用 HTML（A4 縦）で開きます（read-only）'}
      data-testid="record-report-button"
      className={className}
    >
      {isPending ? '作成中…' : label}
    </Button>
  );
}
