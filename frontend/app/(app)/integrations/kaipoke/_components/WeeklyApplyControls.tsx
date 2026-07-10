'use client';

/**
 * WeeklyApplyControls — 週次反映ワークフロー (送る側) の操作部。
 *
 * 週セレクタ + ステップ ①〜④ + 確認ダイアログ + 接続設定未完了の案内 を描く。
 * 状態・ハンドラはすべて useWeeklyApply フックが持ち、ここは描画のみ (ロジック不変)。
 */
import { type ReactNode } from 'react';

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
import { Skeleton } from '@/components/ui/skeleton';

import { WeekDiffView } from './WeekDiffView';
import { type WeeklyApplyVm, nextWeekMonday } from './useWeeklyApply';

export function WeeklyApplyControls({ vm }: { vm: WeeklyApplyVm }) {
  const {
    busy,
    credentialsConfigured,
    weekStart,
    month,
    label,
    sheetId,
    summary,
    confirm,
    setConfirm,
    showDiffDetail,
    setShowDiffDetail,
    setWeekStart,
    expandStatus,
    expand,
    diffLocal,
    apply,
    itemsQuery,
    scheduleRows,
    items,
    total,
    unresolved,
    isExpanded,
    looksUnexpanded,
    resetDiff,
    shiftWeek,
    runExpand,
    runDiff,
    runApply,
  } = vm;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">週次反映ワークフロー</h2>
        <div className="flex items-center gap-1.5">
          <Button variant="outline" size="sm" onClick={() => shiftWeek(-1)}>
            ◀ 前の週
          </Button>
          <span className="min-w-[210px] rounded-md border border-border-default bg-bg-muted px-3 py-1.5 text-center text-sm font-medium tabular-nums text-text-primary">
            {label}
          </span>
          <Button variant="outline" size="sm" onClick={() => shiftWeek(1)}>
            次の週 ▶
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setWeekStart(nextWeekMonday());
              resetDiff();
            }}
          >
            翌週
          </Button>
        </div>
      </div>

      {/* 接続設定未完了の案内 */}
      {!credentialsConfigured && (
        <div className="mb-3 rounded-md border border-amber-500/50 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          先に接続設定（上のカード）を完了してください。
        </div>
      )}

      {/* ステップ ①〜④ (週セレクタの直下・モニターの見える範囲に集約) */}
      <div className="space-y-2">
        {/* ① 展開 */}
        <Step no="01" title={`スケジュール展開（月: ${month}）`}>
          <div className="flex flex-wrap items-center gap-2">
            {expandStatus.isLoading ? (
              <span className="text-xs text-text-muted">確認中…</span>
            ) : expandStatus.isError ? (
              // 破壊的操作の安全弁: 展開状況が確認できない時は操作をブロックする。
              <span className="text-xs text-error">
                展開状況を確認できませんでした。ページを再読み込みしてください。
              </span>
            ) : isExpanded ? (
              <>
                <span className="inline-flex items-center gap-1 rounded bg-success-bg px-2 py-1 text-xs font-medium text-success">
                  ✓ 展開済み
                </span>
                <button
                  type="button"
                  onClick={() => setConfirm('reexpand')}
                  disabled={busy || expand.isPending}
                  className="text-xs text-text-muted underline hover:text-error disabled:opacity-50"
                >
                  再展開（上書き・取り消し用）
                </button>
              </>
            ) : (
              <Button
                size="sm"
                onClick={() => setConfirm('expand')}
                disabled={!credentialsConfigured || busy || expand.isPending}
              >
                展開する
              </Button>
            )}
            <span className="text-xs text-text-muted">
              月1回のみ。カイポケに月間の予定枠を作成します（既存は上書き）。
            </span>
          </div>
        </Step>

        {/* ② 差分計算 */}
        <Step no="02" title="差分を計算（この週）">
          <div className="flex flex-wrap items-center gap-3">
            <Button
              size="sm"
              onClick={runDiff}
              disabled={
                !credentialsConfigured || diffLocal.isPending || scheduleRows.length === 0 || busy
              }
            >
              {diffLocal.isPending
                ? 'カイポケ現況を取得して計算中…（約1分）'
                : 'この週の差分を計算'}
            </Button>
            {sheetId && (
              <div className="flex flex-wrap items-center gap-1.5 text-sm">
                <Chip label="追加" value={summary?.add ?? 0} tone="success" />
                <Chip
                  label="編集"
                  value={(summary?.edit ?? 0) + (summary?.update ?? 0)}
                  tone="warning"
                />
                <Chip label="削除" value={summary?.delete ?? 0} tone="error" />
                {unresolved > 0 && <Chip label="要確認" value={unresolved} tone="warning" />}
              </div>
            )}
          </div>
          {diffLocal.isError && (
            <Alert variant="destructive" className="mt-2">
              <AlertTitle>差分計算に失敗しました</AlertTitle>
              <AlertDescription>
                {diffLocal.error instanceof Error ? diffLocal.error.message : '不明なエラー'}
              </AlertDescription>
            </Alert>
          )}
          {looksUnexpanded && (
            <Alert variant="warning" className="mt-2">
              <AlertTitle>展開がまだかもしれません</AlertTitle>
              <AlertDescription>
                差分がすべて「追加」です。カイポケにこの週の予定が無い可能性があります。
                ①のスケジュール展開が済んでいるか確認してください。
              </AlertDescription>
            </Alert>
          )}
        </Step>

        {/* ③ 確認 */}
        <Step no="03" title="変更内容を確認">
          {!sheetId ? (
            <p className="text-xs text-text-muted">②で差分を計算すると、ここに変更内容が出ます。</p>
          ) : total === 0 ? (
            <p className="text-sm text-success">
              差分なし。カイポケの現況とらく助の予定は一致しています（反映不要）。
            </p>
          ) : (
            <div>
              <button
                type="button"
                onClick={() => setShowDiffDetail((v) => !v)}
                className="text-xs font-medium text-brand-primary hover:underline"
              >
                {showDiffDetail ? '変更の詳細を隠す ▲' : `変更の詳細を見る（${total}件）▼`}
              </button>
              {showDiffDetail && (
                <div className="mt-2">
                  {itemsQuery.isLoading ? (
                    <Skeleton className="h-32 w-full" />
                  ) : (
                    <WeekDiffView weekStart={weekStart} items={items} />
                  )}
                </div>
              )}
            </div>
          )}
        </Step>

        {/* ④ 反映 */}
        <Step no="04" title="カイポケへ反映">
          {!sheetId || total === 0 ? (
            <p className="text-xs text-text-muted">
              差分がある場合に、dry-run で確認 → 本番反映ができます。
            </p>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setConfirm('dry')}
                disabled={!credentialsConfigured || busy}
              >
                dry-run で確認（書込なし）
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setConfirm('real')}
                disabled={!credentialsConfigured || busy}
              >
                この週で本番反映
              </Button>
              <span className="text-xs text-text-muted">
                対象週外のカイポケ予定は変更されません。
              </span>
            </div>
          )}
        </Step>
      </div>

      {/* 確認ダイアログ */}
      <Dialog open={confirm !== null} onOpenChange={(o) => !o && setConfirm(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {confirm === 'expand' && 'この月を展開しますか？'}
              {confirm === 'reexpand' && '再展開しますか？（上書き注意）'}
              {confirm === 'real' && 'この週で本番反映しますか？'}
              {confirm === 'dry' && 'dry-run で確認しますか？'}
            </DialogTitle>
            <DialogDescription className="space-y-2">
              {(confirm === 'expand' || confirm === 'reexpand') && (
                <>
                  <span className="block">
                    対象月: <span className="font-medium text-text-primary">{month}</span>
                  </span>
                  <span className="block text-error">
                    ⚠️ 展開はカイポケに月間の予定枠を作成します。
                    {confirm === 'reexpand'
                      ? 'この月は既に展開済みです。再展開すると、これまでにカイポケへ入力・反映した予定がすべて上書き（消去）されます。取り消し/リセット目的の時だけ実行してください。'
                      : '既にカイポケに手入力した予定がある場合は上書きされます。この月で初めての反映であることを確認してください。'}
                  </span>
                  <span className="block text-text-muted">所要時間: 約15〜20分</span>
                </>
              )}
              {(confirm === 'real' || confirm === 'dry') && (
                <>
                  <span className="block">
                    対象週: <span className="font-medium text-text-primary">{label}</span>（{total}
                    件）
                  </span>
                  {confirm === 'real' ? (
                    <span className="block text-error">
                      カイポケに実際に書き込まれます。対象週外は変更されませんが、この操作は Ctrl+Z
                      の対象外です。
                    </span>
                  ) : (
                    <span className="block">
                      カイポケへの書込はありません。操作の様子だけをシミュレーションで確認します。
                    </span>
                  )}
                  {unresolved > 0 && (
                    <span className="block text-warning-strong">
                      ※ らく助未登録の利用者 {unresolved} 件は安全にスキップされます。
                    </span>
                  )}
                </>
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(null)}>
              やめる
            </Button>
            <Button
              variant={confirm === 'dry' ? 'default' : 'destructive'}
              disabled={apply.isPending || expand.isPending}
              onClick={() => {
                if (confirm === 'expand' || confirm === 'reexpand') void runExpand();
                else void runApply(confirm === 'dry');
              }}
            >
              {confirm === 'expand' && '展開する'}
              {confirm === 'reexpand' && '上書きして再展開する'}
              {confirm === 'real' && '本番反映する'}
              {confirm === 'dry' && 'dry-run を実行'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Step({ no, title, children }: { no: string; title: string; children: ReactNode }) {
  return (
    <div className="flex gap-3 rounded-lg border border-border-default bg-bg-base p-3">
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-primary font-mono text-xs font-bold text-white">
        {no}
      </span>
      <div className="min-w-0 flex-1 space-y-1.5">
        <p className="text-sm font-semibold text-text-primary">{title}</p>
        {children}
      </div>
    </div>
  );
}

function Chip({
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
