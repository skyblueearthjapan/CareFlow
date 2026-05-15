'use client';

/**
 * AutoScheduleDialog — Wave 41 Phase 5 (MVP).
 *
 * 自動スケジュール算出 (auto-allocate) の 3-step UI:
 *   Step 1: トリガー — モード選択 / 対象拠点 / 対象週 を確認して「自動算出を実行」
 *   Step 2: 結果表示 — KPI 5 項目 + 提案 course 一覧 + 警告
 *   Step 3: 採用 or 破棄 — 採用 = commit / 破棄 = proposed を delete
 *
 * v1.0 MVP の妥協ポイント (v1.1 で拡充):
 *   - 進捗バー (Phase 1-5 アニメーション) は出さず spinner のみ
 *   - Before/After 並列表示は簡易テーブル
 *   - Visit Justification ポップアップ未実装
 *   - AI 自然文要約セクション未実装
 *
 * RBAC: admin / manager のみ (呼出側で canEdit ガード)。
 *
 * 設計仕様書: ``docs/plans/auto-schedule-v1.md`` v0.4 §8 ワイヤーフレーム
 * 実装計画書: ``docs/plans/auto-schedule-implementation-plan.md`` v0.2 §4 Phase 5
 */
import * as React from 'react';
import { Loader2, Sparkles, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { toast } from 'sonner';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { ApiError } from '@/lib/api-client';
import { useOffices } from '@/lib/queries/offices';
import { useAutoAllocate, useApplyProposal, useDiscardProposal } from '@/lib/queries/auto_schedule';
import type { AutoAllocateMode, AutoAllocateResponse } from '@/lib/schemas/v2/autoAllocate';

// ─────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

const MODE_LABELS: Record<AutoAllocateMode, string> = {
  mode_1: 'Mode 1: 既存固定枠維持 + 新規最適化',
  mode_2: 'Mode 2: 全面最適化',
};

// ─────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────

export interface AutoScheduleDialogProps {
  open: boolean;
  onClose: () => void;
  isoYear: number;
  isoWeek: number;
  /** null = 全拠点 (UI 上は「(全拠点)」表示)。 */
  defaultOfficeId: string | null;
}

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

function formatErr(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return '不明なエラー';
}

/** ISO 8601 週番号から "YYYY年M月D日(曜)の週" 形式に変換する。 */
function formatWeekLabel(isoYear: number, isoWeek: number): string {
  // ISO 8601: 1月4日は必ず第1週に含まれる。
  const jan4 = new Date(Date.UTC(isoYear, 0, 4));
  const jan4Day = jan4.getUTCDay() || 7; // 0=日 を 7 に正規化
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - jan4Day + 1 + (isoWeek - 1) * 7);
  const m = monday.getUTCMonth() + 1;
  const d = monday.getUTCDate();
  const weekdays = ['日', '月', '火', '水', '木', '金', '土'];
  const wd = weekdays[monday.getUTCDay()];
  // H2 (review): ISO 2026-W01 は実際には 2025-12-29 (月) スタートなので、
  // 引数の isoYear ではなく monday の暦上の年を表示しないと "2026年12月29日(月)" と
  // 誤表示される. 必ず monday.getUTCFullYear() を使う.
  return `${monday.getUTCFullYear()}年${m}月${d}日(${wd})の週`;
}

function formatNumber(n: number, fractionDigits = 1): string {
  if (!Number.isFinite(n)) return '—';
  return n.toFixed(fractionDigits);
}

