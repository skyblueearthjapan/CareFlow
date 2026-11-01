/**
 * 「月の実績」タブ — らく助の QR 実績をカイポケの実績へ反映する（管理者だけ）。
 * 設計 docs/plans/kaipoke-actual-write-design-2026-11-01.md §5-3・モック
 * docs/mockups/monthly-actual-sync-mock.html。
 *
 * hooks（`lib/queries/monthly-actual.ts`）をつなぐのはこのファイルだけ。部品
 * （段・表・操作）は props で受け取る表示だけの部品にしてある。
 *
 * ロボットはサーバーで動くので、押した後に PC を閉じてよい。進み具合は、反映が
 * 動いている間だけ status を数秒おきに読む（終われば止まる・hooks 側の refetchInterval）。
 *
 * 最後の段「一覧を出す」では、看護師別の月の実績（表・A4・Excel の看護師別シート）も出す。
 * A4 は BE が返す HTML を新しいタブで開く（打刻履歴の A4 `VisitHistoryPrintDialog` と同じ開き方）。
 */

import { useMemo, useRef, useState } from 'react';
import { Download } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { triggerBlobDownload } from '@/lib/api/patientsExcel';
import {
  useApplyMonthlyActual,
  useCancelMonthlyActualSchedule,
  useMonthlyActual,
  useMonthlyActualApplyStatus,
  useMonthlyActualExport,
  useMonthlyStaffReport,
  useMonthlyStaffSummary,
  useRefreshMonthlyActual,
  useScheduleMonthlyActual,
  useUpdateMonthlyActualRow,
} from '@/lib/queries/monthly-actual';

import { MonthlyActualRunPanel, type MonthlyActualProgress } from './MonthlyActualRunPanel';
import { MonthlyActualSteps } from './MonthlyActualSteps';
import { MonthlyActualTable } from './MonthlyActualTable';
import { MonthlyStaffTable } from './MonthlyStaffTable';
import {
  currentStep,
  failedRows,
  monthLabel,
  pendingRows,
  recentMonths,
} from './monthlyActualFormat';

function formatFetchedAt(iso: string | null | undefined): string {
  if (!iso) return 'カイポケの実績：まだ読み取っていません';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return `カイポケの実績：${iso} に読み取り`;
  return `カイポケの実績：${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(
    2,
    '0',
  )}:${String(d.getMinutes()).padStart(2, '0')} に読み取り`;
}

