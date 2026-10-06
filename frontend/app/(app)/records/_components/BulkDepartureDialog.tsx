'use client';

/**
 * 「まとめて退出を入れる」ダイアログ（設計 `pc-actual-time-edit-design-2026-10-06.md` D3・
 * モック D）。
 *
 * 「退出なし」で絞った一覧から選んだ訪問に、退出の時刻を 1 件ずつ入れる。決め方は
 * 到着＋予定の長さ（既定・PO 決定 Q1）／到着＋○分／予定の終わり。確認の一覧で訪問ごとの
 * 入る時刻を見てから実行する。新しい API は作らず、既存の `PUT /visits/{id}/actual-time` を
 * **順番に** 呼ぶ（既存の検証と監査がそのまま効く）。失敗は理由つきで残す。入れた時刻の
 * 印は「手入力」（まとめて入れたものも分けない・Q6）。
 *
 * 親は開くたびにマウントし直す（`{open && <…/>}`）。選んだ行は開いた時点で固定する
 * （実行中に一覧を取り直しても、確認した行のまま進める）。
 */

import { useMemo, useState } from 'react';

import {
  BULK_DEFAULT_MINUTES,
  BULK_MAX_MINUTES,
  BULK_MIN_MINUTES,
  bulkDepartureTime,
  runBulkDepartures,
  type BulkDepartureRule,
  type BulkResult,
} from '@/components/records/actualTimeAdjust';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { toast } from '@/components/ui/sonner';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { jstHm } from '@/lib/format/actualTime';
import { cn } from '@/lib/utils';
import { useAdjustVisitActualTime, type VisitHistoryRow } from '@/lib/queries/visit-history';

import { formatHistoryDate, nurseName, plannedMinutes, plannedRange } from './visitHistoryFormat';

interface BulkDepartureDialogProps {
  /** 選んだ「退出なし」の行。 */
  rows: VisitHistoryRow[];
  onClose: () => void;
  /** 実行が終わったら、成功した訪問の id を渡す（選択を外すのに使う）。 */
  onDone?: (succeededIds: string[]) => void;
}

function hm5(t: string | null | undefined): string | null {
  return t ? t.slice(0, 5) : null;
}

