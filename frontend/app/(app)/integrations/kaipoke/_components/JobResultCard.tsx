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

  // 失敗/スキップの個別明細 (どの訪問が未登録か)。カイポケ result.details 由来。
  const details = Array.isArray(result.details)
    ? (result.details as Array<Record<string, unknown>>)
    : [];
  const problems = details.filter((d) => typeof d.status === 'string' && d.status !== 'success');

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

      {/* 要対応: 失敗/スキップの明細 (カイポケに未登録の可能性) */}
      {problems.length > 0 && (
        <div className="mt-4 rounded-lg border border-border-warning bg-warning-bg p-3">
          <p className="mb-2 text-sm font-semibold text-warning-strong">
            要対応 — 失敗・スキップ {problems.length}件（カイポケに未登録の可能性）
          </p>
          <div className="max-h-56 overflow-y-auto rounded border border-border-warning bg-bg-base">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-bg-muted text-left text-text-secondary">
                <tr>
                  <th className="px-2 py-1 font-medium">利用者/職員</th>
                  <th className="px-2 py-1 font-medium">日</th>
                  <th className="px-2 py-1 font-medium">操作</th>
                  <th className="px-2 py-1 font-medium">状態</th>
                  <th className="px-2 py-1 font-medium">理由</th>
                </tr>
              </thead>
              <tbody>
                {problems.map((d, i) => (
                  <tr key={i} className="border-t border-border-subtle">
                    <td className="px-2 py-1 text-text-primary">
                      {String(d.user ?? d.staff ?? '—')}
                    </td>
                    <td className="px-2 py-1 tabular-nums text-text-secondary">
                      {String(d.date ?? '')}
                    </td>
                    <td className="px-2 py-1 text-text-secondary">{String(d.action ?? '')}</td>
                    <td className="px-2 py-1">
                      <span
                        className={
                          d.status === 'skipped' ? 'text-warning-strong' : 'font-medium text-error'
                        }
                      >
                        {d.status === 'skipped' ? 'スキップ' : '失敗'}
                      </span>
                    </td>
                    <td className="px-2 py-1 text-text-muted">{String(d.reason ?? '')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-warning-strong">
            上記はカイポケに未登録の可能性があります。カイポケ画面で手動確認・登録してください。
          </p>
        </div>
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
