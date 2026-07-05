'use client';

/**
 * JobResultCard — 直近ジョブの最終結果サマリ。
 *
 * latestJob.result_summary（reconcile 後の完了サマリ）を読み、成功/失敗/
 * スキップの内訳と行数・エラーを落ち着いたトーンで提示する。旧 GAS の
 * 「適用結果」シート（緑/赤/黄）を CareFlow のトーンで再構成したもの。
 */
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';

import type { KaipokeJob } from '@/lib/schemas/integration';
import { commandLabel } from './JobProgressCard';

function num(v: unknown): number | null {
  return typeof v === 'number' ? v : null;
}

export function JobResultCard({ job }: { job: KaipokeJob }) {
  const summary = (job.result_summary ?? {}) as Record<string, unknown>;
  const result = (summary.result ?? {}) as Record<string, unknown>;
  const op = commandLabel((job.params as Record<string, unknown>)?.op as string) ?? job.job_type;

  const success = num(result.success);
  const failed = num(result.failed);
  const skipped = num(result.skipped);
  const rowCount = num(result.row_count);
  const errorMsg = typeof summary.error === 'string' ? summary.error : null;
  const hasBreakdown = success !== null || failed !== null || skipped !== null;

  const statusVariant =
    job.status === 'completed'
      ? 'success'
      : job.status === 'failed'
        ? 'destructive'
        : job.status === 'cancelled'
          ? 'warning'
          : 'secondary';

  return (
    <Card className="p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-serif text-lg font-bold text-text-primary">直近の実行結果 — {op}</h2>
        <Badge variant={statusVariant}>{job.status}</Badge>
      </div>

      {errorMsg && (
        <Alert variant="destructive" className="mb-3">
          <AlertTitle>エラーで終了しました</AlertTitle>
          <AlertDescription className="break-all">{errorMsg}</AlertDescription>
        </Alert>
      )}

      {hasBreakdown ? (
        <div className="grid grid-cols-3 gap-3">
          <ResultStat label="成功" value={success} tone="success" />
          <ResultStat label="失敗" value={failed} tone="error" />
          <ResultStat label="スキップ" value={skipped} tone="warning" />
        </div>
      ) : rowCount !== null ? (
        <p className="text-sm text-text-secondary">
          取得件数: <span className="font-mono font-medium text-text-primary">{rowCount}</span> 行
        </p>
      ) : (
        <p className="text-sm text-text-muted">この実行に詳細サマリはありません。</p>
      )}

      {job.completed_at && <p className="mt-3 text-xs text-text-muted">完了: {job.completed_at}</p>}
    </Card>
  );
}

function ResultStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | null;
  tone: 'success' | 'error' | 'warning';
}) {
  const toneClass =
    tone === 'success'
      ? 'bg-success-bg text-success'
      : tone === 'error'
        ? 'bg-error-bg text-error'
        : 'bg-warning-bg text-warning-strong';
  return (
    <div className={`rounded-lg px-4 py-3 text-center ${toneClass}`}>
      <div className="font-mono text-2xl font-bold tabular-nums leading-none">{value ?? 0}</div>
      <div className="mt-1.5 text-xs font-medium opacity-80">{label}</div>
    </div>
  );
}
