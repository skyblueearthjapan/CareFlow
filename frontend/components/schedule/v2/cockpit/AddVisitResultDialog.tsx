'use client';

/**
 * AddVisitResultDialog — 「＋訪問」の結果画面 (Phase 4)。
 *
 * 正典 = `docs/plans/add-visit-anywhere-design.md` §3-3 ⑤（複数日付 × (b)/(c) は
 * 日付ごとに順に実行。**1 件でも失敗したらその時点で止め、成功分と失敗分を
 * 結果画面に列挙**）と §8（型のある曜日に足した日は「今週だけの予定」になる）。
 *
 * 表示は日付ごとに 1 行。結果は 5 種:
 *   ✓ 登録 / ✓ 登録（臨時） / ✓ 移動 / ✓ 型を更新 / ✗ 失敗: 理由 / — 未実行
 *
 * 「元に戻す」は **今週だけの操作**（新規追加・移動）が 1 件でもあるときだけ出す。
 * 型 (PUT fixed-visits) は op-log の undo 対象ではないため、型だけの結果では出さない。
 */
import * as React from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { formatDateLabel } from '@/lib/scheduling/addVisitPlan';
import type { AddVisitExecResult } from '@/lib/scheduling/addVisitExecutor';

export type AddVisitResultStatus =
  /** place-and-fix でコースに 1 件足した。 */
  | 'new'
  /** コース未解決 (臨) で POST /visits した = 盤面に出ない・undo で消えない。 */
  | 'new_manual'
  /** visit-move-week-only で動かした。 */
  | 'week'
  /** PUT fixed-visits で型を変えた。 */
  | 'pattern'
  /** 失敗（ここで止まった）。 */
  | 'failed'
  /** 前の失敗で止まったため実行していない。 */
  | 'skipped';

export interface AddVisitResultRow {
  date: string;
  startHM: string;
  minutes: number;
  courseLabel: string;
  status: AddVisitResultStatus;
  /** status='failed' の理由。 */
  message?: string | null;
  /** 補足（§8 の「今週だけの予定として扱われます」など）。 */
  note?: string | null;
  /** 注意喚起（2 名体制を M へ入れて 1 名分しか作れなかった等）。 */
  warn?: string | null;
}

/** コース未所属で作った訪問 (臨) の注意書き (M3)。 */
export const MANUAL_VISIT_WARN =
  '臨時扱い — コース未所属のため盤面に出ません。「元に戻す」でも消えません';

const STATUS_LABEL: Record<AddVisitResultStatus, string> = {
  new: '✓ 登録',
  new_manual: '✓ 登録（臨時）',
  week: '✓ 移動',
  pattern: '✓ 型を更新',
  failed: '✗ 失敗',
  skipped: '— 未実行',
};

const STATUS_CLASS: Record<AddVisitResultStatus, string> = {
  new: 'text-brand-primary',
  new_manual: 'text-brand-primary',
  week: 'text-brand-primary',
  pattern: 'text-brand-primary',
  failed: 'text-error',
  skipped: 'text-text-muted',
};

/**
 * 実行結果 → 表示行（日付順）。
 *
 * `executeAddVisitPlan` の `done` / `failed` / `skipped` をそのまま並べ替えずに
 * 日付順に戻す（`ordered` が実行順 = 日付順の正）。
 */
export function buildAddVisitResultRows(result: AddVisitExecResult): AddVisitResultRow[] {
  const byDoneKey = new Map(result.done.map((d) => [`${d.item.date}|${d.item.startHM}`, d]));
  const skippedKeys = new Set(result.skipped.map((s) => `${s.date}|${s.startHM}`));
  const failedKey = result.failed
    ? `${result.failed.item.date}|${result.failed.item.startHM}`
    : null;

  return result.ordered.map((item) => {
    const key = `${item.date}|${item.startHM}`;
    const base = {
      date: item.date,
      startHM: item.startHM,
      minutes: item.minutes,
      courseLabel: item.courseLabel,
    };
    if (failedKey === key) {
      return { ...base, status: 'failed' as const, message: result.failed?.message ?? null };
    }
    if (skippedKeys.has(key)) return { ...base, status: 'skipped' as const };
    const done = byDoneKey.get(key);
    if (!done) return { ...base, status: 'skipped' as const };
    if (done.kind === 'pattern') return { ...base, status: 'pattern' as const };
    if (done.kind === 'week') return { ...base, status: 'week' as const };
    // §8: 型のある曜日に足した日は、その週を作り直すと型スロットが出なくなる。
    const note = 'この日は今週だけの予定として扱われます（毎週の型は変わりません）';
    if (done.kind === 'new_manual') {
      // 臨 = op-log を経由しない POST /visits。undo の対象外なので明示する (M3)。
      return { ...base, status: 'new_manual' as const, note, warn: MANUAL_VISIT_WARN };
    }
    return { ...base, status: 'new' as const, note };
  });
}

