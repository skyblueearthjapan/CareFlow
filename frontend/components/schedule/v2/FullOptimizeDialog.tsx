'use client';

/**
 * FullOptimizeDialog — Wave 41 v2 (全面最適化モード, 機能 B).
 *
 * 仕様書: ``docs/plans/auto-schedule-v2.md`` v0.2 §4, §7, §13.5.2
 *
 * フロー:
 *   1. 開くと POST /full-optimize で全 active 患者の再構築提案を算出 (時間かかる, spinner)
 *   2. KPI バー + 曜日タブ Before/After 並列表示 (week_proposals)
 *   3. 「個別調整」ボタン → 患者ごと Before/After ポップアップを順に表示 (individual_proposals)
 *   4. 1 件ずつ採用 → 当該患者の patient_fixed_visits 更新 (apply-individual)
 *   5. 「すべて見終わる」で閉じる
 *
 * 一括採用ボタンは設けない (Q2 確定).
 */
import * as React from 'react';
import { ArrowRight, CheckCircle2, Loader2, RefreshCw, Users, X } from 'lucide-react';
import { toast } from 'sonner';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useApplyIndividualMutation, useFullOptimizeMutation } from '@/lib/queries/autoScheduleV2';
import type {
  FullOptimizeResponse,
  IndividualProposal,
  V2CourseSummary,
  V2VisitForUI,
  V2VisitPlan,
  V2WeekdayBeforeAfter,
} from '@/lib/schemas/v2/autoScheduleV2';

import { formatDelta, formatErr, trimSeconds } from './_autoScheduleUtils';

// ─────────────────────────────────────────────────────────────────────────
// Constants / Helpers
// ─────────────────────────────────────────────────────────────────────────

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;
const DISPLAY_WEEKDAYS = [0, 1, 2, 3, 4, 5] as const;

function formatNumber(n: number | undefined, frac = 1): string {
  if (n === undefined || !Number.isFinite(n)) return '—';
  return n.toFixed(frac);
}

function fmtWd(weekday: number): string {
  return WEEKDAY_LABELS[weekday] ?? `?${weekday}`;
}

// ─────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────

