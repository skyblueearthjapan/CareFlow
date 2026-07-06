'use client';

/**
 * InboundPanel — カイポケ → CareFlow 取り込みパネル（逆方向同期・日単位）。
 *
 * 概念: 週 apply がバトンタッチで、apply 済み週はカイポケが正。
 * カイポケでの直し込み（キャンセル・時刻変更）を CareFlow に取り込む。
 *
 * 操作フロー:
 *   週チップ（今週/来週、apply 実績ゲート）
 *   → ❶ diff-inbound（カイポケ export + 逆向きシート生成、〜1分）
 *   → ❷ 曜日チップ選択 + 差分明細
 *   → ❸ dry-run で確認 → 二重確認ダイアログ → 実取り込み
 */

import { useEffect, useMemo, useRef, useState } from 'react';
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
import {
  useApplyInbound,
  useCorrectionItems,
  useInboundEligibility,
  useStartDiffInbound,
} from '@/lib/queries/integrations';
import type {
  ApplyInboundResult,
  ApplyInboundResultItem,
  CorrectionItem,
} from '@/lib/schemas/integration';

// ──────────────────────────── 定数 ────────────────────────────

const WEEKDAYS = ['月', '火', '水', '木', '金', '土'] as const;

const ACTION_META: Record<string, { label: string; cls: string }> = {
  add: { label: 'カイポケ追加', cls: 'bg-success-bg text-success' },
  delete: { label: 'キャンセル候補', cls: 'bg-error-bg text-error' },
  update: { label: '時間変更', cls: 'bg-warning-bg text-warning-strong' },
  edit: { label: '時間変更', cls: 'bg-warning-bg text-warning-strong' },
  date_change: { label: '日付変更', cls: 'bg-info-bg text-info' },
  companion_change: { label: '同行変更', cls: 'bg-bg-muted text-text-secondary' },
};

const OUTCOME_META: Record<string, { label: string; cls: string }> = {
  cancelled: { label: 'キャンセル', cls: 'bg-error-bg text-error' },
  updated: { label: '更新', cls: 'bg-success-bg text-success' },
  added: { label: '追加', cls: 'bg-info-bg text-info' },
  skipped: { label: 'スキップ', cls: 'bg-bg-muted text-text-muted' },
  failed: { label: '失敗', cls: 'bg-error-bg text-error' },
};

// ──────────────────────────── ユーティリティ ────────────────────────────

function mondayOf(d: Date): Date {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const day = x.getDay();
  x.setDate(x.getDate() + (day === 0 ? -6 : 1 - day));
  return x;
}