export interface AddVisitResultDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  patientName: string;
  rows: AddVisitResultRow[];
  /** 「元に戻す」= op-log の undo。今週だけの操作があるときだけ出す。 */
  onUndo?: () => void;
}

export function AddVisitResultDialog({
  open,
  onOpenChange,
  patientName,
  rows,
  onUndo,
}: AddVisitResultDialogProps) {
  const successCount = rows.filter(
    (r) =>
      r.status === 'new' ||
      r.status === 'new_manual' ||
      r.status === 'week' ||
      r.status === 'pattern',
  ).length;
  const failed = rows.find((r) => r.status === 'failed') ?? null;
  // 型の更新は op-log の undo 対象ではない (§2-2)。臨 (POST /visits) も op-log に
  // 載らないので除く (M3)。戻せるのは place-and-fix / 移動だけ。
  const canUndo = onUndo != null && rows.some((r) => r.status === 'new' || r.status === 'week');

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg" data-testid="add-visit-result-dialog">
        <DialogHeader>
          <DialogTitle className="text-sm">
            {failed ? '途中で止まりました' : '登録しました'}
          </DialogTitle>
          <DialogDescription className="text-[11px]">
            {patientName}様 — {successCount} 件を処理しました
            {failed ? '（失敗した日で止めています。残りは実行していません）' : ''}
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[50vh] overflow-y-auto rounded border border-border-default">
          <table className="w-full text-[11px]">
            <thead className="bg-bg-muted/60 text-text-muted">
              <tr>
                <th className="px-2 py-1 text-left font-normal">日付</th>
                <th className="px-2 py-1 text-left font-normal">時刻</th>
                <th className="px-2 py-1 text-left font-normal">コース</th>
                <th className="px-2 py-1 text-left font-normal">結果</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={`${row.date}|${row.startHM}`}
                  className="border-t border-border-default align-top"
                  data-testid={`avr-row-${row.date}`}
                >
                  <td className="whitespace-nowrap px-2 py-1">{formatDateLabel(row.date)}</td>
                  <td className="whitespace-nowrap px-2 py-1">
                    {row.startHM}
                    <span className="text-text-muted">（{row.minutes}分）</span>
                  </td>
                  <td className="px-2 py-1">{row.courseLabel}</td>
                  <td className={`px-2 py-1 ${STATUS_CLASS[row.status]}`}>
                    {STATUS_LABEL[row.status]}
                    {row.status === 'failed' && row.message ? `: ${row.message}` : ''}
                    {row.note ? (
                      <div className="mt-0.5 text-[10px] text-text-muted">{row.note}</div>
                    ) : null}
                    {row.warn ? (
                      <div
                        className="mt-0.5 text-[10px] text-warning-strong"
                        data-testid={`avr-warn-${row.date}`}
                      >
                        ⚠ {row.warn}
                      </div>
                    ) : null}
                  </td>
                </tr>
              ))}
              {rows.length === 0 ? (
                <tr>
                  <td className="px-2 py-2 text-text-muted" colSpan={4}>
                    実行した予定はありません
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        <DialogFooter className="gap-2">
          {canUndo ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              data-testid="avr-undo"
              onClick={() => {
                onUndo?.();
                onOpenChange(false);
              }}
            >
              元に戻す
            </Button>
          ) : null}
          <Button
            type="button"
            size="sm"
            data-testid="avr-close"
            onClick={() => onOpenChange(false)}
          >
            閉じる
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