export interface FullOptimizeDialogProps {
  open: boolean;
  onClose: () => void;
  isoYear: number;
  isoWeek: number;
  officeId: string | null;
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export function FullOptimizeDialog({
  open,
  onClose,
  isoYear,
  isoWeek,
  officeId,
}: FullOptimizeDialogProps) {
  const fetchMut = useFullOptimizeMutation();
  const applyMut = useApplyIndividualMutation();

  const [result, setResult] = React.useState<FullOptimizeResponse | null>(null);
  const [activeTab, setActiveTab] = React.useState<string>('0'); // weekday string
  // 個別調整モード関連.
  const [individualMode, setIndividualMode] = React.useState(false);
  const [activePatient, setActivePatient] = React.useState<IndividualProposal | null>(null);
  // 患者 ID → 採用/却下 のローカル状態.
  const [appliedPatientIds, setAppliedPatientIds] = React.useState<Set<string>>(new Set());
  const [rejectedPatientIds, setRejectedPatientIds] = React.useState<Set<string>>(new Set());

  // open のたびにリセット + 再計算.
  React.useEffect(() => {
    if (!open) return;
    setResult(null);
    setActiveTab('0');
    setIndividualMode(false);
    setActivePatient(null);
    setAppliedPatientIds(new Set());
    setRejectedPatientIds(new Set());
    fetchMut.reset();
    applyMut.reset();
    void (async () => {
      try {
        const res = await fetchMut.mutateAsync({
          iso_year: isoYear,
          iso_week: isoWeek,
          office_ids: officeId ? [officeId] : [],
        });
        setResult(res);
      } catch (err) {
        toast.error(`全面最適化に失敗しました: ${formatErr(err)}`);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, isoYear, isoWeek, officeId]);

  const isLoading = fetchMut.isPending;
  const isApplying = applyMut.isPending;
  const isBusy = isLoading || isApplying;

  const handleClose = () => {
    if (isBusy) return;
    onClose();
  };

  const weekProposalByWeekday = React.useMemo(() => {
    const map = new Map<number, V2WeekdayBeforeAfter>();
    if (result) for (const w of result.week_proposals) map.set(w.weekday, w);
    return map;
  }, [result]);

  const handleApplyPatient = async (p: IndividualProposal) => {
    if (!result) return;
    try {
      await applyMut.mutateAsync({
        proposal_batch_id: result.proposal_batch_id,
        patient_id: p.patient_id,
        confirm: true,
        iso_year: isoYear,
        iso_week: isoWeek,
        visit_plans: p.proposed_pfv,
      });
      toast.success(`${p.patient_name} の固定枠を更新しました`);
      setAppliedPatientIds((prev) => new Set(prev).add(p.patient_id));
      // 次の患者へ進む
      const next = remainingPatients.find((x) => x.patient_id !== p.patient_id);
      setActivePatient(next ?? null);
      if (!next) {
        setIndividualMode(false);
        toast.success('すべての患者の確認が完了しました');
      }
    } catch (err) {
      toast.error(`採用に失敗しました: ${formatErr(err)}`);
    }
  };

  const handleRejectPatient = (p: IndividualProposal) => {
    setRejectedPatientIds((prev) => new Set(prev).add(p.patient_id));
    // 次の患者へ進む
    const next = remainingPatients.find((x) => x.patient_id !== p.patient_id);
    setActivePatient(next ?? null);
    if (!next) {
      setIndividualMode(false);
      toast.success('すべての患者の確認が完了しました');
    }
  };

  const remainingPatients = React.useMemo(() => {
    if (!result) return [];
    return result.individual_proposals.filter(
      (p) => !appliedPatientIds.has(p.patient_id) && !rejectedPatientIds.has(p.patient_id),
    );
  }, [result, appliedPatientIds, rejectedPatientIds]);

  // ─── Render ──────────────────────────────────────────────────────
  return (
    <Dialog open={open} onOpenChange={(o) => (!o ? handleClose() : undefined)}>
      <DialogContent
        className="max-h-[92vh] max-w-5xl overflow-y-auto"
        data-testid="full-optimize-dialog"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <RefreshCw className="h-5 w-5 text-brand-primary" aria-hidden />
            全面最適化 - 週単位の再構築提案
          </DialogTitle>
          <DialogDescription>
            全 active 患者で固定枠を再算出し、移動距離・偏差を改善する提案を生成します。 採用は 1
            患者ずつ確認します (一括採用は不可)。
          </DialogDescription>
        </DialogHeader>

        {/* エラー */}
        {fetchMut.error ? (
          <Alert variant="destructive">
            <AlertTitle>算出に失敗しました</AlertTitle>
            <AlertDescription>{formatErr(fetchMut.error)}</AlertDescription>
          </Alert>
        ) : null}

        {/* ローディング (spinner) */}
        {isLoading || !result ? (
          isLoading ? (
            <div
              className="flex flex-col items-center justify-center gap-2 py-12 text-sm text-text-muted"
              data-testid="full-optimize-loading"
            >
              <Loader2 className="h-6 w-6 animate-spin" aria-hidden />全 active 患者で再構築中…
              (時間がかかる場合があります)
            </div>
          ) : null
        ) : (
          <div className="space-y-3 py-2" data-testid="full-optimize-result">
            {/* KPI バー */}
            <section
              className="grid grid-cols-2 gap-2 sm:grid-cols-4"
              data-testid="full-optimize-kpi"
            >
              <div className="rounded border border-border-default p-2">
                <div className="text-[10px] text-text-muted">移動距離 (km)</div>
                <div className="tnum text-sm font-semibold text-text-primary">
                  {formatNumber(result.kpi_overall.total_distance_km_before)} →{' '}
                  {formatNumber(result.kpi_overall.total_distance_km_after)}
                </div>
                <div className="tnum text-[10px] text-text-muted">
                  削減 {formatNumber(result.kpi_overall.distance_reduction_pct, 1)}%
                </div>
              </div>
              <div className="rounded border border-border-default p-2">
                <div className="text-[10px] text-text-muted">コース数</div>
                <div className="tnum text-sm font-semibold text-text-primary">
                  {result.kpi_overall.courses_count_before} →{' '}
                  {result.kpi_overall.courses_count_after}
                </div>
              </div>
              <div className="rounded border border-border-default p-2">
                <div className="text-[10px] text-text-muted">容量超過</div>
                <div className="tnum text-sm font-semibold text-text-primary">
                  {result.kpi_overall.capacity_overflows} 件
                </div>
              </div>
              <div className="rounded border border-border-default p-2">
                <div className="text-[10px] text-text-muted">警告</div>
                <div className="tnum text-sm font-semibold text-text-primary">
                  {result.warnings.length} 件
                </div>
              </div>
            </section>

            {/* 警告 */}
            {result.warnings.length > 0 ? (
              <Alert variant="warning">
                <AlertTitle className="text-xs">警告</AlertTitle>
                <AlertDescription>
                  <ul className="ml-4 list-disc space-y-0.5 text-xs">
                    {result.warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                </AlertDescription>
              </Alert>
            ) : null}

            {/* 曜日タブ Before/After */}
            <Tabs value={activeTab} onValueChange={setActiveTab}>
              <TabsList className="flex w-full flex-wrap gap-1 bg-bg-muted">
                {DISPLAY_WEEKDAYS.map((wd) => (
                  <TabsTrigger key={wd} value={String(wd)} data-testid={`full-optimize-tab-${wd}`}>
                    {WEEKDAY_LABELS[wd]}
                  </TabsTrigger>
                ))}
                <TabsTrigger value="all" data-testid="full-optimize-tab-all">
                  全体
                </TabsTrigger>
              </TabsList>

              {DISPLAY_WEEKDAYS.map((wd) => {
                const wp = weekProposalByWeekday.get(wd);
                return (
                  <TabsContent
                    key={wd}
                    value={String(wd)}
                    data-testid={`full-optimize-panel-${wd}`}
                  >
                    {wp ? (
                      <BeforeAfterWeekPanel weekday={wd} proposal={wp} />
                    ) : (
                      <div className="py-6 text-center text-xs text-text-muted">
                        {WEEKDAY_LABELS[wd]}曜日の提案はありません
                      </div>
                    )}
                  </TabsContent>
                );
              })}

              <TabsContent value="all" data-testid="full-optimize-panel-all">
                <AllWeekSummary proposals={result.week_proposals} />
              </TabsContent>
            </Tabs>
          </div>
        )}

        <DialogFooter className="flex-col gap-2 sm:flex-row">
          <Button type="button" variant="outline" onClick={handleClose} disabled={isBusy}>
            すべて見終わる
          </Button>
          <Button
            type="button"
            onClick={() => {
              setIndividualMode(true);
              const next = remainingPatients[0];
              setActivePatient(next ?? null);
              if (!next) toast.info('調整可能な患者がありません');
            }}
            disabled={isBusy || !result || result.individual_proposals.length === 0}
            data-testid="full-optimize-individual-button"
          >
            <Users className="mr-1 h-4 w-4" aria-hidden />
            個別調整 ({remainingPatients.length} 件)
          </Button>
        </DialogFooter>
      </DialogContent>

      {/* 個別調整: 患者ごと Before/After ポップアップ */}
      {individualMode && activePatient ? (
        <IndividualPatientPopup
          proposal={activePatient}
          isApplying={isApplying}
          onApply={() => handleApplyPatient(activePatient)}
          onReject={() => handleRejectPatient(activePatient)}
          onNext={() => {
            const next = remainingPatients.find((p) => p.patient_id !== activePatient.patient_id);
            setActivePatient(next ?? null);
            if (!next) {
              setIndividualMode(false);
              toast.success('すべての患者の確認が完了しました');
            }
          }}
          remaining={remainingPatients.length}
        />
      ) : null}
    </Dialog>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// BeforeAfterWeekPanel — 曜日タブの中身 (Before / After を 2 列で並べる)
//
// Backend は ``before/after = { courses: V2CourseSummary[] }`` で渡してくる.
// ─────────────────────────────────────────────────────────────────────────

function totalsFor(courses: V2CourseSummary[]): { distance: number; visits: number } {
  let distance = 0;
  let visits = 0;
  for (const c of courses) {
    distance += c.distance_km;
    visits += c.visits_count;
  }
  return { distance, visits };
}

function BeforeAfterWeekPanel({
  weekday,
  proposal,
}: {
  weekday: number;
  proposal: V2WeekdayBeforeAfter;
}) {
  const beforeTotal = totalsFor(proposal.before.courses);
  const afterTotal = totalsFor(proposal.after.courses);
  return (
    <div
      className="grid grid-cols-1 gap-2 md:grid-cols-2"
      data-testid={`full-optimize-before-after-${weekday}`}
    >
      <CourseListColumn
        title="Before"
        courses={proposal.before.courses}
        total={beforeTotal}
        tone="muted"
      />
      <CourseListColumn
        title="After"
        courses={proposal.after.courses}
        total={afterTotal}
        tone="primary"
      />
      <div className="md:col-span-2 rounded border border-border-default p-2 text-xs">
        <div className="font-semibold text-text-primary">変化</div>
        <ul className="ml-4 mt-1 list-disc text-text-secondary">
          <li>距離: {formatDelta(afterTotal.distance - beforeTotal.distance)}</li>
          <li>
            訪問数: {beforeTotal.visits} → {afterTotal.visits}
          </li>
        </ul>
      </div>
    </div>
  );
}

function CourseListColumn({
  title,
  courses,
  total,
  tone,
}: {
  title: string;
  courses: V2CourseSummary[];
  total: { distance: number; visits: number };
  tone: 'muted' | 'primary';
}) {
  const headerCls =
    tone === 'primary'
      ? 'border-brand-primary/40 bg-brand-primary/5 text-brand-primary'
      : 'border-border-default bg-bg-muted text-text-muted';
  return (
    <div className="overflow-hidden rounded border border-border-default">
      <div
        className={`flex items-center justify-between border-b px-2 py-1 text-[11px] font-semibold ${headerCls}`}
      >
        <span>{title}</span>
        <span className="tnum text-text-secondary">
          {total.visits}件 / {total.distance.toFixed(1)}km
        </span>
      </div>
      {courses.length === 0 ? (
        <div className="py-4 text-center text-[11px] text-text-muted">(コースなし)</div>
      ) : (
        <ul className="divide-y divide-border-default">
          {courses.map((c) => (
            <li key={`${c.code}-${c.assigned_staff_id ?? 'none'}`} className="px-2 py-1.5">
              <div className="flex items-center justify-between text-[11px]">
                <span className="font-semibold text-text-primary">{c.code} コース</span>
                <span className="tnum text-text-muted">
                  {c.visits_count}件 / {c.distance_km.toFixed(1)}km
                </span>
              </div>
              {c.visits.length > 0 ? (
                <ul className="mt-1 space-y-0.5">
                  {c.visits.slice(0, 8).map((v: V2VisitForUI, i) => (
                    <li key={i} className="flex items-center gap-1.5 text-[10px]">
                      <span className="tnum text-text-muted">{trimSeconds(v.start_time)}</span>
                      <span className="text-text-primary">{v.patient_name}</span>
                    </li>
                  ))}
                  {c.visits.length > 8 ? (
                    <li className="text-[10px] text-text-muted">…他 {c.visits.length - 8} 件</li>
                  ) : null}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// AllWeekSummary — 「全体」タブの中身 (曜日ごとの距離変化を縦並び一覧)
// ─────────────────────────────────────────────────────────────────────────

function AllWeekSummary({ proposals }: { proposals: V2WeekdayBeforeAfter[] }) {
  if (proposals.length === 0) {
    return <div className="py-4 text-center text-xs text-text-muted">提案がありません</div>;
  }
  return (
    <div className="overflow-x-auto" data-testid="full-optimize-all-summary">
      <table className="w-full text-xs">
        <thead className="bg-bg-muted text-[10px] text-text-muted">
          <tr>
            <th className="px-2 py-1 text-left">曜日</th>
            <th className="px-2 py-1 text-right">Before km</th>
            <th className="px-2 py-1 text-right">After km</th>
            <th className="px-2 py-1 text-right">距離 Δ</th>
            <th className="px-2 py-1 text-right">訪問 Before</th>
            <th className="px-2 py-1 text-right">訪問 After</th>
          </tr>
        </thead>
        <tbody>
          {proposals.map((p) => {
            const b = totalsFor(p.before.courses);
            const a = totalsFor(p.after.courses);
            return (
              <tr key={p.weekday} className="border-t border-border-default">
                <td className="px-2 py-1">{fmtWd(p.weekday)}</td>
                <td className="tnum px-2 py-1 text-right">{b.distance.toFixed(1)}</td>
                <td className="tnum px-2 py-1 text-right">{a.distance.toFixed(1)}</td>
                <td className="tnum px-2 py-1 text-right">
                  {formatDelta(a.distance - b.distance)}
                </td>
                <td className="tnum px-2 py-1 text-right">{b.visits}</td>
                <td className="tnum px-2 py-1 text-right">{a.visits}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// IndividualPatientPopup — 個別調整モードのポップアップ
// ─────────────────────────────────────────────────────────────────────────

interface IndividualPatientPopupProps {
  proposal: IndividualProposal;
  isApplying: boolean;
  remaining: number;
  onApply: () => void;
  onReject: () => void;
  onNext: () => void;
}

function VisitPlanList({
  title,
  plans,
  tone,
}: {
  title: string;
  plans: V2VisitPlan[];
  tone: 'muted' | 'primary';
}) {
  const cls =
    tone === 'primary'
      ? 'border-brand-primary/40 bg-brand-primary/5'
      : 'border-border-default bg-bg-muted';
  return (
    <div className={`rounded border p-2 text-xs ${cls}`}>
      <div className="text-[10px] font-semibold uppercase text-text-muted">{title}</div>
      {plans.length === 0 ? (
        <div className="mt-1 text-[11px] text-text-muted">(なし)</div>
      ) : (
        <ul className="mt-1 space-y-0.5">
          {plans.map((v, i) => (
            <li key={i} className="tnum flex justify-between">
              <span>
                {fmtWd(v.weekday)} {trimSeconds(v.start_time)}
              </span>
              <span className="text-text-muted">{v.course_code}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function IndividualPatientPopup({
  proposal,
  isApplying,
  remaining,
  onApply,
  onReject,
  onNext,
}: IndividualPatientPopupProps) {
  const proposedSummary = proposal.proposed_pfv[0];
  return (
    <Dialog open onOpenChange={(o) => (!o ? onNext() : undefined)}>
      <DialogContent
        className="max-w-md"
        data-testid={`full-optimize-popup-${proposal.patient_id}`}
      >
        <DialogHeader>
          <DialogTitle className="text-base">
            {proposal.patient_name} 様
            <Badge variant="secondary" className="ml-2 text-[10px]">
              残り {remaining} 件
            </Badge>
          </DialogTitle>
          <DialogDescription>
            {proposedSummary
              ? `提案: ${fmtWd(proposedSummary.weekday)} ${trimSeconds(proposedSummary.start_time)} ${proposedSummary.course_code} コース`
              : '提案 visit がありません'}
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-2">
          <VisitPlanList title="Before" plans={proposal.current_pfv} tone="muted" />
          <VisitPlanList title="After" plans={proposal.proposed_pfv} tone="primary" />
        </div>

        <div className="rounded border border-border-default p-2 text-xs">
          <div className="font-semibold text-text-primary">影響</div>
          <ul className="ml-4 mt-1 list-disc space-y-0.5 text-text-secondary">
            <li>距離: {formatDelta(proposal.delta.distance_km)}</li>
            {proposal.delta.capacity ? <li>容量: {proposal.delta.capacity}</li> : null}
            <li>
              訪問数: {proposal.delta.course_visits_count_before} →{' '}
              {proposal.delta.course_visits_count_after}
            </li>
          </ul>
        </div>

        {proposal.warnings.length > 0 ? (
          <Alert variant="warning">
            <AlertTitle className="text-xs">警告</AlertTitle>
            <AlertDescription>
              <ul className="ml-4 list-disc space-y-0.5 text-xs">
                {proposal.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}

        <DialogFooter className="gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={onReject}
            disabled={isApplying}
            data-testid="full-optimize-popup-reject"
          >
            <X className="mr-1 h-4 w-4" aria-hidden />
            却下
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={onNext}
            disabled={isApplying}
            data-testid="full-optimize-popup-next"
          >
            <ArrowRight className="mr-1 h-4 w-4" aria-hidden />
            スキップ
          </Button>
          <Button
            type="button"
            onClick={onApply}
            disabled={isApplying || proposal.proposed_pfv.length === 0}
            data-testid="full-optimize-popup-apply"
          >
            {isApplying ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <CheckCircle2 className="mr-1 h-4 w-4" aria-hidden />
            )}
            この患者を採用
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
