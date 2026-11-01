/**
 * 「月の実績」の段ごとの操作：読み取る・試しに流す・反映する（すぐ／今夜 22 時）・確かめる・一覧を出す。
 * 表示だけ（押したときの処理は props で受け取る）。
 *
 * 反映と予約の前には、月と件数を書いた確認を画面の中で出す（confirm() は使わない）。
 */

import { useState } from 'react';

import { Button } from '@/components/ui/button';
import type { MonthlyActualJobSummary } from '@/lib/schemas/monthly-actual';

import { type StepNo, isJobRunning, isJobScheduled, monthLabel } from './monthlyActualFormat';

type ConfirmKind = 'apply' | 'schedule' | 'retry';

export interface MonthlyActualProgress {
  done: number;
  total: number;
  success: number;
  failed: number;
}

export interface MonthlyActualRunPanelProps {
  step: StepNo;
  month: string;
  /** 反映は終わった月だけ。 */
  isPastMonth: boolean;
  /** これから送る行（「する」・まだ反映していない）の数。 */
  selectedCount: number;
  /** 前の反映で失敗した行の数。 */
  failedCount: number;
  job?: MonthlyActualJobSummary | null;
  /** ロボットの進み具合（動いている間だけ）。 */
  progress?: MonthlyActualProgress | null;
  /** カイポケの実績を読み取っている。 */
  refreshing: boolean;
  /** 送信中（二度押しを防ぐ）。 */
  busy: boolean;
  exporting?: boolean;
  /** 看護師別の月の実績の A4 を開いている。 */
  reportOpening?: boolean;
  onRefresh: () => void;
  onDryRun: () => void;
  onApply: () => void;
  onSchedule: () => void;
  onCancelSchedule: () => void;
  onRetryFailed: () => void;
  onExport: () => void;
  /** 看護師別の月の実績を A4 で開く（ブラウザの印刷で PDF）。 */
  onOpenReport?: () => void;
}

