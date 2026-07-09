'use client';

/**
 * InboundControls — カイポケ → CareFlow 取り込みの操作部。
 *
 * 対象週チップ・❶差分取得・SummaryChip 群・❷曜日選択チップ・❸ dry-run/実取り込み・
 * dry-run 結果テーブル・二重確認ダイアログを描く。6列プレビュー (InboundDiffView) は
 * 下段のカレンダー枠 (InboundCalendar) に分離した。状態・ハンドラは useInbound が持つ。
 */
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { ApplyInboundResultItem } from '@/lib/schemas/integration';

import { type InboundVm, WEEKDAYS, fmtWeekLabel } from './useInbound';

const OUTCOME_META: Record<string, { label: string; cls: string }> = {
  cancelled: { label: 'キャンセル', cls: 'bg-error-bg text-error' },
  updated: { label: '更新', cls: 'bg-success-bg text-success' },
  added: { label: '追加', cls: 'bg-info-bg text-info' },
  skipped: { label: 'スキップ', cls: 'bg-bg-muted text-text-muted' },
  failed: { label: '失敗', cls: 'bg-error-bg text-error' },
};

type WeekOption = 'this' | 'next';

export function InboundControls({ vm }: { vm: InboundVm }) {
  const {
    busy,
    credentialsConfigured,
    thisMonday,
    nextMonday,
    selectedWeek,
    weekStart,
    thisElig,
    nextElig,
    currentElig,
    eligible,
    diffInbound,
    applyInbound,
    weekDays,
    daysWithDiff,
    sheetId,
    summary,
    selectedDays,
    setSelectedDays,
    dryRunResult,
    setDryRunResult,
    confirm,
    setConfirm,
    handleWeekChange,
    runDiff,
    runApply,
    hasSelectedDays,
    selectedDayLabels,
  } = vm;

  return (
    <div>
      {/* ── ヘッダー ── */}
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">カイポケから取り込む</h2>
        <span className="inline-flex items-center rounded px-2 py-1 text-xs font-medium bg-warning-bg text-warning-strong">
          カイポケが正
        </span>
      </div>
      <p className="mb-4 text-sm text-text-secondary">
        カイポケでの直し込みを CareFlow に取り込みます。
        <strong className="text-text-primary">今週の予定表だけ</strong>
        を直します（定期パターンは変わりません）。
      </p>

      {/* 接続設定未完了の案内 */}
      {!credentialsConfigured && (
        <div className="mb-4 rounded-md border border-amber-500/50 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          先に接続設定（上のカード）を完了してください。
        </div>
      )}

      {/* ── 週選択チップ ── */}
      <div className="mb-5">
        <p className="mb-2 text-xs font-medium text-text-secondary">対象週</p>
        <div className="flex flex-wrap gap-2">
          {(['this', 'next'] as WeekOption[]).map((w) => {
            const mon = w === 'this' ? thisMonday : nextMonday;
            const elig = w === 'this' ? thisElig : nextElig;
            const isEligible = elig.data?.eligible ?? false;
            const isLoading = elig.isLoading;
            const isSelected = selectedWeek === w;
            return (
              <button
                key={w}
                type="button"
                disabled={isLoading || !isEligible}
                onClick={() => handleWeekChange(w)}
                className={[
                  'rounded-full border px-3 py-1.5 text-sm font-medium transition-colors',
                  isSelected && isEligible
                    ? 'border-brand-primary bg-brand-primary text-white'
                    : isEligible
                      ? 'border-border-default bg-bg-base text-text-primary hover:bg-bg-muted'
                      : 'cursor-not-allowed border-border-subtle bg-bg-muted text-text-muted opacity-60',
                ].join(' ')}
              >
                {w === 'this' ? '今週' : '来週'}
                <span className="ml-2 text-xs tabular-nums">{fmtWeekLabel(mon)}</span>
              </button>
            );
          })}
        </div>
        {!currentElig.isLoading && !eligible && (
          <p className="mt-2 text-xs text-text-muted">
            先に④反映（送る）を済ませてください。apply 実績のある週のみ取り込み可能です。
          </p>
        )}
      </div>

      {/* ── 操作エリア（eligible でない場合はグレーアウト） ── */}
      <div className={eligible ? '' : 'pointer-events-none opacity-40'}>
        {/* ❶ 差分取得 */}
        <div className="mb-5 space-y-2">
          <Button
            size="sm"
            onClick={() => void runDiff()}
            disabled={!credentialsConfigured || !eligible || diffInbound.isPending || busy}
          >
            {diffInbound.isPending
              ? 'カイポケ現況を取得中…（約1分）'
              : '❶ カイポケの現況を取得して差分を見る'}
          </Button>
          {diffInbound.isPending && (
            <p className="text-xs text-text-muted">
              カイポケからエクスポートして差分を計算しています。約1分かかります。
            </p>
          )}
          {sheetId && summary !== null && (
            <div className="flex flex-wrap items-center gap-1.5">
              <SummaryChip label="キャンセル候補" value={summary.delete ?? 0} tone="error" />
              <SummaryChip
                label="時間変更"
                value={(summary.edit ?? 0) + (summary.date_change ?? 0)}
                tone="warning"
              />
              <SummaryChip label="カイポケのみ" value={summary.add ?? 0} tone="success" />
              {(summary.unresolved_patient ?? 0) > 0 && (
                <SummaryChip
                  label="要確認"
                  value={summary.unresolved_patient ?? 0}
                  tone="warning"
                />
              )}
            </div>
          )}
          {diffInbound.isError && (
            <Alert variant="destructive">
              <AlertTitle>差分取得に失敗しました</AlertTitle>
              <AlertDescription>
                {diffInbound.error instanceof Error ? diffInbound.error.message : '不明なエラー'}
              </AlertDescription>
            </Alert>
          )}
        </div>

        {/* ❷ 曜日選択 */}
        {sheetId && (
          <div className="mb-5 space-y-3">
            <div>
              <p className="mb-2 text-xs font-medium text-text-secondary">
                取り込む曜日（差分のある曜日のみ選択可）
              </p>
              <div className="flex flex-wrap gap-1.5">
                {weekDays.map((d, i) => {
                  const hasItems = daysWithDiff.has(d);
                  const isChosen = selectedDays.has(d);
                  return (
                    <button
                      key={d}
                      type="button"
                      disabled={!hasItems}
                      onClick={() => {
                        const next = new Set(selectedDays);
                        if (isChosen) next.delete(d);
                        else next.add(d);
                        setSelectedDays(next);
                        setDryRunResult(null);
                      }}
                      className={[
                        'rounded border px-2.5 py-1 text-xs font-medium transition-colors',
                        isChosen
                          ? 'border-brand-primary bg-brand-primary text-white'
                          : hasItems
                            ? 'border-border-default bg-bg-base text-text-primary hover:bg-bg-muted'
                            : 'cursor-not-allowed border-border-subtle bg-bg-muted text-text-muted opacity-50',
                      ].join(' ')}
                    >
                      {WEEKDAYS[i]}
                    </button>
                  );
                })}
                <button
                  type="button"
                  onClick={() => {
                    setSelectedDays(new Set(weekDays.filter((d) => daysWithDiff.has(d))));
                    setDryRunResult(null);
                  }}
                  className="rounded border border-border-default bg-bg-base px-2.5 py-1 text-xs font-medium text-text-primary hover:bg-bg-muted"
                >
                  全部
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ❸ dry-run + 実取り込み */}
        {sheetId && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => void runApply(true)}
                disabled={
                  !credentialsConfigured || !hasSelectedDays || applyInbound.isPending || busy
                }
              >
                {applyInbound.isPending && !dryRunResult ? '実行中…' : '❸ dry-run で確認'}
              </Button>
              {dryRunResult && (
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => setConfirm(true)}
                  disabled={busy || applyInbound.isPending}
                >
                  選んだ曜日を取り込む
                </Button>
              )}
              {!hasSelectedDays && (
                <span className="text-xs text-text-muted">曜日を1つ以上選択してください</span>
              )}
            </div>

            {applyInbound.isError && (
              <Alert variant="destructive">
                <AlertTitle>実行に失敗しました</AlertTitle>
                <AlertDescription>
                  {applyInbound.error instanceof Error
                    ? applyInbound.error.message
                    : '不明なエラー'}
                </AlertDescription>
              </Alert>
            )}

            {/* dry-run 結果テーブル */}
            {dryRunResult && (
              <div className="overflow-x-auto rounded-lg border border-border-default">
                <table className="min-w-full text-xs">
                  <thead>
                    <tr className="border-b border-border-default bg-bg-muted">
                      <th className="px-3 py-2 text-left font-medium text-text-secondary">
                        利用者
                      </th>
                      <th className="px-3 py-2 text-left font-medium text-text-secondary">日付</th>
                      <th className="px-3 py-2 text-left font-medium text-text-secondary">操作</th>
                      <th className="px-3 py-2 text-left font-medium text-text-secondary">結果</th>
                      <th className="px-3 py-2 text-left font-medium text-text-secondary">詳細</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dryRunResult.results.map((r) => (
                      <DryRunRow key={r.itemId} result={r} />
                    ))}
                  </tbody>
                </table>
                <div className="border-t border-border-default bg-bg-muted px-3 py-2 text-xs text-text-secondary">
                  キャンセル: {dryRunResult.cancelled} / 更新: {dryRunResult.updated} / 追加:{' '}
                  {dryRunResult.added} / スキップ: {dryRunResult.skipped} / 失敗:{' '}
                  {dryRunResult.failed}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── 二重確認ダイアログ ── */}
      <Dialog open={confirm} onOpenChange={(o) => !o && setConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>選んだ曜日を取り込みますか？</DialogTitle>
            <DialogDescription className="space-y-2">
              <span className="block">
                対象週:{' '}
                <span className="font-medium text-text-primary">{fmtWeekLabel(weekStart)}</span>
              </span>
              <span className="block">
                対象曜日: <span className="font-medium text-text-primary">{selectedDayLabels}</span>
              </span>
              <span className="block text-error">
                CareFlow のスケジュールに実際に書き込まれます。この操作は Ctrl+Z
                の対象外です（定期パターンは変わりません）。
              </span>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(false)}>
              やめる
            </Button>
            <Button
              variant="destructive"
              disabled={applyInbound.isPending}
              onClick={() => void runApply(false)}
            >
              取り込む
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function SummaryChip({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: 'success' | 'warning' | 'error';
}) {
  const cls =
    tone === 'success'
      ? 'bg-success-bg text-success'
      : tone === 'error'
        ? 'bg-error-bg text-error'
        : 'bg-warning-bg text-warning-strong';
  return (
    <span className={`inline-flex items-center gap-1 rounded px-2 py-1 text-xs ${cls}`}>
      {label} <span className="font-mono font-bold tabular-nums">{value}</span>
    </span>
  );
}

function DryRunRow({ result }: { result: ApplyInboundResultItem }) {
  const meta = OUTCOME_META[result.outcome] ?? {
    label: result.outcome,
    cls: 'bg-bg-muted text-text-muted',
  };
  return (
    <tr className="border-b border-border-subtle">
      <td className="px-3 py-2 text-text-primary">{result.patientName ?? '—'}</td>
      <td className="px-3 py-2 tabular-nums text-text-secondary">{result.date ?? '—'}</td>
      <td className="px-3 py-2 text-text-secondary">{result.action}</td>
      <td className="px-3 py-2">
        <span
          className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ${meta.cls}`}
        >
          {meta.label}
        </span>
      </td>
      <td className="px-3 py-2 text-text-muted">{result.detail ?? ''}</td>
    </tr>
  );
}
