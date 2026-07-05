'use client';

/**
 * WeeklyApplyPanel — 週単位でカイポケへ反映するための操作パネル (K-2 UI)。
 *
 * フロー: ①対象週を選ぶ (前後にトントンと移動) → ②「この週の差分を計算」で
 * diff-local (週スコープ) → ③週ビューで反映内容を確認 → ④「この週で反映」を
 * 明示確認 → dry-run / 本番 apply。対象週外は触らない (週スコープ)。
 */
import { useMemo, useState } from 'react';
import { toast } from 'sonner';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { useCorrectionItems, useStartApply, useStartDiffLocal } from '@/lib/queries/integrations';

import { WeekDiffView } from './WeekDiffView';

const WEEKDAY = ['日', '月', '火', '水', '木', '金', '土'];

function mondayOf(d: Date): Date {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const day = x.getDay(); // 0=日..6=土
  const diff = day === 0 ? -6 : 1 - day; // 月曜起点
  x.setDate(x.getDate() + diff);
  return x;
}

function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function nextWeekMonday(): Date {
  const m = mondayOf(new Date());
  m.setDate(m.getDate() + 7); // 既定は「翌週」(実運用は翌週分を反映)
  return m;
}

export function WeeklyApplyPanel() {
  const [weekStart, setWeekStart] = useState<Date>(() => nextWeekMonday());
  const [sheetId, setSheetId] = useState<string | null>(null);
  const [summary, setSummary] = useState<Record<string, number> | null>(null);
  const [confirm, setConfirm] = useState<null | 'dry' | 'real'>(null);

  const diffLocal = useStartDiffLocal();
  const apply = useStartApply();
  const itemsQuery = useCorrectionItems(sheetId ?? undefined, { limit: 500 });
  const items = itemsQuery.data?.items ?? [];

  const weekEnd = useMemo(() => {
    const e = new Date(weekStart);
    e.setDate(e.getDate() + 6);
    return e;
  }, [weekStart]);

  const label = `${weekStart.getMonth() + 1}/${weekStart.getDate()}（${WEEKDAY[weekStart.getDay()]}）〜 ${weekEnd.getMonth() + 1}/${weekEnd.getDate()}（${WEEKDAY[weekEnd.getDay()]}）`;

  const shiftWeek = (deltaWeeks: number) => {
    const n = new Date(weekStart);
    n.setDate(n.getDate() + deltaWeeks * 7);
    setWeekStart(n);
    // 週を変えたら前の差分は無効化 (別週の内容を誤って反映しないため)。
    setSheetId(null);
    setSummary(null);
  };

  const runDiff = async () => {
    setSheetId(null);
    setSummary(null);
    try {
      const res = await diffLocal.mutateAsync({
        month: `${weekStart.getFullYear()}-${String(weekStart.getMonth() + 1).padStart(2, '0')}`,
        weekStart: fmtDate(weekStart),
        weekEnd: fmtDate(weekEnd),
      });
      setSheetId(res.sheetId);
      setSummary(res.summary ?? {});
    } catch {
      // エラーは下の Alert で表示。
    }
  };

  const runApply = async (dryRun: boolean) => {
    if (!sheetId) return;
    setConfirm(null);
    try {
      await apply.mutateAsync({ sheetId, dryRun });
      toast.success(
        dryRun
          ? 'dry-run を開始しました。ライブモニターで操作を確認できます（書込なし）。'
          : '本番反映を開始しました。ライブモニターで進捗を確認してください。',
      );
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '実行に失敗しました');
    }
  };

  const total = summary?.total ?? 0;
  const unresolved = summary?.unresolved_patient ?? 0;

  return (
    <Card className="p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">週単位でカイポケへ反映</h2>
        {/* 週セレクタ (前後にトントンと移動) */}
        <div className="flex items-center gap-1.5">
          <Button variant="outline" size="sm" onClick={() => shiftWeek(-1)}>
            ◀ 前の週
          </Button>
          <span className="min-w-[220px] rounded-md border border-border-default bg-bg-muted px-3 py-1.5 text-center text-sm font-medium tabular-nums text-text-primary">
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
              setSheetId(null);
              setSummary(null);
            }}
            title="翌週へ戻す"
          >
            翌週
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={runDiff} disabled={diffLocal.isPending}>
          {diffLocal.isPending ? 'カイポケ現況を取得して計算中…（約1分）' : 'この週の差分を計算'}
        </Button>
        {sheetId && (
          <div className="flex flex-wrap items-center gap-2 text-sm text-text-secondary">
            <SummaryChip label="合計" value={total} tone="neutral" />
            <SummaryChip label="追加" value={summary?.add ?? 0} tone="success" />
            <SummaryChip
              label="編集"
              value={(summary?.edit ?? 0) + (summary?.update ?? 0)}
              tone="warning"
            />
            <SummaryChip label="削除" value={summary?.delete ?? 0} tone="error" />
            {unresolved > 0 && <SummaryChip label="要確認" value={unresolved} tone="warning" />}
          </div>
        )}
      </div>

      {diffLocal.isError && (
        <Alert variant="destructive" className="mt-3">
          <AlertTitle>差分計算に失敗しました</AlertTitle>
          <AlertDescription>
            {diffLocal.error instanceof Error ? diffLocal.error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      )}

      {/* 週ビュー */}
      {sheetId && (
        <div className="mt-4">
          {itemsQuery.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : total === 0 ? (
            <Alert>
              <AlertTitle>この週の差分はありません</AlertTitle>
              <AlertDescription>
                カイポケの現況と CareFlow の予定が一致しています。反映は不要です。
              </AlertDescription>
            </Alert>
          ) : (
            <WeekDiffView weekStart={weekStart} items={items} />
          )}
        </div>
      )}

      {/* 実行 (明示確認) */}
      {sheetId && total > 0 && (
        <div className="mt-4 flex flex-wrap items-center justify-end gap-2 border-t border-border-subtle pt-4">
          <p className="mr-auto text-xs text-text-muted">
            対象週外のカイポケ予定は変更されません。まず dry-run で確認できます。
          </p>
          <Button variant="outline" onClick={() => setConfirm('dry')} disabled={apply.isPending}>
            dry-run で確認（書込なし）
          </Button>
          <Button
            variant="destructive"
            onClick={() => setConfirm('real')}
            disabled={apply.isPending}
          >
            この週で本番反映
          </Button>
        </div>
      )}

      {/* 確認ダイアログ */}
      <Dialog open={confirm !== null} onOpenChange={(o) => !o && setConfirm(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {confirm === 'real' ? 'この週で本番反映しますか？' : 'dry-run で確認しますか？'}
            </DialogTitle>
            <DialogDescription className="space-y-2">
              <span className="block">
                対象週: <span className="font-medium text-text-primary">{label}</span>（{total}件）
              </span>
              {confirm === 'real' ? (
                <span className="block text-error">
                  カイポケに実際に書き込まれます。対象週外は変更されませんが、この操作は Ctrl+Z
                  の対象外です。ライブモニターで進捗を確認してください。
                </span>
              ) : (
                <span className="block">
                  カイポケへの書込はありません。操作の様子だけをシミュレーションで確認します。
                </span>
              )}
              {unresolved > 0 && (
                <span className="block text-warning-strong">
                  ※ CareFlow 未登録の利用者 {unresolved} 件は安全にスキップされます。
                </span>
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(null)}>
              やめる
            </Button>
            <Button
              variant={confirm === 'real' ? 'destructive' : 'default'}
              disabled={apply.isPending}
              onClick={() => runApply(confirm === 'dry')}
            >
              {confirm === 'real' ? '本番反映する' : 'dry-run を実行'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function SummaryChip({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: 'neutral' | 'success' | 'warning' | 'error';
}) {
  const cls =
    tone === 'success'
      ? 'bg-success-bg text-success'
      : tone === 'warning'
        ? 'bg-warning-bg text-warning-strong'
        : tone === 'error'
          ? 'bg-error-bg text-error'
          : 'bg-bg-muted text-text-secondary';
  return (
    <span className={`inline-flex items-center gap-1 rounded-md px-2 py-1 ${cls}`}>
      {label} <span className="font-mono font-bold tabular-nums">{value}</span>
    </span>
  );
}