function formatAt(iso: string | null | undefined): string {
  if (!iso) return '今夜 22 時';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(
    d.getMinutes(),
  ).padStart(2, '0')}`;
}

function Progress({ progress, dryRun }: { progress: MonthlyActualProgress; dryRun: boolean }) {
  const { done, total, success, failed } = progress;
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  return (
    <div className="space-y-1" data-testid="ma-progress">
      <div className="h-2.5 overflow-hidden rounded-full bg-bg-muted">
        <i className="block h-full bg-brand-primary transition-all" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-xs text-text-secondary">
        ロボットが{dryRun ? '試しに流して' : '反映して'}います：{done} / {total} 件（成功 {success}
        ・失敗 {failed}）。PC を閉じても、サーバーで続きます。
      </p>
    </div>
  );
}

export function MonthlyActualRunPanel(props: MonthlyActualRunPanelProps) {
  const { step, month, isPastMonth, selectedCount, failedCount, job, busy, exporting, refreshing } =
    props;
  const [confirm, setConfirm] = useState<ConfirmKind | null>(null);
  const running = isJobRunning(job);
  const scheduled = isJobScheduled(job);
  const label = monthLabel(month);
  const progress = props.progress ?? {
    done: (job?.success ?? 0) + (job?.failed ?? 0),
    total: job?.total ?? 0,
    success: job?.success ?? 0,
    failed: job?.failed ?? 0,
  };
  const canSend = isPastMonth && !busy && !running && !refreshing;

  const confirmText: Record<ConfirmKind, string> = {
    apply: `${label}の ${selectedCount} 件を、いまカイポケへ反映します。よろしいですか。`,
    schedule: `${label}の ${selectedCount} 件を、今夜 22 時にカイポケへ反映します。よろしいですか。`,
    retry: `${label}の失敗した ${failedCount} 件だけを、もう一度カイポケへ反映します。よろしいですか。`,
  };
  const confirmGo: Record<ConfirmKind, () => void> = {
    apply: props.onApply,
    schedule: props.onSchedule,
    retry: props.onRetryFailed,
  };

  const refreshButton = (text: string) => (
    <Button type="button" disabled={busy || refreshing || running} onClick={props.onRefresh}>
      {refreshing ? '読み取っています…' : text}
    </Button>
  );

  const dryRunButton = (text: string) => (
    <Button type="button" disabled={!canSend || selectedCount === 0} onClick={props.onDryRun}>
      {text}
    </Button>
  );

  return (
    <div
      className="space-y-3 rounded-lg border border-border-default bg-bg-base p-4"
      data-testid="ma-run-panel"
    >
      {!isPastMonth && (
        <p className="rounded-md bg-info-bg px-3 py-2 text-sm" data-testid="ma-not-past">
          反映できるのは終わった月だけです。{label}は、月が明けてから反映してください。
        </p>
      )}
      {job?.error && !running && (
        <p className="rounded-md bg-error-bg px-3 py-2 text-sm text-error">
          ロボットが止まりました：{job.error}
        </p>
      )}

      {step === 1 && (
        <>
          <h3 className="text-base font-bold">カイポケの実績を読み取る</h3>
          <p className="text-sm leading-relaxed">
            カイポケの{label}
            の実績を書き出して取り込みます。読むだけで、カイポケは変わりません（1〜2
            分）。らく助の側は、打刻履歴で合わせた時刻を含めた実績を使います。
          </p>
          {refreshButton('カイポケの実績を読み取る')}
        </>
      )}

      {step === 2 && (
        <>
          <h3 className="text-base font-bold">試しに流す（登録しない）</h3>
          <p className="text-sm leading-relaxed">
            ロボットがカイポケの実績の行を開き、時刻を入れるところまで行います。「登録する」は押さずに閉じます。
          </p>
          {dryRunButton(`この ${selectedCount} 件で試しに流す`)}
        </>
      )}

      {step === 3 && (
        <>
          <h3 className="text-base font-bold">試しに流す（登録しない）</h3>
          {running ? (
            <Progress progress={progress} dryRun />
          ) : (
            <>
              <p className="rounded-md bg-warning-bg px-3 py-2 text-sm" data-testid="ma-dry-ng">
                試しで時刻を入れるところまで進まなかった行か、まだ試していない行があります。表の「結果」を見て、「しない」にするか、打刻履歴で合わせてから、もう一度試してください。
              </p>
              {dryRunButton(`この ${selectedCount} 件で、もう一度試しに流す`)}
            </>
          )}
        </>
      )}

      {step === 4 && (
        <>
          {running ? (
            <>
              <h3 className="text-base font-bold">反映する</h3>
              <Progress progress={progress} dryRun={false} />
            </>
          ) : scheduled ? (
            <div className="space-y-2">
              <p className="rounded-md bg-info-bg px-3 py-2 text-sm" data-testid="ma-scheduled">
                {formatAt(job?.scheduled_at)} に流す予約をしました。PC
                を閉じてもかまいません。朝にこの画面で結果を見てください。
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={props.onCancelSchedule}
              >
                予約を取り消す
              </Button>
            </div>
          ) : (
            <>
              <p className="rounded-md bg-success-bg px-3 py-2 text-sm" data-testid="ma-dry-ok">
                試しに流した {selectedCount}{' '}
                件は、すべて時刻を入れるところまで進みました（登録はしていません）。
              </p>
              <h3 className="text-base font-bold">反映する</h3>
              <p className="text-sm leading-relaxed">
                選んだ {selectedCount}{' '}
                件の実績の時刻を、らく助の実績に合わせて登録します。提供時間はカイポケが計算し直します。夜に流すと、事務所のカイポケの作業とぶつかりません。
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  disabled={!canSend || selectedCount === 0 || confirm !== null}
                  onClick={() => setConfirm('apply')}
                >
                  {selectedCount} 件をすぐ反映する
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={!canSend || selectedCount === 0 || confirm !== null}
                  onClick={() => setConfirm('schedule')}
                >
                  今夜 22 時に流す
                </Button>
              </div>
            </>
          )}
        </>
      )}

      {step === 5 && (
        <>
          <p className="rounded-md bg-success-bg px-3 py-2 text-sm" data-testid="ma-applied">
            {job?.success ?? 0} 件を登録しました。
            {failedCount > 0 && `失敗 ${failedCount} 件（理由は表の「結果」の欄）。`}
          </p>
          <h3 className="text-base font-bold">確かめる</h3>
          <p className="text-sm leading-relaxed">
            もう一度カイポケの実績を読み取り、反映した行の差が 0 になったかを見ます。
          </p>
          <div className="flex flex-wrap gap-2">
            {refreshButton('カイポケの実績を読み取り直す')}
            {failedCount > 0 && (
              <Button
                type="button"
                variant="outline"
                disabled={!canSend || confirm !== null}
                onClick={() => setConfirm('retry')}
              >
                失敗した {failedCount} 件だけ流し直す
              </Button>
            )}
          </div>
        </>
      )}

      {step === 6 && (
        <>
          <h3 className="text-base font-bold">月の一覧を出す</h3>
          <p className="text-sm leading-relaxed">
            反映した後のカイポケの実績をもとに、月の一覧を Excel
            で書き出します。反映しなかった行は、突き合わせの表に残ります。看護師別の月の実績は、下の表と
            A4（印刷で PDF にできます）でも見られます。
          </p>
          <div className="flex flex-wrap gap-2">
            <Button type="button" disabled={exporting} onClick={props.onExport}>
              {exporting ? '書き出しています…' : 'Excel を書き出す'}
            </Button>
            {props.onOpenReport && (
              <Button
                type="button"
                variant="outline"
                disabled={props.reportOpening}
                onClick={props.onOpenReport}
                data-testid="ma-open-report"
              >
                {props.reportOpening ? '開いています…' : 'A4 で開く（印刷で PDF）'}
              </Button>
            )}
          </div>
        </>
      )}

      {(step === 5 || step === 6) && selectedCount - failedCount > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-t border-border-default pt-3 text-sm">
          <span className="text-text-secondary">
            まだ反映していない「する」の行が {selectedCount} 件あります。
          </span>
          {dryRunButton(`${selectedCount} 件で試しに流す`)}
        </div>
      )}

      {confirm && (
        <div
          role="alertdialog"
          aria-label="カイポケへ反映する前の確認"
          className="space-y-2 rounded-lg border border-border-warning bg-warning-bg p-3 text-sm"
          data-testid="ma-confirm"
        >
          <p>{confirmText[confirm]}</p>
          <p className="text-xs text-text-secondary">
            カイポケの実績の時刻を書き換えます。予定には触れません。
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              size="sm"
              disabled={!canSend}
              onClick={() => {
                const go = confirmGo[confirm];
                setConfirm(null);
                go();
              }}
            >
              反映する
            </Button>
            <Button type="button" size="sm" variant="outline" onClick={() => setConfirm(null)}>
              やめる
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
