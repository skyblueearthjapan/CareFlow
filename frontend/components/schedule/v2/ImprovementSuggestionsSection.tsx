'use client';

/**
 * ImprovementSuggestionsSection — 配置済み患者向けの改善提案セクション (P2-C).
 *
 * PatientScheduleDetailDialog の「固定枠 vs 今週比較」と「プール投入提案」の間に
 * `!enablePoolProposal`(= 配置済み患者クリック) のときだけ表示する。
 *
 * 役割:
 *   - useImprovementSuggestions で単一患者の改善提案を取得 (staleTime 5 分).
 *   - 提案カード群 (ImprovementSuggestionCard) を描画し、採用/見送りを配線する。
 *   - 採用 = useConfirmFixedVisits + mergeAdoptedIntoNormalFixedVisits 流用
 *     (他曜日の movability/is_pinned を保持; day_change は元曜日を除去して「移動」).
 *     成功時 toastFixedVisitWarnings (P0-2) + カード除去 + improvement-suggestions invalidate.
 *   - 見送り = DismissReasonDialog を開き、理由 → (昇格確認) → dismiss POST.
 *   - 0 件時は filtered_summary から非表示内訳テキストを出す (0 カテゴリは省略).
 *   - API 未デプロイ環境でも壊れない: fetch エラー時はセクション内にエラーテキストのみ表示し、
 *     ダイアログ全体は生かす。
 *
 * デザイン: Warm & Human トークンのみ.
 */