function sumHViolations(h: Record<string, number>): number {
  return Object.values(h).reduce((acc, v) => acc + (Number.isFinite(v) ? v : 0), 0);
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export function AutoScheduleDialog({
  open,
  onClose,
  isoYear,
  isoWeek,
  defaultOfficeId,
}: AutoScheduleDialogProps) {
  // H4 (W41 v1.0 cross-review): Backend schema max_length=200 と整合させ、
  // かつ「拠点未選択 = 空配列送信 → Backend が全拠点扱い」の契約を保つため
  // フロント側の取得 limit も 200 に揃える.
  const officesQuery = useOffices({ limit: 200 });
  const offices = officesQuery.allOffices;

  const autoAllocateMut = useAutoAllocate();
  const applyMut = useApplyProposal();
  const discardMut = useDiscardProposal();

  const [mode, setMode] = React.useState<AutoAllocateMode>('mode_1');
  // 拠点フィルタ: 空 Set = 全拠点。
  // defaultOfficeId があれば初期値としてそれだけチェック済みにする。
  const [selectedOfficeIds, setSelectedOfficeIds] = React.useState<Set<string>>(() =>
    defaultOfficeId ? new Set([defaultOfficeId]) : new Set(),
  );

  // open のたびに state をリセットする。
  React.useEffect(() => {
    if (open) {
      setMode('mode_1');
      setSelectedOfficeIds(defaultOfficeId ? new Set([defaultOfficeId]) : new Set());
      autoAllocateMut.reset();
      applyMut.reset();
      discardMut.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, defaultOfficeId]);

  const result: AutoAllocateResponse | undefined = autoAllocateMut.data;
  const proposalBatchId = result?.proposal_batch_id ?? null;
  const isAllocating = autoAllocateMut.isPending;
  const isApplying = applyMut.isPending;
  const isDiscarding = discardMut.isPending;
  const isBusy = isAllocating || isApplying || isDiscarding;
  // 採用 / 破棄が成功した後はトリガー画面に戻る前にダイアログを閉じる側で扱う。
  const isFinalized = applyMut.isSuccess || discardMut.isSuccess;

  const officeNameById = React.useMemo(() => {
    const m = new Map<string, string>();
    for (const o of offices) m.set(o.id, o.name);
    return m;
  }, [offices]);

  const toggleOffice = (officeId: string) => {
    setSelectedOfficeIds((prev) => {
      const next = new Set(prev);
      if (next.has(officeId)) next.delete(officeId);
      else next.add(officeId);
      return next;
    });
  };

  const handleClose = () => {
    if (isBusy) return;
    onClose();
  };

  const handleAllocate = async () => {
    // H4 (W41 v1.0 cross-review): 未選択 (空) の場合は **空配列** を送信し、
    // Backend 側で「空 = 全拠点」と解釈させる. これにより useOffices の
    // limit 制約を回避できる (旧仕様は offices.map で全拠点 ID を送ろうとして
    // limit:100 を超える環境で漏れていた). 拠点が選択されていれば selected の
    // みを送信する.
    const officeIdsToSend = Array.from(selectedOfficeIds);
    try {
      await autoAllocateMut.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        office_ids: officeIdsToSend,
        mode,
      });
    } catch (err) {
      toast.error(`自動算出に失敗しました: ${formatErr(err)}`);
    }
  };

  const handleApply = async () => {
    if (!proposalBatchId) return;
    try {
      const res = await applyMut.mutateAsync(proposalBatchId);
      toast.success(
        `提案を採用しました (courses=${res.courses_committed}, visits=${res.visits_committed})`,
      );
      // 自動で閉じる (UI 上はトーストだけ残す).
      onClose();
    } catch (err) {
      toast.error(`採用に失敗しました: ${formatErr(err)}`);
    }
  };

  const handleDiscard = async () => {
    if (!proposalBatchId) return;
    if (!window.confirm('提案を破棄しますか？\nこの操作は取り消せません。')) {
      return;
    }
    try {
      const res = await discardMut.mutateAsync(proposalBatchId);
      toast.success(
        `提案を破棄しました (courses=${res.courses_deleted}, visits=${res.visits_deleted})`,
      );
      onClose();
    } catch (err) {
      toast.error(`破棄に失敗しました: ${formatErr(err)}`);
    }
  };

  const isoWeekLabel = formatWeekLabel(isoYear, isoWeek);

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) handleClose();
      }}
    >
      <DialogContent
        className="max-h-[90vh] max-w-3xl overflow-y-auto"
        data-testid="auto-schedule-dialog"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-brand-primary" aria-hidden />
            自動スケジュール算出
          </DialogTitle>
          <DialogDescription>
            対象週 <span className="font-semibold">{isoWeekLabel}</span> の course
            配置とスタッフ割付を自動で最適化します。 算出結果は提案 (proposed)
            状態で保存され、「採用」ボタンを押すまで本番には反映されません。
          </DialogDescription>
        </DialogHeader>

        {/* ─── Step 1: トリガー (結果がまだ無いとき) ──────────────── */}
        {!result ? (
          <div className="space-y-4 py-2" data-testid="auto-schedule-trigger">
            {/* モード選択 */}
            <div className="space-y-2">
              <Label htmlFor="auto-allocate-mode">算出モード</Label>
              <Select
                value={mode}
                onValueChange={(v) => setMode(v as AutoAllocateMode)}
                disabled={isAllocating}
              >
                <SelectTrigger id="auto-allocate-mode" data-testid="auto-allocate-mode-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="mode_1">{MODE_LABELS.mode_1}</SelectItem>
                  <SelectItem value="mode_2">{MODE_LABELS.mode_2}</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-text-muted">
                Mode 1: 既存の固定枠を維持しながら新規 visit を最適配置します。Mode 2:
                全体を一から再最適化します。
              </p>
            </div>

            {/* 対象拠点 (複数選択) */}
            <div className="space-y-2">
              <Label>対象拠点</Label>
              {officesQuery.isLoading ? (
                <p className="text-xs text-text-muted">拠点を読み込み中…</p>
              ) : offices.length === 0 ? (
                <p className="text-xs text-text-muted">拠点が登録されていません</p>
              ) : (
                <div
                  className="space-y-1.5 rounded border border-border-default bg-bg-muted p-2"
                  data-testid="auto-allocate-office-list"
                >
                  {offices.map((o) => {
                    const checked = selectedOfficeIds.has(o.id);
                    return (
                      <label key={o.id} className="flex cursor-pointer items-center gap-2 text-sm">
                        <Checkbox
                          checked={checked}
                          onCheckedChange={() => toggleOffice(o.id)}
                          disabled={isAllocating}
                          data-testid={`auto-allocate-office-${o.id}`}
                        />
                        <span>{o.name}</span>
                      </label>
                    );
                  })}
                </div>
              )}
              <p className="text-xs text-text-muted">未選択 (空) の場合は全拠点を対象にします。</p>
            </div>

            {/* 対象週 (表示のみ) */}
            <div className="space-y-1">
              <Label>対象週</Label>
              <p className="tnum text-sm text-text-primary">{isoWeekLabel}</p>
            </div>
          </div>
        ) : (
          /* ─── Step 2: 結果表示 ────────────────────────────────── */
          <div className="space-y-4 py-2" data-testid="auto-schedule-result">
            {/* KPI 5 項目 */}
            <section className="space-y-2">
              <h4 className="text-sm font-semibold text-text-primary">KPI</h4>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                <div className="rounded border border-border-default p-2">
                  <div className="text-[10px] text-text-muted">移動距離 (km)</div>
                  <div className="tnum text-sm font-semibold text-text-primary">
                    {formatNumber(result.kpi.total_distance_km_before)} →{' '}
                    {formatNumber(result.kpi.total_distance_km_after)}
                  </div>
                  <div className="tnum text-[10px] text-text-muted">
                    削減 {formatNumber(result.kpi.distance_reduction_pct, 1)}%
                  </div>
                </div>
                <div className="rounded border border-border-default p-2">
                  <div className="text-[10px] text-text-muted">スタッフ偏差</div>
                  <div className="tnum text-sm font-semibold text-text-primary">
                    {formatNumber(result.kpi.staff_load_stddev_before, 2)} →{' '}
                    {formatNumber(result.kpi.staff_load_stddev_after, 2)}
                  </div>
                </div>
                <div className="rounded border border-border-default p-2">
                  <div className="text-[10px] text-text-muted">改善スコア</div>
                  <div className="tnum text-sm font-semibold text-success">
                    {formatNumber(result.kpi.improvement_score, 2)}
                  </div>
                </div>
                <div className="rounded border border-border-default p-2">
                  <div className="text-[10px] text-text-muted">H 制約違反 (合計)</div>
                  <div className="tnum text-sm font-semibold text-text-primary">
                    {sumHViolations(result.kpi.h_violations)} 件
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1 text-[10px] text-text-secondary">
                    {Object.entries(result.kpi.h_violations).map(([k, v]) => (
                      <Badge key={k} variant={v > 0 ? 'destructive' : 'secondary'} className="tnum">
                        {k}: {v}
                      </Badge>
                    ))}
                  </div>
                </div>
                <div className="rounded border border-border-default p-2">
                  <div className="text-[10px] text-text-muted">警告件数</div>
                  <div className="tnum text-sm font-semibold text-text-primary">
                    {result.warnings.length} 件
                  </div>
                </div>
              </div>
            </section>

            {/* 警告 */}
            {result.warnings.length > 0 ? (
              <Alert variant="warning" data-testid="auto-schedule-warnings">
                <AlertTriangle className="h-4 w-4" aria-hidden />
                <AlertTitle>警告</AlertTitle>
                <AlertDescription>
                  <ul className="ml-4 list-disc space-y-0.5 text-xs">
                    {result.warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                </AlertDescription>
              </Alert>
            ) : (
              <Alert variant="default">
                <CheckCircle2 className="h-4 w-4 text-success" aria-hidden />
                <AlertTitle>警告なし</AlertTitle>
                <AlertDescription>ハード制約・ソフト制約とも警告は出ていません。</AlertDescription>
              </Alert>
            )}

            {/* 提案 course 一覧 */}
            <section className="space-y-2">
              <h4 className="text-sm font-semibold text-text-primary">
                提案コース ({result.proposals.length} 件)
              </h4>
              {result.proposals.length === 0 ? (
                <p className="text-xs text-text-muted">提案された course はありません。</p>
              ) : (
                <div className="overflow-x-auto rounded border border-border-default">
                  <table className="w-full text-xs" data-testid="auto-schedule-proposals-table">
                    <thead className="bg-bg-muted">
                      <tr>
                        <th className="px-2 py-1 text-left font-medium">拠点</th>
                        <th className="px-2 py-1 text-left font-medium">曜日</th>
                        <th className="px-2 py-1 text-left font-medium">コード</th>
                        <th className="px-2 py-1 text-left font-medium">担当</th>
                        <th className="px-2 py-1 text-right font-medium">visits</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.proposals.map((p) => (
                        <tr key={p.course_id} className="border-t border-border-default">
                          <td className="px-2 py-1">
                            {officeNameById.get(p.office_id) ?? p.office_id.slice(0, 8)}
                          </td>
                          <td className="px-2 py-1">{WEEKDAY_LABELS[p.weekday] ?? p.weekday}</td>
                          <td className="px-2 py-1 font-semibold">{p.code}</td>
                          <td className="px-2 py-1">
                            {p.assigned_staff_name ?? (
                              <span className="text-text-muted">未割当</span>
                            )}
                          </td>
                          <td className="tnum px-2 py-1 text-right">{p.visits_count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </div>
        )}

        {/* ─── エラー表示 (autoAllocate / apply / discard 共通) ──────── */}
        {autoAllocateMut.error ? (
          <Alert variant="destructive">
            <AlertTitle>自動算出に失敗しました</AlertTitle>
            <AlertDescription>{formatErr(autoAllocateMut.error)}</AlertDescription>
          </Alert>
        ) : null}
        {applyMut.error ? (
          <Alert variant="destructive">
            <AlertTitle>採用に失敗しました</AlertTitle>
            <AlertDescription>{formatErr(applyMut.error)}</AlertDescription>
          </Alert>
        ) : null}
        {discardMut.error ? (
          <Alert variant="destructive">
            <AlertTitle>破棄に失敗しました</AlertTitle>
            <AlertDescription>{formatErr(discardMut.error)}</AlertDescription>
          </Alert>
        ) : null}

        <DialogFooter>
          {!result ? (
            <>
              <Button type="button" variant="outline" onClick={handleClose} disabled={isAllocating}>
                キャンセル
              </Button>
              <Button
                type="button"
                onClick={handleAllocate}
                disabled={isAllocating || officesQuery.isLoading}
                data-testid="auto-allocate-run-button"
              >
                {isAllocating ? (
                  <>
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                    処理中…
                  </>
                ) : (
                  <>
                    <Sparkles className="mr-1 h-4 w-4" aria-hidden />
                    自動算出を実行
                  </>
                )}
              </Button>
            </>
          ) : (
            <>
              <Button
                type="button"
                variant="outline"
                onClick={handleDiscard}
                disabled={isBusy || isFinalized}
                data-testid="auto-allocate-discard-button"
              >
                {isDiscarding ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                ) : null}
                破棄
              </Button>
              <Button
                type="button"
                onClick={handleApply}
                disabled={isBusy || isFinalized || !result?.proposals.length}
                data-testid="auto-allocate-apply-button"
              >
                {isApplying ? <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden /> : null}
                採用
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
