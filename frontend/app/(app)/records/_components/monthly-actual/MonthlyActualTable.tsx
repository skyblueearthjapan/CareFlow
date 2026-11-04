/**
 * 「月の実績」の突き合わせ：件数のカード・判定の絞り込み・差がある行の表。表示だけ。
 *
 * - 一致の行は表に出さず、件数だけ出す（「一致 378 件は対象外」）。
 * - らく助の時刻は桃色、カイポケは青。差は分で、5 分を超えると赤。
 * - 「人が見る」（review）の行を「する」にするときは、画面の中で確認の一言を出す。
 * - カイポケに無い／カイポケだけの行は切り替えられない（カイポケで足す・確かめる案内）。
 * - 記録書Ⅱの列：今の記録書Ⅱの時刻 → そろえる時刻。記録書Ⅱが無い行は扱わない。
 *   結果は実績と記録書Ⅱを分けて出す（記録書Ⅱの理由は知らないコードもそのまま出す）。
 */

import { useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';

import type { MonthlyActualCounts, MonthlyActualRow } from '@/lib/schemas/monthly-actual';

import {
  type ChipTone,
  diffMinutes,
  flagChips,
  isLockedStatus,
  record2ReasonLabel,
  resultLabel,
  shortDate,
  timeRange,
} from './monthlyActualFormat';

type Filter =
  | 'all'
  | 'selected'
  | 'apply'
  | 'k2_only'
  | 'review'
  | 'missing_in_kaipoke'
  | 'kaipoke_only';

const CHIP_TONE: Record<ChipTone, string> = {
  red: 'bg-error-bg text-error',
  amber: 'bg-warning-bg text-warning-strong',
  blue: 'bg-info-bg text-info-strong',
  ok: 'bg-success-bg text-success',
};

function Diff({ value }: { value: number | null }) {
  if (value === null) return <span className="text-text-muted">—</span>;
  const tone = Math.abs(value) > 5 ? 'text-error' : value !== 0 ? 'text-warning-strong' : '';
  return (
    <span className={`tnum font-bold ${tone}`} data-big={Math.abs(value) > 5 ? 'true' : undefined}>
      {value > 0 ? '+' : ''}
      {value}
    </span>
  );
}

/** 記録書Ⅱの結果の言葉（実績の「反映した」に当たるのは「そろえた」）。 */
function record2Result(status: string | null | undefined): { label: string; ok: boolean } | null {
  if (status === 'success') return { label: 'そろえた', ok: true };
  return resultLabel(status);
}

/** 記録書Ⅱの列：今の時刻 → そろえる時刻。 */
function Record2Cell({ row }: { row: MonthlyActualRow }) {
  if (!row.record2_start && !row.record2_end) {
    return <span className="text-xs text-text-muted">記録書Ⅱなし</span>;
  }
  const now = timeRange(row.record2_start, row.record2_end);
  if (!row.needs_record2) {
    return (
      <span className="tnum text-text-secondary" title="記録書Ⅱは送る時刻と同じです">
        {now}
        <span className="block text-xs text-text-muted">同じ</span>
      </span>
    );
  }
  return (
    <span className="tnum" data-testid={`ma-record2-${row.id}`}>
      <span className="text-info-strong">{now}</span>
      <span className="block text-xs font-semibold text-brand-primary-hover">
        → {timeRange(row.new_start, row.new_end)} にそろえる
      </span>
    </span>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="min-w-[110px] flex-1 rounded-lg border border-border-default px-2.5 py-2">
      <span className="text-xs text-text-secondary">{label}</span>
      <b className={`block text-xl tnum ${tone ?? 'text-text-primary'}`}>{value}</b>
    </div>
  );
}

export interface MonthlyActualTableProps {
  counts: MonthlyActualCounts;
  rows: MonthlyActualRow[];
  /** 「する／しない」を変える。 */
  onToggle: (rowId: string, apply: boolean) => void;
  /** 反映中・送信中は切り替えさせない。 */
  disabled?: boolean;
}

export function MonthlyActualTable({ counts, rows, onToggle, disabled }: MonthlyActualTableProps) {
  const [filter, setFilter] = useState<Filter>('all');
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const diffRows = useMemo(() => rows.filter((r) => r.status !== 'match'), [rows]);
  const selectedCount = diffRows.filter((r) => r.apply).length;
  const shown = useMemo(
    () =>
      diffRows.filter((r) =>
        filter === 'all' ? true : filter === 'selected' ? r.apply : r.status === filter,
      ),
    [diffRows, filter],
  );

  const choose = (row: MonthlyActualRow, apply: boolean) => {
    if (disabled || isLockedStatus(row.status) || !row.selectable || row.apply === apply) return;
    if (apply && row.status === 'review') {
      setConfirmId(row.id);
      return;
    }
    onToggle(row.id, apply);
  };

  const chips: ReadonlyArray<{ key: Filter; label: string; n: number }> = [
    { key: 'all', label: 'すべて', n: diffRows.length },
    { key: 'selected', label: '「する」にした', n: selectedCount },
    { key: 'apply', label: '反映する', n: counts.apply },
    { key: 'k2_only', label: '記録書Ⅱだけ', n: counts.k2_only },
    { key: 'review', label: '人が見る', n: counts.review },
    { key: 'missing_in_kaipoke', label: 'カイポケに無い', n: counts.missing_in_kaipoke },
    { key: 'kaipoke_only', label: 'カイポケだけ', n: counts.kaipoke_only },
  ];

  return (
    <div className="space-y-2" data-testid="ma-table-block">
      <div className="flex flex-wrap gap-2" data-testid="ma-counts">
        <Stat label="一致（対象外）" value={counts.match} tone="text-success" />
        <Stat label="反映する" value={counts.apply} tone="text-error" />
        <Stat label="記録書Ⅱだけ" value={counts.k2_only} tone="text-error" />
        <Stat label="人が見る" value={counts.review} tone="text-warning-strong" />
        <Stat label="カイポケに無い" value={counts.missing_in_kaipoke} tone="text-info-strong" />
        <Stat label="カイポケだけ" value={counts.kaipoke_only} tone="text-info-strong" />
      </div>
      <p className="text-xs text-text-muted" data-testid="ma-match-note">
        一致 {counts.match} 件は対象外です。下は差がある行です。「する」にした {selectedCount}{' '}
        件だけが次へ進みます。記録書Ⅱがある行は、記録書Ⅱの「実際の訪問時間」も同じ時刻にそろえます（
        {counts.record2} 件）。
      </p>

      <div className="flex flex-wrap gap-1.5" role="group" aria-label="判定で絞り込む">
        {chips.map((c) => (
          <button
            key={c.key}
            type="button"
            aria-pressed={filter === c.key}
            onClick={() => setFilter(c.key)}
            className={[
              'rounded-full border px-2.5 py-1 text-xs',
              filter === c.key
                ? 'border-brand-primary bg-brand-primary text-white'
                : 'border-border-default bg-bg-base text-text-secondary hover:bg-bg-muted',
            ].join(' ')}
          >
            {c.label}（{c.n}）
          </button>
        ))}
      </div>

      <div className="overflow-x-auto rounded-lg border border-border-default">
        <table className="w-full min-w-[900px] text-sm" data-testid="ma-table">
          <thead>
            <tr className="border-b border-border-default bg-brand-primary-50 text-left text-xs text-text-secondary">
              <th className="px-2 py-2 font-medium">反映</th>
              <th className="px-2 py-2 font-medium">日付</th>
              <th className="px-2 py-2 font-medium">利用者</th>
              <th className="px-2 py-2 font-medium">らく助の担当</th>
              <th className="px-2 py-2 font-medium">らく助（QR）</th>
              <th className="px-2 py-2 font-medium">カイポケの実績</th>
              <th className="px-2 py-2 font-medium">記録書Ⅱ</th>
              <th className="px-2 py-2 font-medium">到着の差</th>
              <th className="px-2 py-2 font-medium">退出の差</th>
              <th className="px-2 py-2 font-medium">印</th>
              <th className="px-2 py-2 font-medium">結果</th>
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 ? (
              <tr>
                <td colSpan={11} className="px-3 py-6 text-center text-text-muted">
                  この絞り込みの行はありません。
                </td>
              </tr>
            ) : (
              shown.map((r) => {
                const locked = isLockedStatus(r.status) || !r.selectable;
                const sendDiffers =
                  (r.new_start && r.new_start !== r.rk_start) ||
                  (r.new_end && r.new_end !== r.rk_end);
                const result = resultLabel(r.result_status);
                const record2 = record2Result(r.record2_result_status);
                const record2Note = [
                  record2ReasonLabel(r.record2_result_reason),
                  r.record2_result_message,
                ]
                  .filter(Boolean)
                  .join(' / ');
                return (
                  <tr
                    key={r.id}
                    className="border-b border-border-default align-top"
                    data-testid={`ma-row-${r.id}`}
                  >
                    <td className="px-2 py-2">
                      {locked ? (
                        <span
                          className="text-xs text-info-strong"
                          data-testid={`ma-locked-${r.id}`}
                        >
                          {r.status === 'missing_in_kaipoke'
                            ? 'カイポケで足してください'
                            : r.status === 'kaipoke_only'
                              ? 'カイポケで確かめてください'
                              : '送る時刻がありません'}
                        </span>
                      ) : (
                        <div className="space-y-1">
                          <span
                            className="inline-flex overflow-hidden rounded-md border border-border-default"
                            role="group"
                            aria-label={`${shortDate(r.date)} ${r.patient_name ?? ''} を反映するか`}
                          >
                            <button
                              type="button"
                              aria-pressed={r.apply}
                              disabled={disabled}
                              onClick={() => choose(r, true)}
                              className={[
                                'px-2 py-1 text-xs',
                                r.apply ? 'bg-brand-primary text-white' : 'bg-bg-base',
                              ].join(' ')}
                            >
                              する
                            </button>
                            <button
                              type="button"
                              aria-pressed={!r.apply}
                              disabled={disabled}
                              onClick={() => choose(r, false)}
                              className={[
                                'px-2 py-1 text-xs',
                                !r.apply ? 'bg-text-secondary text-white' : 'bg-bg-base',
                              ].join(' ')}
                            >
                              しない
                            </button>
                          </span>
                          {confirmId === r.id && (
                            <div
                              role="alertdialog"
                              aria-label="印の付いた行を反映するかの確認"
                              className="w-56 space-y-1.5 rounded-md border border-border-warning bg-warning-bg p-2 text-xs"
                            >
                              <p>
                                印の付いた行です。中身を確かめてから反映してください。反映しますか。
                              </p>
                              <div className="flex gap-1.5">
                                <Button
                                  type="button"
                                  size="sm"
                                  onClick={() => {
                                    setConfirmId(null);
                                    onToggle(r.id, true);
                                  }}
                                >
                                  反映する
                                </Button>
                                <Button
                                  type="button"
                                  size="sm"
                                  variant="outline"
                                  onClick={() => setConfirmId(null)}
                                >
                                  やめる
                                </Button>
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-2 py-2 tnum">{shortDate(r.date)}</td>
                    <td className="whitespace-nowrap px-2 py-2">{r.patient_name ?? '—'}</td>
                    <td className="whitespace-nowrap px-2 py-2 text-text-secondary">
                      {r.rk_staff || '—'}
                    </td>
                    <td className="whitespace-nowrap px-2 py-2 tnum font-semibold text-brand-primary-hover">
                      {timeRange(r.rk_start, r.rk_end)}
                      {sendDiffers && (
                        <div className="text-xs font-normal text-text-muted">
                          送る {timeRange(r.new_start, r.new_end)}
                        </div>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-2 py-2 tnum text-info-strong">
                      {timeRange(r.kp_start, r.kp_end)}
                      {r.kp_staff && <div className="text-xs text-text-muted">{r.kp_staff}</div>}
                    </td>
                    <td className="whitespace-nowrap px-2 py-2">
                      <Record2Cell row={r} />
                    </td>
                    <td className="px-2 py-2">
                      <Diff
                        value={r.diff_start ?? diffMinutes(r.new_start ?? r.rk_start, r.kp_start)}
                      />
                    </td>
                    <td className="px-2 py-2">
                      <Diff value={r.diff_end ?? diffMinutes(r.new_end ?? r.rk_end, r.kp_end)} />
                    </td>
                    <td className="px-2 py-2">
                      {flagChips(r).map((c) => (
                        <span
                          key={c.key}
                          title={c.title}
                          className={`mb-1 mr-1 inline-block whitespace-nowrap rounded px-1.5 py-px text-[11px] ${CHIP_TONE[c.tone]}`}
                        >
                          {c.label}
                        </span>
                      ))}
                    </td>
                    <td className="px-2 py-2 text-xs">
                      {result && (
                        <div data-testid={`ma-result-${r.id}`}>
                          <span className="text-text-muted">実績：</span>
                          <span className={result.ok ? 'text-success' : 'font-bold text-error'}>
                            {result.label}
                          </span>
                          {(r.result_message || r.result_reason) && (
                            <div className="text-text-muted">
                              {r.result_message || r.result_reason}
                            </div>
                          )}
                        </div>
                      )}
                      {record2 && (
                        <div className="mt-1" data-testid={`ma-record2-result-${r.id}`}>
                          <span className="text-text-muted">記録書Ⅱ：</span>
                          <span className={record2.ok ? 'text-success' : 'font-bold text-error'}>
                            {record2.label}
                          </span>
                          {record2Note && <div className="text-text-muted">{record2Note}</div>}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