import * as React from 'react';
import { useQueries, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { fetcher } from '@/lib/api/fetcher';
import {
  IMPROVEMENT_SUGGESTIONS_KEY,
  useApplySwap,
  useImprovementSuggestions,
} from '@/lib/queries/improvementSuggestions';
import { useConfirmFixedVisits } from '@/lib/queries/propose_confirm';
import { useVisitMoveWeekOnly } from '@/lib/queries/visitMoveWeekOnly';
import { useFixedVisits, toastFixedVisitWarnings } from '@/lib/queries/patient_fixed_visits';
import { coerceWeeklyPattern, type PatientRead } from '@/lib/schemas/patient';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type {
  ImprovementFilteredSummary,
  ImprovementSuggestion,
} from '@/lib/schemas/v2/improvementSuggestion';
import type {
  PatientFixedVisitsBulkPut,
  PatientFixedVisitV2Read,
} from '@/lib/schemas/v2/patient_fixed_visit';

import {
  buildCourseTemplateIdResolver,
  improvementCandidateToFixedVisitItem,
  mergeAdoptedIntoNormalFixedVisits,
} from './_proposeSlotUtils';
import { ChangeScopeChoice, type ChangeScopeValue } from './ChangeScopeChoice';
import { CourseMoveTimeline } from './CourseMoveTimeline';
import { DismissReasonDialog } from './DismissReasonDialog';
import { ImprovementSuggestionCard } from './ImprovementSuggestionCard';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

/** 提案の指紋 (dismiss / 採用済み判定と同一: kind × target_weekday). */
function fingerprint(s: ImprovementSuggestion): string {
  return `${s.kind}-${s.target_weekday}`;
}

/** filtered_summary → 「ピン留めN件・閾値未満N件…」の内訳テキスト (0 は省略). */
function summarizeFiltered(fs: ImprovementFilteredSummary): string[] {
  const parts: Array<[number, string]> = [
    [fs.pinned, `ピン留め${fs.pinned}件`],
    [fs.locked, `可動域が完全固定${fs.locked}件`],
    [fs.below_threshold, `効果が閾値未満${fs.below_threshold}件`],
    [fs.dismissed, `却下済み${fs.dismissed}件`],
    [fs.day_restricted, `曜日変更が未許可${fs.day_restricted}件`],
    [fs.no_current_visit, `当週訪問なし${fs.no_current_visit}件`],
  ];
  return parts.filter(([n]) => n > 0).map(([, label]) => label);
}

export interface ImprovementSuggestionsSectionProps {
  patient: PatientRead;
  isoYear: number;
  isoWeek: number;
  /** admin / manager のみ採用/見送り可 (RBAC; BE でも担保). */
  canEdit: boolean;
  /** 採用確定後に親へ通知 (画面更新トリガ). */
  onAdopted?: () => void;
}

export function ImprovementSuggestionsSection({
  patient,
  isoYear,
  isoWeek,
  canEdit,
  onAdopted,
}: ImprovementSuggestionsSectionProps) {
  const { data: session, status: sessionStatus } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  const suggestionsQuery = useImprovementSuggestions(patient.id, isoYear, isoWeek);
  const confirmMut = useConfirmFixedVisits();
  const applySwapMut = useApplySwap();
  const visitMoveWeekOnlyMut = useVisitMoveWeekOnly();
  // マージ確定用に既存 normal 固定枠を取得 (採用しなかった曜日を保持するため).
  const existingFixedVisitsQuery = useFixedVisits(patient.id, 'normal');

  // 採用済み / 見送り済みの指紋 (invalidate 後の refetch までカードをローカルで隠す).
  const [handled, setHandled] = React.useState<Set<string>>(new Set());
  // 採用処理中の指紋 (ボタン disable 用).
  const [adoptingFp, setAdoptingFp] = React.useState<string | null>(null);
  // 見送りダイアログの対象.
  const [dismissTarget, setDismissTarget] = React.useState<ImprovementSuggestion | null>(null);
  // スワップ採用の確認ダイアログの対象.
  const [swapConfirmTarget, setSwapConfirmTarget] = React.useState<ImprovementSuggestion | null>(
    null,
  );
  // Wave U-2: move 採用の確認ダイアログの対象 (move も確認ステップで反映先を選ぶ).
  const [moveConfirmTarget, setMoveConfirmTarget] = React.useState<ImprovementSuggestion | null>(
    null,
  );
  // Wave U-2: 反映先の選択 (move / swap 共通・既定 A = 固定訪問週間に登録).
  const [moveScope, setMoveScope] = React.useState<ChangeScopeValue>('pattern');
  const [swapScope, setSwapScope] = React.useState<ChangeScopeValue>('pattern');

  // 患者・週が変わったらローカル状態をリセット.
  React.useEffect(() => {
    setHandled(new Set());
    setAdoptingFp(null);
    setDismissTarget(null);
    setSwapConfirmTarget(null);
    setMoveConfirmTarget(null);
    setMoveScope('pattern');
    setSwapScope('pattern');
  }, [patient.id, isoYear, isoWeek]);

  const allSuggestions = React.useMemo(
    () => suggestionsQuery.data?.suggestions ?? [],
    [suggestionsQuery.data],
  );
  const visibleSuggestions = React.useMemo(
    () => allSuggestions.filter((s) => !handled.has(fingerprint(s))),
    [allSuggestions, handled],
  );

  // 採用枠 → course_template_id 解決のため、候補に出現する拠点の course-templates を取得.
  const candidateOfficeIds = React.useMemo(() => {
    const set = new Set<string>();
    for (const s of allSuggestions) set.add(s.candidate.office_id);
    return Array.from(set).sort();
  }, [allSuggestions]);
  const templatesQueries = useQueries({
    queries: candidateOfficeIds.map((oid) => ({
      queryKey: ['course-templates', 'list', oid] as const,
      enabled: sessionStatus === 'authenticated' && Boolean(oid),
      staleTime: 5 * 60 * 1000,
      queryFn: () =>
        fetcher<CourseTemplateRead[]>(
          `/api/v1/course-templates?office_id=${encodeURIComponent(oid)}`,
          { accessToken, refreshToken },
        ),
    })),
  });
  const templatesDepKey = templatesQueries.map((q) => `${q.dataUpdatedAt}:${q.status}`).join(',');
  const resolveCourseTemplateId = React.useMemo(
    () => buildCourseTemplateIdResolver(templatesQueries.map((q) => q.data)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [templatesDepKey],
  );

  // 採用 1 件を既存 normal 枠にマージ. day_change は元曜日 (target_weekday) の
  // slot_index=0 を除去してから採用枠を足す (= 「移動」). time_change は同曜日なので
  // mergeAdoptedIntoNormalFixedVisits が採用曜日を置換する.
  const buildMergedPut = React.useCallback(
    (existing: PatientFixedVisitV2Read[], s: ImprovementSuggestion): PatientFixedVisitsBulkPut => {
      const wp = coerceWeeklyPattern(patient.weekly_pattern);
      const adoptedItem = improvementCandidateToFixedVisitItem(
        s.candidate,
        resolveCourseTemplateId,
        wp.service_minutes,
      );
      let base = existing;
      if (s.kind === 'day_change' && s.target_weekday !== s.candidate.weekday) {
        // 設計意図 (レビューL1): 除去は slot0 のみ。slot1 (2名体制の相方) は自動では
        // 動かさず元曜日に残す — 相方の移動は人間の判断 (片肺化は採用後の再検証警告
        // と固定枠パネルで可視化される)。
        base = existing.filter(
          (v) => !(v.weekday === s.target_weekday && (v.slot_index ?? 0) === 0),
        );
      }
      return {
        mode: 'normal',
        items: mergeAdoptedIntoNormalFixedVisits(base, [adoptedItem]),
      } satisfies PatientFixedVisitsBulkPut;
    },
    [patient.weekly_pattern, resolveCourseTemplateId],
  );

  // Wave U-2: 採用ボタン = 確認ステップを開く (move も swap も反映先 A/B を選ばせる).
  const handleAdopt = React.useCallback((s: ImprovementSuggestion) => {
    if (s.kind === 'swap') {
      setSwapScope('pattern');
      setSwapConfirmTarget(s);
      return;
    }
    setMoveScope('pattern');
    setMoveConfirmTarget(s);
  }, []);

  // Wave U-2: move 採用の確定 (反映先 A/B 付き).
  //   A (pattern): PUT fixed-visits に change_scope='pattern_and_week' + iso を付与 (型 + 今週).
  //   B (week):    visit-move-week-only で今週の visit だけ移す (型は不変).
  const handleConfirmMove = React.useCallback(() => {
    const s = moveConfirmTarget;
    if (!s) return;
    const fp = fingerprint(s);

    if (moveScope === 'pattern') {
      // ─── A 経路: 型変更 + 今週即反映 ─────────────────────────────
      if (existingFixedVisitsQuery.isLoading || existingFixedVisitsQuery.isFetching) {
        toast.warning('既存の固定枠を読み込み中です。少し待ってからお試しください');
        return;
      }
      if (existingFixedVisitsQuery.isError) {
        toast.error(
          '既存の固定枠の読み込みに失敗しました。他曜日の枠を保護できないため中止しました',
        );
        return;
      }
      if (templatesQueries.some((q) => q.isLoading)) {
        toast.warning('コース情報を読み込み中です。少し待ってからお試しください');
        return;
      }
      if (confirmMut.isPending) return; // レビューM2: 並行採用の二重ガード
      const existing = existingFixedVisitsQuery.data ?? [];
      setAdoptingFp(fp);
      const putBody: PatientFixedVisitsBulkPut = {
        ...buildMergedPut(existing, s),
        change_scope: 'pattern_and_week',
        iso_year: isoYear,
        iso_week: isoWeek,
      };
      confirmMut.mutate(
        { patientId: patient.id, body: putBody },
        {
          onSuccess: (data) => {
            const candWd = WEEKDAY_LABELS[s.candidate.weekday] ?? '?';
            if (data?.week_sync == null) {
              toast.warning(
                `${patient.name} 様の枠を ${candWd} ${s.candidate.start_time.slice(0, 5)} に変更しました（固定訪問週間に登録）が、` +
                  `今週のスケジュールへの反映は行われませんでした。「週を生成」を再実行すると反映されます`,
              );
            } else {
              toast.success(
                `${patient.name} 様の枠を ${candWd} ${s.candidate.start_time.slice(0, 5)} に変更し、今週にも反映しました（他曜日は維持）`,
              );
            }
            toastFixedVisitWarnings(data?.warnings);
            setHandled((prev) => new Set(prev).add(fp));
            setAdoptingFp(null);
            setMoveConfirmTarget(null);
            void qc.invalidateQueries({ queryKey: [IMPROVEMENT_SUGGESTIONS_KEY] });
            onAdopted?.();
          },
          onError: () => {
            setAdoptingFp(null);
            toast.error('採用に失敗しました');
          },
        },
      );
      return;
    }

    // ─── B 経路: この週だけ移動 (型は不変) ───────────────────────────
    if (visitMoveWeekOnlyMut.isPending) return;
    if (templatesQueries.some((q) => q.isLoading)) {
      toast.warning('コース情報を読み込み中です。少し待ってからお試しください');
      return;
    }
    const tplId = resolveCourseTemplateId(s.candidate.office_id, s.candidate.course_code);
    setAdoptingFp(fp);
    visitMoveWeekOnlyMut.mutate(
      {
        iso_year: isoYear,
        iso_week: isoWeek,
        patient_id: patient.id,
        old_weekday: s.current.weekday,
        old_start_time: s.current.start_time,
        new_weekday: s.candidate.weekday,
        new_start_time: s.candidate.start_time,
        ...(tplId ? { new_course_template_id: tplId } : {}),
      },
      {
        onSuccess: () => {
          toast.success(
            `${patient.name} 様の枠をこの週だけ反映しました（毎週の型は変更していません）`,
          );
          setHandled((prev) => new Set(prev).add(fp));
          setAdoptingFp(null);
          setMoveConfirmTarget(null);
          void qc.invalidateQueries({ queryKey: [IMPROVEMENT_SUGGESTIONS_KEY] });
          onAdopted?.();
        },
        onError: () => {
          setAdoptingFp(null);
          toast.error('この週だけの反映に失敗しました');
        },
      },
    );
  }, [
    moveConfirmTarget,
    moveScope,
    existingFixedVisitsQuery,
    templatesQueries,
    patient.id,
    patient.name,
    confirmMut,
    visitMoveWeekOnlyMut,
    resolveCourseTemplateId,
    buildMergedPut,
    isoYear,
    isoWeek,
    qc,
    onAdopted,
  ]);

  const handleDismissed = React.useCallback(() => {
    if (dismissTarget) {
      const fp = fingerprint(dismissTarget);
      setHandled((prev) => new Set(prev).add(fp));
    }
  }, [dismissTarget]);

  // スワップ確認 → apply-swap 実行.
  //   a = 表示中患者 X. a_new = candidate 由来 (= Y の旧枠へ X が移る).
  //     course_template_id は candidate.office_id + candidate.course_code を resolver で解決.
  //   b = counterpart Y. b_new = counterpart.new_* (= X の旧枠へ Y が移る).
  //     Y の移動先 (X の旧枠) の course は current に course_code が無く解決不能なため
  //     course_template_id は省略 (BE 契約上 optional).
  const handleConfirmSwap = React.useCallback(() => {
    const s = swapConfirmTarget;
    const cp = s?.swap_counterpart;
    if (!s || !cp) return;
    if (applySwapMut.isPending) return;
    const fp = fingerprint(s);
    const aTpl = resolveCourseTemplateId(s.candidate.office_id, s.candidate.course_code);
    // Wave U-2: 反映先 A=型入替+今週 / B=今週だけ.
    const changeScope = swapScope === 'pattern' ? 'pattern_and_week' : 'week_only';
    applySwapMut.mutate(
      {
        patient_a_id: patient.id,
        patient_b_id: cp.patient_id,
        a_new: {
          weekday: s.candidate.weekday,
          start_time: s.candidate.start_time,
          ...(aTpl ? { course_template_id: aTpl } : {}),
        },
        b_new: {
          weekday: cp.new_weekday,
          start_time: cp.new_start_time,
        },
        iso_year: isoYear,
        iso_week: isoWeek,
        change_scope: changeScope,
      },
      {
        onSuccess: (data) => {
          if (swapScope === 'pattern') {
            if (data?.week_sync == null) {
              toast.warning(
                `${patient.name} 様と ${cp.patient_name} 様の枠を固定訪問週間で入れ替えましたが、` +
                  `今週のスケジュールへの反映は行われませんでした。「週を生成」を再実行すると反映されます`,
              );
            } else {
              toast.success(
                `${patient.name} 様と ${cp.patient_name} 様の枠を入れ替え、今週にも反映しました`,
              );
            }
          } else {
            toast.success(
              `${patient.name} 様と ${cp.patient_name} 様の枠をこの週だけ入れ替えました（毎週の型は変更していません）`,
            );
          }
          // apply-swap の warnings は現場向け日本語文字列配列 (非致命).
          const ws = data?.warnings ?? [];
          for (const w of ws.slice(0, 3)) toast.warning(w);
          if (ws.length > 3) toast.warning(`他 ${ws.length - 3} 件の警告があります`);
          setHandled((prev) => new Set(prev).add(fp));
          setSwapConfirmTarget(null);
          onAdopted?.();
        },
        onError: () => toast.error('入れ替えに失敗しました'),
      },
    );
  }, [
    swapConfirmTarget,
    swapScope,
    applySwapMut,
    resolveCourseTemplateId,
    patient.id,
    patient.name,
    isoYear,
    isoWeek,
    onAdopted,
  ]);

  // ─── Render ───────────────────────────────────────────────────────
  const isLoading = suggestionsQuery.isLoading;
  const isError = suggestionsQuery.isError;
  const filtered = suggestionsQuery.data?.filtered_summary;

  return (
    <section
      className="rounded border border-border-default p-3"
      data-testid="improvement-suggestions-section"
    >
      <h3 className="mb-2 text-sm font-semibold text-text-primary">配置改善の提案</h3>

      {isLoading ? (
        <div className="flex items-center gap-2 py-3 text-xs text-text-muted">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          算出中…
        </div>
      ) : isError ? (
        // API 未デプロイ / 通信失敗でもダイアログ全体は生かす (セクション内テキストのみ).
        <div className="py-2 text-xs text-text-muted" data-testid="improvement-suggestions-error">
          改善提案を取得できませんでした。時間をおいて再度お試しください。
        </div>
      ) : visibleSuggestions.length > 0 ? (
        <div className="space-y-3" data-testid="improvement-suggestions-list">
          {visibleSuggestions.map((s) => (
            <div key={fingerprint(s)} className="space-y-1">
              <ImprovementSuggestionCard
                suggestion={s}
                canEdit={canEdit}
                patientName={patient.name}
                // 採用実行中は全カードを無効化 (レビューM2: 同一 existing スナップショット
                // からの並行 PUT で先行採用が上書き消失するのを防ぐ)。
                adopting={
                  adoptingFp === fingerprint(s) ||
                  confirmMut.isPending ||
                  applySwapMut.isPending ||
                  visitMoveWeekOnlyMut.isPending
                }
                onAdopt={handleAdopt}
                onDismiss={setDismissTarget}
              />
              {/* UI 統一: 範囲最適化と同じコースタイムライン (同一コース=1枚/別コース=2枚並列). */}
              <CourseMoveTimeline
                suggestion={s}
                targetPatientId={patient.id}
                patientName={patient.name}
                sourceCourse={s.source_course}
                destinationCourse={s.destination_course}
              />
            </div>
          ))}
        </div>
      ) : (
        <div className="py-2 text-xs text-text-muted" data-testid="improvement-suggestions-empty">
          {(() => {
            const parts = filtered ? summarizeFiltered(filtered) : [];
            if (parts.length === 0) {
              return 'いまのところ改善できる提案はありません。';
            }
            return `改善提案はありません（${parts.join('・')}で非表示）。`;
          })()}
        </div>
      )}

      <DismissReasonDialog
        open={dismissTarget != null}
        suggestion={dismissTarget}
        patientId={patient.id}
        onClose={() => setDismissTarget(null)}
        onDismissed={handleDismissed}
      />

      <Dialog
        open={swapConfirmTarget != null}
        onOpenChange={(o) =>
          !o && !applySwapMut.isPending ? setSwapConfirmTarget(null) : undefined
        }
      >
        <DialogContent aria-describedby="swap-confirm-desc" data-testid="swap-confirm-dialog">
          <DialogHeader>
            <DialogTitle>2名の枠を入れ替えますか？</DialogTitle>
            <DialogDescription id="swap-confirm-desc">
              {swapConfirmTarget?.swap_counterpart
                ? `2名の枠を同時に入れ替えます。${swapConfirmTarget.swap_counterpart.patient_name} 様にも変更が生じます。`
                : '2名の枠を同時に入れ替えます。'}
            </DialogDescription>
          </DialogHeader>
          {/* Wave U-2: 反映先の選択 (既定 A). */}
          <div className="py-1">
            <ChangeScopeChoice
              value={swapScope}
              onChange={setSwapScope}
              disabled={applySwapMut.isPending}
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setSwapConfirmTarget(null)}
              disabled={applySwapMut.isPending}
              data-testid="swap-confirm-cancel"
            >
              キャンセル
            </Button>
            <Button
              type="button"
              onClick={handleConfirmSwap}
              disabled={applySwapMut.isPending}
              data-testid="swap-confirm-apply"
            >
              {applySwapMut.isPending ? (
                <>
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                  入れ替え中…
                </>
              ) : (
                '入れ替える'
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Wave U-2: move 採用の確認ダイアログ (反映先 A/B を選ぶ). */}
      <Dialog
        open={moveConfirmTarget != null}
        onOpenChange={(o) =>
          !o && !confirmMut.isPending && !visitMoveWeekOnlyMut.isPending
            ? setMoveConfirmTarget(null)
            : undefined
        }
      >
        <DialogContent aria-describedby="move-confirm-desc" data-testid="move-confirm-dialog">
          <DialogHeader>
            <DialogTitle>枠の変更を反映しますか？</DialogTitle>
            <DialogDescription id="move-confirm-desc">
              {moveConfirmTarget
                ? `${patient.name} 様の枠を ${WEEKDAY_LABELS[moveConfirmTarget.candidate.weekday] ?? '?'} ${moveConfirmTarget.candidate.start_time.slice(0, 5)} に変更します。反映先を選んでください。`
                : '反映先を選んでください。'}
            </DialogDescription>
          </DialogHeader>
          <div className="py-1">
            <ChangeScopeChoice
              value={moveScope}
              onChange={setMoveScope}
              disabled={confirmMut.isPending || visitMoveWeekOnlyMut.isPending}
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setMoveConfirmTarget(null)}
              disabled={confirmMut.isPending || visitMoveWeekOnlyMut.isPending}
              data-testid="move-confirm-cancel"
            >
              キャンセル
            </Button>
            <Button
              type="button"
              onClick={handleConfirmMove}
              disabled={confirmMut.isPending || visitMoveWeekOnlyMut.isPending}
              data-testid="move-confirm-apply"
            >
              {confirmMut.isPending || visitMoveWeekOnlyMut.isPending ? (
                <>
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                  反映中…
                </>
              ) : (
                '反映する'
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