export function BulkDepartureDialog({
  rows: initialRows,
  onClose,
  onDone,
}: BulkDepartureDialogProps) {
  const [rows] = useState(initialRows);
  const [rule, setRule] = useState<BulkDepartureRule>('len');
  const [minutesText, setMinutesText] = useState(String(BULK_DEFAULT_MINUTES));
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<BulkResult[] | null>(null);
  const adjust = useAdjustVisitActualTime();

  const minutes = Number.parseInt(minutesText, 10);
  const plans = useMemo(
    () =>
      rows.map((row) => ({
        row,
        arrival: jstHm(row.arrival_at),
        ...bulkDepartureTime(
          rule,
          {
            arrival: jstHm(row.arrival_at),
            plannedStart: hm5(row.start_time),
            plannedEnd: hm5(row.end_time),
          },
          Number.isFinite(minutes) ? minutes : Number.NaN,
        ),
      })),
    [rows, rule, minutes],
  );

  const run = async () => {
    setRunning(true);
    const out = await runBulkDepartures(
      plans.map((p) => ({ visitId: p.row.visit_id, time: p.time, why: p.why })),
      (visitId, time) => adjust.mutateAsync({ visitId, kind: 'departure', time }),
      apiErrorMessage,
    );
    setRunning(false);
    setResults(out);
    const ok = out.filter((r) => r.ok);
    onDone?.(ok.map((r) => r.visitId));
    if (ok.length > 0) toast.success(`${ok.length} 件に退出を入れました`);
  };

  const rowById = new Map(rows.map((r) => [r.visit_id, r]));
  const okCount = results?.filter((r) => r.ok).length ?? 0;
  const failed = results?.filter((r) => !r.ok) ?? [];
  const exampleRow = rows[0];
  const exampleLen = exampleRow ? plannedMinutes(exampleRow) : null;
  const exampleLenTime = exampleRow
    ? bulkDepartureTime('len', {
        arrival: jstHm(exampleRow.arrival_at),
        plannedStart: hm5(exampleRow.start_time),
        plannedEnd: hm5(exampleRow.end_time),
      }).time
    : null;

  const ruleOptions: ReadonlyArray<{ value: BulkDepartureRule; label: string; note: string }> = [
    {
      value: 'len',
      label: '到着＋予定の長さ',
      note:
        exampleRow && exampleLen != null
          ? `例: 到着 ${jstHm(exampleRow.arrival_at) ?? '—'}・予定 ${exampleLen} 分 → ${
              exampleLenTime ?? '—'
            }`
          : '予定の長さぶん滞在したことにします',
    },
    { value: 'min', label: '到着＋', note: '全件同じ長さ' },
    {
      value: 'end',
      label: '予定の終わり',
      note: exampleRow
        ? `例: 予定 ${plannedRange(exampleRow) ?? '—'} → ${hm5(exampleRow.end_time) ?? '—'}`
        : '',
    },
  ];

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !running) onClose();
      }}
    >
      <DialogContent className="max-w-3xl gap-4 text-sm" data-testid="history-bulk-dialog">
        <DialogHeader>
          <DialogTitle>まとめて退出を入れる</DialogTitle>
          <DialogDescription className="text-text-secondary">
            選んだ {rows.length}{' '}
            件の訪問に、退出の時刻を入れます。入れた時刻には「手入力」の印が付きます。
          </DialogDescription>
        </DialogHeader>

        {results === null ? (
          <>
            <fieldset className="space-y-1.5" disabled={running}>
              <legend className="mb-1 text-xs font-bold text-text-secondary">退出の決め方</legend>
              {ruleOptions.map((o) => (
                <label
                  key={o.value}
                  className={cn(
                    'flex cursor-pointer flex-wrap items-center gap-2 rounded-lg border px-3 py-2',
                    rule === o.value
                      ? 'border-brand-primary bg-brand-primary-50'
                      : 'border-border-default',
                  )}
                >
                  <input
                    type="radio"
                    name="bulk-departure-rule"
                    value={o.value}
                    checked={rule === o.value}
                    onChange={() => setRule(o.value)}
                    data-testid={`history-bulk-rule-${o.value}`}
                  />
                  <span className="font-bold text-text-primary">{o.label}</span>
                  {o.value === 'min' && (
                    <>
                      <Input
                        type="number"
                        inputMode="numeric"
                        min={BULK_MIN_MINUTES}
                        max={BULK_MAX_MINUTES}
                        aria-label="到着からの分"
                        className="tnum h-8 w-20 px-2"
                        value={minutesText}
                        onFocus={() => setRule('min')}
                        onChange={(e) => {
                          setMinutesText(e.target.value);
                          setRule('min');
                        }}
                        data-testid="history-bulk-minutes"
                      />
                      <span className="font-bold text-text-primary">分</span>
                    </>
                  )}
                  {o.value === 'len' && (
                    <span className="rounded-full bg-brand-primary-light px-2 py-0.5 text-xs font-bold text-brand-primary-hover">
                      おすすめ
                    </span>
                  )}
                  <span className="text-xs text-text-secondary">{o.note}</span>
                </label>
              ))}
            </fieldset>

            <div>
              <div className="mb-1 text-xs font-bold text-text-secondary">入る時刻の確認</div>
              <div className="max-h-[40vh] overflow-y-auto rounded-lg border border-border-default">
                <table className="w-full text-sm" data-testid="history-bulk-confirm">
                  <thead>
                    <tr className="border-b border-border-default text-left text-xs text-text-muted">
                      <th className="px-3 py-1.5 font-medium">日</th>
                      <th className="px-3 py-1.5 font-medium">利用者</th>
                      <th className="px-3 py-1.5 font-medium">職員</th>
                      <th className="px-3 py-1.5 font-medium">予定</th>
                      <th className="px-3 py-1.5 font-medium">到着</th>
                      <th className="px-3 py-1.5 font-medium">入る退出</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plans.map((p) => (
                      <tr
                        key={p.row.visit_id}
                        className="border-b border-border-default last:border-b-0"
                        data-testid={`history-bulk-plan-${p.row.visit_id}`}
                      >
                        <td className="tnum whitespace-nowrap px-3 py-1.5">
                          {formatHistoryDate(p.row.visit_date)}
                        </td>
                        <td className="whitespace-nowrap px-3 py-1.5 font-semibold">
                          {p.row.patient_name ?? '—'}
                        </td>
                        <td className="whitespace-nowrap px-3 py-1.5">{nurseName(p.row) || '—'}</td>
                        <td className="tnum whitespace-nowrap px-3 py-1.5">
                          {plannedRange(p.row) ?? '—'}
                        </td>
                        <td className="tnum whitespace-nowrap px-3 py-1.5">{p.arrival ?? '—'}</td>
                        <td className="tnum whitespace-nowrap px-3 py-1.5">
                          <b data-testid={`history-bulk-time-${p.row.visit_id}`}>{p.time ?? '—'}</b>
                          <span className="ml-1.5 text-xs text-text-secondary">{p.why}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        ) : (
          <div data-testid="history-bulk-result">
            <p className="text-base font-bold text-text-primary" data-testid="history-bulk-summary">
              ✓ {okCount} 件入れました ・ 失敗 {failed.length}
            </p>
            {failed.length > 0 && (
              <ul className="mt-2 space-y-1" data-testid="history-bulk-failures">
                {failed.map((f) => {
                  const row = rowById.get(f.visitId);
                  return (
                    <li
                      key={f.visitId}
                      className="rounded-md border border-border-error bg-error-bg px-3 py-1.5 text-error"
                      data-testid={`history-bulk-failure-${f.visitId}`}
                    >
                      {row ? `${formatHistoryDate(row.visit_date)} ${row.patient_name ?? '—'}` : ''}
                      {f.time ? `（退出 ${f.time}）` : ''}: {f.error}
                    </li>
                  );
                })}
              </ul>
            )}
            <p className="mt-2 text-xs text-text-secondary">
              入れた訪問は一覧の「退出なし」から外れ、「手入力」の印で出ます。
            </p>
          </div>
        )}

        <DialogFooter className="items-center">
          <span className="mr-auto text-xs text-text-secondary">
            1 件ずつ記録します。理由の入力はありません。
          </span>
          <Button type="button" variant="outline" onClick={onClose} disabled={running}>
            {results === null ? 'キャンセル' : '閉じる'}
          </Button>
          {results === null && (
            <Button
              type="button"
              onClick={() => void run()}
              disabled={running || rows.length === 0}
              data-testid="history-bulk-run"
            >
              {running ? '記録しています…' : `${rows.length} 件に退出を入れる（実行）`}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