export function MonthlyActualTab() {
  const months = useMemo(() => recentMonths(new Date()), []);
  const [month, setMonth] = useState<string>(months[0] ?? '');

  const list = useMonthlyActual(month);
  const view = list.data;
  const running = !!view?.job?.running;
  const status = useMonthlyActualApplyStatus(month, { enabled: running });

  const refresh = useRefreshMonthlyActual();
  const updateRow = useUpdateMonthlyActualRow();
  const apply = useApplyMonthlyActual();
  const schedule = useScheduleMonthlyActual();
  const cancelSchedule = useCancelMonthlyActualSchedule();
  const exportFile = useMonthlyActualExport();
  const staffReport = useMonthlyStaffReport();

  // 二度押しの止め。state は描き直しまで古いので、同じ瞬間の 2 回目は ref で止める。
  const sendingRef = useRef(false);
  const [sending, setSending] = useState(false);
  const busy =
    sending ||
    refresh.isPending ||
    apply.isPending ||
    schedule.isPending ||
    cancelSchedule.isPending;

  const send = async (fn: () => Promise<unknown>, okMessage: string) => {
    if (sendingRef.current) return;
    sendingRef.current = true;
    setSending(true);
    try {
      await fn();
      toast.success(okMessage);
    } catch (e) {
      toast.error(apiErrorMessage(e));
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  };

  // 動いている間は status の行（結果つき）を一覧の行に重ねる。
  const rows = useMemo(() => {
    const base = view?.rows ?? [];
    const fresh = running ? status.data?.rows : undefined;
    if (!fresh?.length) return base;
    const byId = new Map(fresh.map((r) => [r.id, r]));
    return base.map((r) => byId.get(r.id) ?? r);
  }, [view?.rows, running, status.data?.rows]);

  const merged = view ? { ...view, rows } : undefined;
  const step = currentStep(merged);
  const pending = pendingRows(rows);
  const failed = failedRows(rows);
  const staffSummary = useMonthlyStaffSummary(month, { enabled: step === 6 });

  const progress: MonthlyActualProgress | null = (() => {
    const s = status.data;
    if (!running || !s) return null;
    const c = s.counts;
    const success = c.success + c.dry_run_ok;
    const failedN = c.failed + c.error;
    const processed = typeof s.progress?.processed === 'number' ? s.progress.processed : null;
    const total =
      typeof s.progress?.total === 'number'
        ? s.progress.total
        : (s.job?.total ?? view?.job?.total ?? 0);
    return { done: processed ?? success + failedN + c.skipped, total, success, failed: failedN };
  })();

  const onExport = async () => {
    try {
      const { blob, filename } = await exportFile.mutateAsync(month);
      triggerBlobDownload(blob, filename);
      toast.success('Excel を書き出しました');
    } catch (e) {
      toast.error(`Excel の書き出しに失敗しました: ${apiErrorMessage(e)}`);
    }
  };

  const onOpenReport = async () => {
    // window.open は click の中で同期的に呼ぶ（ポップアップの止めを避ける）。
    const win = typeof window !== 'undefined' ? window.open('', '_blank') : null;
    if (!win) {
      toast.warning('ポップアップがブロックされました。ブロックを解除してもう一度お試しください。');
      return;
    }
    try {
      const html = await staffReport.mutateAsync(month);
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
      toast.error(`A4 の出力に失敗しました: ${apiErrorMessage(e)}`);
    }
  };

  return (
    <div
      className="flex flex-col gap-3 overflow-hidden rounded-lg border border-border-default bg-bg-base p-4 shadow-outer-card"
      data-testid="monthly-actual-tab"
    >
      <div className="flex flex-wrap items-center gap-2.5">
        <h2 className="text-base font-bold">月の実績をカイポケへ</h2>
        <select
          aria-label="対象の月"
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          disabled={busy}
          className="rounded-md border border-border-default bg-bg-base px-2 py-1.5 text-sm"
        >
          {months.map((m) => (
            <option key={m} value={m}>
              {monthLabel(m)}
            </option>
          ))}
        </select>
        <span className="text-xs text-text-muted">{formatFetchedAt(view?.kp_fetched_at)}</span>
        {view?.kp_fetched_at && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="ml-auto"
            onClick={onExport}
            disabled={exportFile.isPending}
          >
            <Download className="h-4 w-4" aria-hidden="true" />
            {exportFile.isPending ? '書き出しています…' : 'Excel を書き出す'}
          </Button>
        )}
      </div>

      {list.isLoading ? (
        <>
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-[300px] w-full" />
        </>
      ) : list.isError || !merged ? (
        <Alert variant="destructive">
          <AlertTitle>月の実績を読み込めませんでした</AlertTitle>
          <AlertDescription>{apiErrorMessage(list.error)}</AlertDescription>
        </Alert>
      ) : (
        <>
          <MonthlyActualSteps current={step} />
          {merged.refresh_error && (
            <p className="rounded-md bg-error-bg px-3 py-2 text-sm text-error">
              カイポケの実績を読み取れませんでした：{merged.refresh_error}
            </p>
          )}
          {merged.evening_warning && (
            <p className="rounded-md bg-warning-bg px-3 py-2 text-sm">{merged.evening_warning}</p>
          )}
          <MonthlyActualRunPanel
            step={step}
            month={month}
            isPastMonth={merged.is_past_month}
            selectedCount={pending.length}
            failedCount={failed.length}
            job={merged.job}
            progress={progress}
            refreshing={merged.refreshing}
            busy={busy}
            exporting={exportFile.isPending}
            onRefresh={() =>
              send(() => refresh.mutateAsync(month), 'カイポケの実績の読み取りを始めました')
            }
            onDryRun={() =>
              send(
                () => apply.mutateAsync({ month, dryRun: true, rowIds: pending.map((r) => r.id) }),
                '試しに流すのを始めました（登録はしません）',
              )
            }
            onApply={() =>
              send(
                () => apply.mutateAsync({ month, dryRun: false, rowIds: pending.map((r) => r.id) }),
                'カイポケへの反映を始めました',
              )
            }
            onSchedule={() =>
              send(
                () => schedule.mutateAsync({ month, dryRun: false }),
                '今夜 22 時に流す予約をしました',
              )
            }
            onCancelSchedule={() =>
              send(() => cancelSchedule.mutateAsync(month), '予約を取り消しました')
            }
            onRetryFailed={() =>
              send(
                () => apply.mutateAsync({ month, dryRun: false, onlyFailed: true }),
                '失敗した行の流し直しを始めました',
              )
            }
            onExport={onExport}
            onOpenReport={onOpenReport}
            reportOpening={staffReport.isPending}
          />
          {step === 6 && (
            <section className="space-y-2" data-testid="ma-staff-section">
              <h3 className="text-base font-bold">看護師別の月の実績</h3>
              <p className="text-xs text-text-secondary">
                らく助の実績から数えています。提供時間は到着〜退出で、30
                分未満と退出の記録が無い訪問は到着＋35
                分にした後の値です。移動は拠点と訪問先を直線でつないだ目安です。見出しを押すと並べ替えます。
              </p>
              {staffSummary.isLoading ? (
                <Skeleton className="h-[160px] w-full" />
              ) : staffSummary.isError || !staffSummary.data ? (
                <p className="rounded-md bg-error-bg px-3 py-2 text-sm text-error">
                  看護師別の月の実績を読み込めませんでした：{apiErrorMessage(staffSummary.error)}
                </p>
              ) : (
                <MonthlyStaffTable summary={staffSummary.data} />
              )}
            </section>
          )}
          {merged.kp_fetched_at && (
            <MonthlyActualTable
              counts={merged.counts}
              rows={rows}
              disabled={busy || running || merged.refreshing || updateRow.isPending}
              onToggle={(rowId, value) =>
                updateRow.mutate(
                  { rowId, apply: value },
                  { onError: (e) => toast.error(apiErrorMessage(e)) },
                )
              }
            />
          )}
        </>
      )}
    </div>
  );
}