function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function fmtMonth(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

function fmtWeekLabel(mon: Date): string {
  const sat = new Date(mon);
  sat.setDate(sat.getDate() + 5);
  return `${mon.getMonth() + 1}/${mon.getDate()}（月）〜 ${sat.getMonth() + 1}/${sat.getDate()}（土）`;
}

function field(obj: unknown, key: string): string {
  if (obj && typeof obj === 'object' && key in obj) {
    const v = (obj as Record<string, unknown>)[key];
    return v == null ? '' : String(v);
  }
  return '';
}

// ──────────────────────────── メインコンポーネント ────────────────────────────

type WeekOption = 'this' | 'next';

export function InboundPanel({ busy }: { busy: boolean }) {
  const thisMonday = useMemo(() => mondayOf(new Date()), []);
  const nextMonday = useMemo(() => {
    const m = mondayOf(new Date());
    m.setDate(m.getDate() + 7);
    return m;
  }, []);

  const [selectedWeek, setSelectedWeek] = useState<WeekOption>('this');
  const weekStart = selectedWeek === 'this' ? thisMonday : nextMonday;
  const weekStartStr = fmtDate(weekStart);
  const month = fmtMonth(weekStart);

  const [sheetId, setSheetId] = useState<string | null>(null);
  const [summary, setSummary] = useState<Record<string, number> | null>(null);
  const [selectedDays, setSelectedDays] = useState<Set<string>>(new Set());
  const [dryRunResult, setDryRunResult] = useState<ApplyInboundResult | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const autoSelectedRef = useRef(false);

  const thisElig = useInboundEligibility(fmtDate(thisMonday));
  const nextElig = useInboundEligibility(fmtDate(nextMonday));
  const currentElig = selectedWeek === 'this' ? thisElig : nextElig;
  const eligible = currentElig.data?.eligible ?? false;

  const diffInbound = useStartDiffInbound();
  const applyInbound = useApplyInbound();
  const itemsQuery = useCorrectionItems(sheetId ?? undefined, { limit: 500 });
  const items = useMemo(() => itemsQuery.data?.items ?? [], [itemsQuery.data?.items]);

  // 月〜土の YYYY-MM-DD リスト。
  const weekDays = useMemo(
    () =>
      Array.from({ length: 6 }, (_, i) => {
        const d = new Date(weekStart);
        d.setDate(d.getDate() + i);
        return fmtDate(d);
      }),
    [weekStart],
  );

  // 差分アイテムが存在する曜日の日付セット。
  const daysWithDiff = useMemo(() => {
    const set = new Set<string>();
    for (const item of items) {
      const dayStr = field(item.after, 'date') || field(item.before, 'date');
      const dayNum = Number.parseInt(dayStr, 10);
      if (!Number.isFinite(dayNum)) continue;
      for (const dateStr of weekDays) {
        if (new Date(dateStr).getDate() === dayNum) {
          set.add(dateStr);
          break;
        }
      }
    }
    return set;
  }, [items, weekDays]);

  // 差分シートが切り替わったら自動選択フラグをリセット。
  useEffect(() => {
    autoSelectedRef.current = false;
  }, [sheetId]);

  // アイテム読み込み完了後、差分がある曜日をすべて自動選択。
  useEffect(() => {
    if (sheetId && !autoSelectedRef.current && daysWithDiff.size > 0) {
      autoSelectedRef.current = true;
      setSelectedDays(new Set(daysWithDiff));
    }
  }, [sheetId, daysWithDiff]);

  const resetDiff = () => {
    setSheetId(null);
    setSummary(null);
    setSelectedDays(new Set());
    setDryRunResult(null);
    setShowDiff(false);
    autoSelectedRef.current = false;
  };

  const handleWeekChange = (week: WeekOption) => {
    setSelectedWeek(week);
    resetDiff();
  };

  const runDiff = async () => {
    resetDiff();
    try {
      const res = await diffInbound.mutateAsync({ month, weekStart: weekStartStr });
      setSheetId(res.sheetId);
      setSummary(res.summary as Record<string, number>);
    } catch {
      // エラーは Alert で表示。
    }
  };

  const runApply = async (dryRun: boolean) => {
    if (!sheetId) return;
    setConfirm(false);
    const days = Array.from(selectedDays);
    try {
      const res = await applyInbound.mutateAsync({ sheetId, dryRun, days });
      if (dryRun) {
        setDryRunResult(res);
      } else {
        toast.success(
          `取り込み完了: キャンセル ${res.cancelled}件 / 更新 ${res.updated}件 / 追加 ${res.added}件 / スキップ ${res.skipped}件`,
        );
        resetDiff();
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '実行に失敗しました');
    }
  };

  const hasSelectedDays = selectedDays.size > 0;
  const selectedDayLabels = weekDays
    .filter((d) => selectedDays.has(d))
    .map((d, i) => WEEKDAYS[weekDays.indexOf(d)] ?? WEEKDAYS[i])
    .join('・');

  return (
    <Card className="p-5">
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
            disabled={!eligible || diffInbound.isPending || busy}
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

        {/* ❷ 曜日選択 + 差分明細 */}
        {sheetId && (
          <div className="mb-5 space-y-3">
            {/* 曜日チップ */}
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

            {/* 差分明細トグル */}
            <div>
              <button
                type="button"
                onClick={() => setShowDiff((v) => !v)}
                className="text-xs font-medium text-brand-primary hover:underline"
              >
                {showDiff ? '差分明細を隠す ▲' : `差分明細を見る（${summary?.total ?? 0}件）▼`}
              </button>
              {showDiff && (
                <div className="mt-2">
                  {itemsQuery.isLoading ? (
                    <Skeleton className="h-32 w-full" />
                  ) : (
                    <InboundDiffView weekStart={weekStart} items={items} />
                  )}
                </div>
              )}
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
                disabled={!hasSelectedDays || applyInbound.isPending || busy}
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
    </Card>
  );
}

// ──────────────────────────── サブコンポーネント ────────────────────────────

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

/** 月〜土の6列グリッドに差分アイテムを日付で振り分けて表示。 */
function InboundDiffView({ weekStart, items }: { weekStart: Date; items: CorrectionItem[] }) {
  const days = Array.from({ length: 6 }, (_, i) => {
    const d = new Date(weekStart);
    d.setDate(d.getDate() + i);
    return d;
  });

  const byDay = new Map<number, CorrectionItem[]>();
  for (const it of items) {
    const dayStr = field(it.after, 'date') || field(it.before, 'date');
    const n = Number.parseInt(dayStr, 10);
    if (!Number.isFinite(n)) continue;
    const arr = byDay.get(n);
    if (arr) arr.push(it);
    else byDay.set(n, [it]);
  }

  return (
    <div className="overflow-x-auto">
      <div className="grid min-w-[720px] grid-cols-6 gap-2">
        {days.map((d, i) => {
          const list = byDay.get(d.getDate()) ?? [];
          return (
            <div key={i} className="rounded-lg border border-border-default bg-bg-muted">
              <div className="border-b border-border-subtle px-2 py-1.5 text-center text-xs font-medium text-text-secondary">
                {d.getMonth() + 1}/{d.getDate()} （{WEEKDAYS[i]}）
                {list.length > 0 && (
                  <span className="ml-1 text-[10px] text-text-muted">{list.length}件</span>
                )}
              </div>
              <div className="min-h-[80px] space-y-1.5 p-1.5">
                {list.length === 0 ? (
                  <p className="pt-4 text-center text-[11px] text-text-muted">—</p>
                ) : (
                  list.map((it) => <InboundCard key={it.id} item={it} />)
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function InboundCard({ item }: { item: CorrectionItem }) {
  const meta = ACTION_META[item.action] ?? {
    label: item.action,
    cls: 'bg-bg-muted text-text-secondary',
  };
  const name = field(item.after, 'user_name') || field(item.before, 'user_name') || '—';
  const timeAfter = field(item.after, 'start_time');
  const timeBefore = field(item.before, 'start_time');
  const isEdit = item.action === 'edit' || item.action === 'update';
  const isExcluded = !item.include;

  return (
    <div
      className={[
        'rounded-md border px-2 py-1.5 text-[11px] shadow-xs',
        isExcluded ? 'border-border-subtle bg-bg-muted' : 'border-border-subtle bg-bg-base',
      ].join(' ')}
    >
      <div className="mb-1 flex items-center justify-between gap-1">
        <span
          className={`inline-flex items-center rounded px-1.5 py-0.5 font-medium ${
            isExcluded ? 'bg-bg-muted text-text-muted' : meta.cls
          }`}
        >
          {meta.label}
        </span>
        {isExcluded && (
          <span className="text-[10px] text-text-muted" title="名寄せ未解決などで自動的に除外">
            取り込み対象外
          </span>
        )}
      </div>
      <div
        className={[
          'truncate font-medium',
          isExcluded ? 'text-text-muted' : 'text-text-primary',
        ].join(' ')}
        title={name}
      >
        {name}
      </div>
      <div className={['mt-0.5', isExcluded ? 'text-text-muted' : 'text-text-secondary'].join(' ')}>
        {isEdit && timeBefore && timeBefore !== timeAfter ? (
          <span>
            <span className="opacity-60 line-through">{timeBefore}</span> → {timeAfter}
          </span>
        ) : (
          <span>{timeAfter || timeBefore || '--:--'}</span>
        )}
      </div>
    </div>
  );
}
