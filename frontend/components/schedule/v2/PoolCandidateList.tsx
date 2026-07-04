'use client';

/**
 * PoolCandidateList — ③ 単体MVP (Phase G-102).
 *
 * 患者スケジュール詳細ダイアログのプール投入セクションに「他の空き枠（候補一覧）」を
 * 追加する. 既存の ``POST /v2/propose-slots``(= 実現可能スロットの複数候補ランキング;
 * 前方/後方移動制約・容量・昼休み・週コース実在・割付スタッフを考慮)を **この患者 1 人**
 * に対し on-demand で呼び、 ユーザーが候補を比較して 1 つ採用できるようにする.
 *
 * 採用は他曜日の固定枠を保持する fixed-visits マージ確定 (PUT /patients/{id}/fixed-visits)
 * で行う (= ProposeNewModal の既存患者確定と同じ安全な経路). 上段の diff-add「推奨提案」
 * とは併存し干渉しない.
 *
 * 実現可能性(移動+実働+バッファ・週コース実在・スタッフ稼働)は BE のソルバが担保するため、
 * 非現実な枠・未生成コース・休みスタッフ枠は候補に出ない (③ の狙い).
 */
import * as React from 'react';
import { useQueries } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { AlertTriangle, CheckCircle2, Lightbulb, Loader2, Plus, Sparkles, X } from 'lucide-react';
import { toast } from 'sonner';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

import { fetcher } from '@/lib/api/fetcher';
import { useProposeSlots, proposeWarningLabel } from '@/lib/queries/fieldBoard';
import { usePlaceAndFix } from '@/lib/queries/place_and_fix';
import { useConfirmFixedVisits } from '@/lib/queries/propose_confirm';
import { useFixedVisits, toastFixedVisitWarnings } from '@/lib/queries/patient_fixed_visits';
import { coerceWeeklyPattern, type PatientRead } from '@/lib/schemas/patient';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { PatientFixedVisitsBulkPut } from '@/lib/schemas/v2/patient_fixed_visit';
import type {
  ExcludedSummaryItem,
  ProposeMiniScheduleEntry,
  ProposeSlotItem,
  ProposeTimeType,
  WeekdayCode,
} from '@/lib/schemas/v2/propose_slots';

import {
  buildCourseTemplateIdResolver,
  buildWeekOnlyPlaceAndFixRequest,
  mergeAdoptedIntoNormalFixedVisits,
  proposedSlotToFixedVisitItem,
  slotKey,
} from './_proposeSlotUtils';
import { ChangeScopeChoice, type ChangeScopeValue } from './ChangeScopeChoice';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

/**
 * P-1b: 除外理由コードの日本語ラベル。
 * 未知コードは「その他の理由」として表示する (寛容パース規約)。
 * Stage P-2: PoolOverviewPane からも再利用するため export。
 */
export const EXCLUDED_REASON_LABEL: Record<string, string> = {
  capacity_full: 'コース容量が上限',
  lunch_window: '昼休みの時間帯',
  travel_shortage: '移動時間が確保できず',
  no_gap: '空き時間なし',
  course_closed: 'コースが存在しない',
  // I-11 (pair_mode 統合): 同住所ペアが「同時間帯NG」設定のため除外.
  pair_blocked: '同住所ペアの設定（同時間帯NG）',
};

function excludedReasonLabel(reason: string): string {
  return EXCLUDED_REASON_LABEL[reason] ?? 'その他の理由';
}

function trimSeconds(t: string | null | undefined): string {
  if (!t) return '';
  return t.length >= 5 ? t.slice(0, 5) : t;
}

/** 性別制限の名前色 (通常リスト WeekdayScheduleCard と同じ #dc2626 / #2563eb). */
function sexNameColor(sex: string | null | undefined): string | undefined {
  if (sex === 'female_only') return '#dc2626';
  if (sex === 'male_only') return '#2563eb';
  return undefined;
}

/** 性別制限・2名体制・同住所のマーカー群 (通常リストと同じ視覚言語). */
function RowMarkers({ row }: { row: ProposeMiniScheduleEntry }) {
  return (
    <>
      {row.is_pair ? (
        <span className="text-[10px] text-yellow-600" title="同住所ペア">
          📍
        </span>
      ) : null}
      {row.sex_restriction === 'female_only' ? (
        <span className="text-[10px]" style={{ color: '#dc2626' }}>
          👩女性のみ
        </span>
      ) : row.sex_restriction === 'male_only' ? (
        <span className="text-[10px]" style={{ color: '#2563eb' }}>
          👨男性のみ
        </span>
      ) : null}
      {row.is_multi_staff ? (
        <Badge variant="info" className="text-[9px]">
          複数
        </Badge>
      ) : null}
    </>
  );
}

/**
 * ミニスケジュール 1 行 (= そのコース当日の既存訪問 or 提案枠).
 * is_here の行は「ここに入れますか」と強調し、 コース全体の中での挿入位置を見せる
 * (ProposeNewModal の MiniRow と同じ視覚言語のコンパクト版).
 * 通常リストと同じ色分け (性別制限の名前色・複数バッジ・同住所📍) を付ける.
 */
function MiniRow({ row }: { row: ProposeMiniScheduleEntry }) {
  if (row.is_here) {
    return (
      <div className="flex items-center gap-2">
        <span className="tnum w-11 shrink-0 text-[11px] font-medium text-brand-primary">
          {trimSeconds(row.time)}
        </span>
        <div className="flex flex-1 flex-wrap items-center gap-1.5 rounded border border-dashed border-brand-primary bg-brand-primary/5 px-2 py-1">
          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-brand-primary text-white">
            <Plus className="h-2.5 w-2.5" aria-hidden />
          </span>
          <span className="text-[11px] font-semibold text-brand-primary">
            {row.is_pair ? 'ここに一緒に入れますか' : 'ここに入れますか'}
          </span>
          <RowMarkers row={row} />
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <span className="tnum w-11 shrink-0 text-[11px] text-text-muted">
        {trimSeconds(row.time)}
      </span>
      <div className="flex flex-1 flex-wrap items-center gap-1.5 rounded border-l-2 border-brand-primary/50 bg-bg-muted/50 px-2 py-1 text-[11px]">
        <span
          className={sexNameColor(row.sex_restriction) ? undefined : 'text-text-primary'}
          style={{
            color: sexNameColor(row.sex_restriction),
            fontWeight: row.sex_restriction ? 600 : undefined,
          }}
        >
          {row.name}
        </span>
        <RowMarkers row={row} />
      </div>
    </div>
  );
}

export interface PoolCandidateListProps {
  patient: PatientRead;
  isoYear: number;
  isoWeek: number;
  /** 一括ダイアログと同一の拠点スコープ. null = 全拠点. */
  officeId: string | null;
  /** 採用ボタンを出すか (RBAC; admin/manager のみ). */
  canEdit: boolean;
  /** 採用確定後に親へ通知 (PFV/visits の再取得は invalidate 済だが画面更新トリガに使う). */
  onAdopted?: () => void;
  /**
   * 主提案モード (単体プール詳細ダイアログ用). true のとき:
   *   - 開いた時点で自動的に propose-slots を実行する (ボタン押下不要).
   *   - 見出しを「空き枠の候補」にする (上位に別見出しがある前提).
   * 既定 false = 従来の on-demand 併設用途 (一括ダイアログ等で N 件同時 mount する
   * ケースのクエリバーストを避けるため、 こちらはボタン押下で初めて実行する).
   */
  primary?: boolean;
  /**
   * W-5b: 定員超過候補まで自動展開するか (BulkPoolInsertDialog done 画面からの直行専用).
   * primary=true かつ通常候補 0 件 + overcapacity_available_count>=1 のとき、
   * 「定員超過の候補を表示」ボタンと同等の再リクエスト (include_overcapacity=true) を
   * 自動実行し、超過候補一覧が開いた状態にする。自動実行は 1 回だけ (ref ガード)。
   */
  autoRequestOvercapacity?: boolean;
}

export function PoolCandidateList({
  patient,
  isoYear,
  isoWeek,
  officeId,
  canEdit,
  onAdopted,
  primary = false,
  autoRequestOvercapacity = false,
}: PoolCandidateListProps) {
  const { data: session, status: sessionStatus } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  // 候補一覧を要求したか (on-demand: ボタン押下で初めて propose-slots を回す).
  const [requested, setRequested] = React.useState(false);
  // 採用確認対象の候補. null = 未確認.
  const [pending, setPending] = React.useState<ProposeSlotItem | null>(null);
  // U-1: 反映先の選択 ('pattern' = A: 固定訪問週間に登録 / 'week' = B: この週だけ). 既定 A.
  const [scopeChoice, setScopeChoice] = React.useState<ChangeScopeValue>('pattern');
  // 方式b: 定員超過候補を表示するモードか (「定員超過の候補を表示」ボタンで切替)。
  const [overcapacityRequested, setOvercapacityRequested] = React.useState(false);
  // 方式b: 超過候補採用時の管理者判断理由 (確認モーダルの必須テキストエリア)。
  const [overcapacityReason, setOvercapacityReason] = React.useState('');

  const proposeMut = useProposeSlots();
  const confirmMut = useConfirmFixedVisits();
  const placeAndFixMut = usePlaceAndFix();
  // マージ確定用に既存 normal 固定枠を取得 (採用しなかった曜日を保持するため).
  const existingFixedVisitsQuery = useFixedVisits(patient.id, 'normal');

  const result = proposeMut.data;
  const slots = React.useMemo(() => result?.slots ?? [], [result]);
  // W-3: 通常候補 (希望適合) と効率優先の代替枠 (希望外) を分離。
  const normalSlots = React.useMemo(() => slots.filter((s) => !s.is_efficiency_alternative), [slots]);
  const efficiencySlots = React.useMemo(() => slots.filter((s) => s.is_efficiency_alternative), [slots]);
  // P-1b: 除外理由サマリ。旧BEは未送出のため nullish → [] フォールバック (寛容パース規約)。
  const excludedSummary = React.useMemo<ExcludedSummaryItem[]>(
    () => result?.excluded_summary ?? [],
    [result],
  );

  // 採用枠 → course_template_id 解決のため、 候補に出現する拠点の course-templates を取得.
  const slotOfficeIds = React.useMemo(() => {
    const s = new Set<string>();
    for (const sl of slots) s.add(sl.office_id);
    return Array.from(s).sort();
  }, [slots]);
  const templatesQueries = useQueries({
    queries: slotOfficeIds.map((oid) => ({
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

  const handleRun = React.useCallback(
    (includeOvercapacity = false) => {
      setRequested(true);
      setOvercapacityRequested(includeOvercapacity);
      setPending(null);
      setOvercapacityReason('');
      const wp = coerceWeeklyPattern(patient.weekly_pattern);
      const showTimeRange = wp.time_type === '固定' || wp.time_type === '時間帯';
      const showEnd = wp.time_type === '時間帯';
      proposeMut.mutate(
        {
          address: patient.address ?? '',
          lat: typeof patient.lat === 'number' ? patient.lat : null,
          lng: typeof patient.lng === 'number' ? patient.lng : null,
          service_minutes: wp.service_minutes,
          time_type: wp.time_type as ProposeTimeType,
          preferred_start: showTimeRange ? wp.preferred_start : null,
          preferred_end: showEnd ? wp.preferred_end : null,
          preferred_weekdays: wp.preferred_weekdays as WeekdayCode[],
          visit_frequency: wp.visit_frequency ?? undefined,
          frequency_per_week: wp.frequency_per_week,
          requires_multiple_staff:
            (patient as { requires_multiple_staff?: boolean | null }).requires_multiple_staff ===
            true,
          sex_restriction: (patient.sex_restriction as string | null | undefined) ?? null,
          iso_year: isoYear,
          iso_week: isoWeek,
          office_ids: officeId ? [officeId] : [],
          existing_patient_id: patient.id,
          limit: 10,
          // W-3: 効率優先の代替枠 (希望外だが近接/余裕が良い枠) を上乗せ提案する。
          include_efficiency_alternatives: true,
          include_overcapacity: includeOvercapacity,
        },
        { onError: () => toast.error('候補の取得に失敗しました') },
      );
    },
    [patient, isoYear, isoWeek, officeId, proposeMut],
  );

  // 主提案モード (単体プール詳細ダイアログ): 開いた時点 / 患者切替時に自動計算する.
  // 併設用途 (primary=false) は handleRun をボタンから呼ぶ従来の on-demand を維持.
  // autoFiredRef もリセットして、新患者/週/拠点では再度 auto-overcapacity を許可する.
  const autoFiredRef = React.useRef(false);
  React.useEffect(() => {
    if (primary) {
      autoFiredRef.current = false;
      handleRun();
    }
    // patient 切替・週変更・拠点変更でのみ再実行 (handleRun 同値依存は除外).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [primary, patient.id, isoYear, isoWeek, officeId]);

  // W-5b: autoRequestOvercapacity — 通常候補 0 件 + 超過候補あり なら自動展開 (1 回限り).
  // primary の自動 propose 結果が返ったあとにこの effect が動く (result 変化で起動).
  React.useEffect(() => {
    if (!autoRequestOvercapacity || !primary) return;
    if (autoFiredRef.current) return;
    if (!result) return;
    if (normalSlots.length === 0 && (result.overcapacity_available_count ?? 0) >= 1) {
      autoFiredRef.current = true;
      handleRun(true);
    }
    // result / normalSlots.length 変化でのみ起動; ref ガードで 1 回だけ実行.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, normalSlots.length]);

  // 選択候補 1 件を既存 normal 枠にマージ (採用曜日の slot_index=0 を置換, 他は保持).
  // 採用枠の item 化・マージは共有 _proposeSlotUtils (ProposeNewModal と共通) を使う.
  const buildMergedPut = React.useCallback(
    (existing: Parameters<typeof mergeAdoptedIntoNormalFixedVisits>[0], s: ProposeSlotItem) => {
      const wp = coerceWeeklyPattern(patient.weekly_pattern);
      const adoptedItem = proposedSlotToFixedVisitItem(
        s,
        resolveCourseTemplateId,
        wp.service_minutes,
      );
      return {
        mode: 'normal',
        items: mergeAdoptedIntoNormalFixedVisits(existing, [adoptedItem]),
      } satisfies PatientFixedVisitsBulkPut;
    },
    [patient.weekly_pattern, resolveCourseTemplateId],
  );

  const handleConfirmAdopt = () => {
    const slot = pending;
    if (!slot) return;
    // course_template_id 解決のための course-templates がまだ読み込み中なら待つ.
    if (templatesQueries.some((q) => q.isLoading)) {
      toast.warning('コース情報を読み込み中です。少し待ってからお試しください');
      return;
    }

    if (scopeChoice === 'pattern') {
      // ─── A 経路: PUT fixed-visits (型変更 + 今週即反映) ─────────────────────
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
      const existing = existingFixedVisitsQuery.data ?? [];
      const putBody: PatientFixedVisitsBulkPut = {
        ...buildMergedPut(existing, slot),
        change_scope: 'pattern_and_week',
        iso_year: isoYear,
        iso_week: isoWeek,
        ...(slot.overcapacity ? { capacity_override_reason: overcapacityReason } : {}),
      };
      confirmMut.mutate(
        { patientId: patient.id, body: putBody },
        {
          onSuccess: (data) => {
            // week_sync が無い = 今週への反映が行われなかった (旧 BE / 週再生成の失敗)。
            // 型には登録済みなので、正確に伝えて「週を生成」の再実行を促す。
            if (data?.week_sync == null) {
              toast.warning(
                `${patient.name} 様を固定訪問週間に登録しましたが、今週のスケジュールへの` +
                  `反映は行われませんでした。「週を生成」を再実行すると反映されます`,
              );
            } else {
              toast.success(
                `${patient.name} 様を採用しました。固定訪問週間に登録し、今週のスケジュールにも反映しました` +
                  (slot.overcapacity ? '（定員超過を管理者判断で許可）' : ''),
              );
            }
            // P0-2 Commit 3: 再検証 warnings があれば警告表示 (A 経路で従来どおり).
            toastFixedVisitWarnings(data?.warnings);
            // MVP 制約: 同住所ペア候補でも相方 (slot_index=1) は自動作成しない.
            if (slot.is_pair) {
              toast.warning(
                '同住所ペアの相方枠は自動作成されません。必要に応じて個別に追加してください',
              );
            }
            setPending(null);
            setScopeChoice('pattern');
            setOvercapacityReason('');
            setOvercapacityRequested(false);
            onAdopted?.();
          },
          onError: () => toast.error('採用に失敗しました'),
        },
      );
    } else {
      // ─── B 経路: place-and-fix (fix_pattern=false, 今週のみ) ─────────────────
      // Wave U-2: 共通ヘルパ (ProposeNewModal と共有) で week-only リクエストを構築.
      const wp = coerceWeeklyPattern(patient.weekly_pattern);
      const req = buildWeekOnlyPlaceAndFixRequest(slot, resolveCourseTemplateId, {
        patientId: patient.id,
        isoYear,
        isoWeek,
        serviceFallbackMin: wp.service_minutes,
        capacityOverrideReason: slot.overcapacity ? overcapacityReason : null,
      });
      if (req === null) {
        toast.error('コース情報を解決できませんでした。再読み込みしてお試しください');
        return;
      }
      placeAndFixMut.mutate(req, {
        onSuccess: () => {
          toast.success(
            `${patient.name} 様を今週だけ配置しました（毎週の型は変更していません）` +
              (slot.overcapacity ? '（定員超過を管理者判断で許可）' : ''),
          );
          setPending(null);
          setScopeChoice('pattern');
          setOvercapacityReason('');
          setOvercapacityRequested(false);
          onAdopted?.();
        },
        onError: () => toast.error('配置に失敗しました'),
      });
    }
  };

  const isBusy = proposeMut.isPending || confirmMut.isPending || placeAndFixMut.isPending;

  // ─── Render ───────────────────────────────────────────────────────
  // primary (主提案) は自動実行のためボタン待ち state を出さない. 併設 (on-demand) は
  // ボタン押下で初めて propose-slots を回す.
  if (!requested && !primary) {
    return (
      <div className="mt-2 border-t border-border-default/60 pt-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => handleRun()}
          className="h-7 px-3 text-xs"
          data-testid="pool-candidate-run-button"
        >
          <Sparkles className="mr-1 h-3.5 w-3.5" aria-hidden />
          他の空き枠も見る（候補一覧）
        </Button>
      </div>
    );
  }

  return (
    <div className="mt-2 border-t border-border-default/60 pt-2" data-testid="pool-candidate-list">
      <div className="mb-2 flex items-center gap-2">
        <h4 className="text-xs font-semibold text-text-primary">
          {primary ? '空き枠の候補' : '他の空き枠（候補一覧）'}
        </h4>
        {proposeMut.isPending ? (
          <span className="flex items-center gap-1 text-[11px] text-text-muted">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            算出中…
          </span>
        ) : (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => handleRun()}
            disabled={isBusy}
            className="ml-auto h-6 px-2 text-[11px]"
            data-testid="pool-candidate-refresh-button"
          >
            再計算
          </Button>
        )}
      </div>

      {proposeMut.isError ? (
        <Alert variant="destructive">
          <AlertTitle className="text-xs">候補の取得に失敗しました</AlertTitle>
          <AlertDescription className="text-xs">
            住所/緯度経度の設定や通信状況をご確認ください。
          </AlertDescription>
        </Alert>
      ) : null}

      {!proposeMut.isPending && result && normalSlots.length === 0 ? (
        <div className="py-3 text-xs text-text-muted" data-testid="pool-candidate-empty">
          {excludedSummary.length > 0 ? (
            /* P-1b: 除外理由別の内訳表示 (N-6「黙って消さない」). */
            <ul className="space-y-0.5" data-testid="pool-candidate-excluded-summary">
              {excludedSummary.map((item, i) => (
                <li key={i} className="flex items-center gap-1">
                  <span className="font-medium">{WEEKDAY_LABELS[item.weekday] ?? '?'}曜:</span>
                  <span>
                    {excludedReasonLabel(item.reason)} ({item.count}件)
                  </span>
                </li>
              ))}
            </ul>
          ) : efficiencySlots.length === 0 ? (
            /* 除外理由がない場合のフォールバック (効率代替もなければ表示). */
            <div className="text-center">
              実現可能な空き枠が見つかりませんでした。
              {result.message ? <div className="mt-1">{result.message}</div> : null}
            </div>
          ) : (
            /* 通常候補 0 件でも効率代替がある場合は行き止まりにしない誘導. */
            <div className="text-center">
              ご希望に合致する枠は見つかりませんでしたが、下の「効率優先の代替枠」をご確認ください。
            </div>
          )}
          {/* 方式b: 定員超過候補の呼びかけバナー (通常候補 0 件 + 定員超過で入れる枠がある場合). */}
          {!overcapacityRequested && (result?.overcapacity_available_count ?? 0) >= 1 ? (
            <div
              className="mt-2 rounded border border-yellow-400 bg-yellow-50/50 px-3 py-2 dark:border-yellow-600 dark:bg-yellow-900/20"
              data-testid="pool-overcapacity-callout"
            >
              <p className="text-xs font-semibold text-yellow-800 dark:text-yellow-300">
                <strong>
                  定員を +1 名許容すれば入る候補が {result?.overcapacity_available_count ?? 0}{' '}
                  件あります。
                </strong>
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => handleRun(true)}
                className="mt-1.5 h-7 border-yellow-500 px-3 text-xs text-yellow-700 hover:bg-yellow-100 dark:border-yellow-600 dark:text-yellow-400"
                data-testid="pool-overcapacity-show-button"
              >
                <AlertTriangle className="mr-1 h-3.5 w-3.5" aria-hidden />
                定員超過の候補を表示
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}

      {normalSlots.length > 0 ? (
        <ul className="space-y-1.5" data-testid="pool-candidate-slots">
          {normalSlots.map((s, i) => (
            <li
              key={`${slotKey(s)}-${i}`}
              className="rounded border border-border-default bg-bg-base p-2 text-xs"
              data-testid={`pool-candidate-${slotKey(s)}`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary" className="text-[10px]">
                  #{i + 1}
                </Badge>
                <span className="tnum font-medium text-text-primary">
                  {WEEKDAY_LABELS[s.weekday] ?? '?'} {trimSeconds(s.start_time)}–
                  {trimSeconds(s.end_time)}
                </span>
                <span className="text-text-secondary">{s.course_label}</span>
                {s.staff_name ? (
                  <span className="text-[11px] text-text-muted">担当: {s.staff_name}</span>
                ) : null}
                {s.is_pair && s.pair_partner ? (
                  <Badge variant="info" className="text-[10px]">
                    同住所ペア: {s.pair_partner}
                  </Badge>
                ) : null}
                {s.marginal_cost_minutes !== null && s.marginal_cost_minutes !== undefined ? (
                  /* P-1a: 挿入の厳密限界コスト (診断・改善提案と同じ物差し). */
                  <Badge
                    variant="secondary"
                    className="text-[10px]"
                    data-testid="pool-candidate-delta-badge"
                    title="診断・改善提案と同じ物差し（厳密限界コスト: コース全体の移動増分）"
                  >
                    コースの移動{' '}
                    {Math.round(s.marginal_cost_minutes) <= 0
                      ? '±0分'
                      : `+${Math.round(s.marginal_cost_minutes)}分`}
                  </Badge>
                ) : null}
                <span className="tnum ml-auto text-[10px] text-text-muted">
                  スコア {s.score.toFixed(0)}
                </span>
              </div>

              {s.reasons.length > 0 ? (
                <div className="mt-1 text-[11px] text-text-muted">{s.reasons.join(' / ')}</div>
              ) : null}

              {s.warnings.length > 0 ? (
                <div className="mt-1 flex flex-wrap gap-1">
                  {s.warnings.map((w, wi) => (
                    <Badge key={wi} variant="warning" className="text-[10px]">
                      {proposeWarningLabel(w)}
                    </Badge>
                  ))}
                </div>
              ) : null}

              {/* このコース当日の全体スケジュール + 「ここに入れますか」挿入位置. */}
              {s.mini_schedule.length > 0 ? (
                <div
                  className="mt-1.5 rounded border border-border-default bg-bg-muted/20 p-2"
                  data-testid={`pool-candidate-mini-${slotKey(s)}`}
                >
                  <div className="mb-1 text-[10px] font-semibold text-text-muted">
                    {s.course_label}
                    {s.staff_name ? `（${s.staff_name}）` : ''} の{' '}
                    {WEEKDAY_LABELS[s.weekday] ?? '?'}曜 ・ 既存{' '}
                    {s.mini_schedule.filter((m) => !m.is_here).length} 件 + 提案枠
                  </div>
                  <div className="flex flex-col gap-1">
                    {s.mini_schedule.map((row, ri) => (
                      <MiniRow key={ri} row={row} />
                    ))}
                  </div>
                </div>
              ) : null}

              {canEdit ? (
                <div className="mt-1.5 flex items-center justify-end">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => setPending(s)}
                    disabled={isBusy}
                    className="h-6 px-2 text-[11px]"
                    data-testid={`pool-candidate-adopt-${slotKey(s)}`}
                  >
                    この枠で採用
                  </Button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {/* W-3: 効率優先の代替枠 (希望外だが近接/余裕が良い枠). 既定閉じた折りたたみ. */}
      {efficiencySlots.length > 0 ? (
        <details
          className="mt-2 rounded border border-brand-primary/30 text-xs"
          data-testid="pool-efficiency-section"
        >
          <summary className="flex cursor-pointer select-none items-center gap-1.5 rounded px-2 py-1.5 text-xs font-semibold text-text-secondary hover:bg-bg-muted">
            <Lightbulb className="h-3.5 w-3.5 text-brand-primary" aria-hidden />
            効率優先の代替枠（ご希望とは異なります）
            <Badge variant="secondary" className="ml-1 text-[10px]" data-testid="pool-efficiency-count">
              {efficiencySlots.length}件
            </Badge>
          </summary>
          <div className="rounded-b border-t border-brand-primary/20 bg-brand-primary/5 p-2">
            <p className="mb-1.5 text-[11px] text-text-muted">
              ご希望の曜日・時間帯とは異なりますが、移動効率や空きの余裕がより良い枠です。
            </p>
            <ul className="space-y-1.5" data-testid="pool-efficiency-slot-list">
              {efficiencySlots.map((s, i) => (
                <li
                  key={`eff-${slotKey(s)}-${i}`}
                  className="rounded border border-border-default bg-bg-base p-2"
                  data-testid={`pool-efficiency-${slotKey(s)}`}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="secondary" className="text-[10px]">
                      #{normalSlots.length + i + 1}
                    </Badge>
                    <span className="tnum font-medium text-text-primary">
                      {WEEKDAY_LABELS[s.weekday] ?? '?'} {trimSeconds(s.start_time)}–
                      {trimSeconds(s.end_time)}
                    </span>
                    <span className="text-text-secondary">{s.course_label}</span>
                    {s.staff_name ? (
                      <span className="text-[11px] text-text-muted">担当: {s.staff_name}</span>
                    ) : null}
                    {s.is_pair && s.pair_partner ? (
                      <Badge variant="info" className="text-[10px]">
                        同住所ペア: {s.pair_partner}
                      </Badge>
                    ) : null}
                    {s.marginal_cost_minutes !== null && s.marginal_cost_minutes !== undefined ? (
                      <Badge
                        variant="secondary"
                        className="text-[10px]"
                        title="診断・改善提案と同じ物差し（厳密限界コスト: コース全体の移動増分）"
                      >
                        コースの移動{' '}
                        {Math.round(s.marginal_cost_minutes) <= 0
                          ? '±0分'
                          : `+${Math.round(s.marginal_cost_minutes)}分`}
                      </Badge>
                    ) : null}
                    <span className="tnum ml-auto text-[10px] text-text-muted">
                      スコア {s.score.toFixed(0)}
                    </span>
                  </div>

                  {s.reasons.length > 0 ? (
                    <div className="mt-1 text-[11px] text-text-muted">{s.reasons.join(' / ')}</div>
                  ) : null}

                  {s.warnings.length > 0 ? (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {s.warnings.map((w, wi) => (
                        <Badge key={wi} variant="warning" className="text-[10px]">
                          {proposeWarningLabel(w)}
                        </Badge>
                      ))}
                    </div>
                  ) : null}

                  {s.mini_schedule.length > 0 ? (
                    <div
                      className="mt-1.5 rounded border border-border-default bg-bg-muted/20 p-2"
                    >
                      <div className="mb-1 text-[10px] font-semibold text-text-muted">
                        {s.course_label}
                        {s.staff_name ? `（${s.staff_name}）` : ''} の{' '}
                        {WEEKDAY_LABELS[s.weekday] ?? '?'}曜 ・ 既存{' '}
                        {s.mini_schedule.filter((m) => !m.is_here).length} 件 + 提案枠
                      </div>
                      <div className="flex flex-col gap-1">
                        {s.mini_schedule.map((row, ri) => (
                          <MiniRow key={ri} row={row} />
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {canEdit ? (
                    <div className="mt-1.5 flex items-center justify-end">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => setPending(s)}
                        disabled={isBusy}
                        className="h-6 px-2 text-[11px]"
                        data-testid={`pool-efficiency-adopt-${slotKey(s)}`}
                      >
                        この枠で採用
                      </Button>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        </details>
      ) : null}

      {/* 方式b: 定員超過候補セクション (「定員超過の候補を表示」クリック後に表示). */}
      {overcapacityRequested && (result?.overcapacity_slots ?? []).length > 0 ? (
        <div className="mt-2" data-testid="pool-overcapacity-section">
          <div className="mb-1.5 flex items-center gap-1 text-xs font-semibold text-yellow-700 dark:text-yellow-400">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
            定員超過（管理者判断）
          </div>
          <ul className="space-y-1.5">
            {(result!.overcapacity_slots ?? []).map((s, i) => (
              <li
                key={`oc-${slotKey(s)}-${i}`}
                className="rounded border border-yellow-400 bg-yellow-50/30 p-2 text-xs dark:border-yellow-600 dark:bg-yellow-900/10"
                data-testid={`pool-overcapacity-${slotKey(s)}`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary" className="text-[10px]">
                    #{i + 1}
                  </Badge>
                  <span className="tnum font-medium text-text-primary">
                    {WEEKDAY_LABELS[s.weekday] ?? '?'} {trimSeconds(s.start_time)}–
                    {trimSeconds(s.end_time)}
                  </span>
                  <span className="text-text-secondary">{s.course_label}</span>
                  {s.staff_name ? (
                    <span className="text-[11px] text-text-muted">担当: {s.staff_name}</span>
                  ) : null}
                  {/* 方式b: 定員超過警告バッジ */}
                  <Badge
                    variant="warning"
                    className="text-[10px]"
                    data-testid="pool-overcapacity-badge"
                    title="このコースの定員(通常上限)を1名超えます。採用には理由の記録が必要です"
                  >
                    定員超過 +1名
                  </Badge>
                  {s.is_pair && s.pair_partner ? (
                    <Badge variant="info" className="text-[10px]">
                      同住所ペア: {s.pair_partner}
                    </Badge>
                  ) : null}
                  {s.marginal_cost_minutes !== null && s.marginal_cost_minutes !== undefined ? (
                    <Badge
                      variant="secondary"
                      className="text-[10px]"
                      title="診断・改善提案と同じ物差し（厳密限界コスト: コース全体の移動増分）"
                    >
                      コースの移動{' '}
                      {Math.round(s.marginal_cost_minutes) <= 0
                        ? '±0分'
                        : `+${Math.round(s.marginal_cost_minutes)}分`}
                    </Badge>
                  ) : null}
                  <span className="tnum ml-auto text-[10px] text-text-muted">
                    スコア {s.score.toFixed(0)}
                  </span>
                </div>

                {s.reasons.length > 0 ? (
                  <div className="mt-1 text-[11px] text-text-muted">{s.reasons.join(' / ')}</div>
                ) : null}

                {s.warnings.length > 0 ? (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {s.warnings.map((w, wi) => (
                      <Badge key={wi} variant="warning" className="text-[10px]">
                        {proposeWarningLabel(w)}
                      </Badge>
                    ))}
                  </div>
                ) : null}

                {s.mini_schedule.length > 0 ? (
                  <div
                    className="mt-1.5 rounded border border-border-default bg-bg-muted/20 p-2"
                    data-testid={`pool-candidate-mini-${slotKey(s)}`}
                  >
                    <div className="mb-1 text-[10px] font-semibold text-text-muted">
                      {s.course_label}
                      {s.staff_name ? `（${s.staff_name}）` : ''} の{' '}
                      {WEEKDAY_LABELS[s.weekday] ?? '?'}曜 ・ 既存{' '}
                      {s.mini_schedule.filter((m) => !m.is_here).length} 件 + 提案枠
                    </div>
                    <div className="flex flex-col gap-1">
                      {s.mini_schedule.map((row, ri) => (
                        <MiniRow key={ri} row={row} />
                      ))}
                    </div>
                  </div>
                ) : null}

                {canEdit ? (
                  <div className="mt-1.5 flex items-center justify-end">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => setPending(s)}
                      disabled={isBusy}
                      className="h-6 px-2 text-[11px]"
                      data-testid={`pool-overcapacity-adopt-${slotKey(s)}`}
                    >
                      この枠で採用
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* 採用確認 (インライン). 他曜日を保持するマージ確定. U-1: A/B 選択付き. */}
      {pending ? (
        <div
          className="mt-2 rounded border border-brand-primary/40 bg-brand-primary/5 p-2 text-xs"
          data-testid="pool-candidate-confirm"
        >
          <div className="text-text-primary">
            <span className="font-medium">{patient.name} 様</span> を{' '}
            <span className="tnum font-medium">
              {WEEKDAY_LABELS[pending.weekday] ?? '?'} {trimSeconds(pending.start_time)}{' '}
              {pending.course_label}
            </span>{' '}
            に追加します。反映先を選んでください（他曜日の固定枠は維持されます）。
          </div>
          <div className="mt-2">
            <ChangeScopeChoice
              value={scopeChoice}
              onChange={setScopeChoice}
              disabled={confirmMut.isPending || placeAndFixMut.isPending}
            />
          </div>
          {/* 方式b: 定員超過候補の採用理由入力欄 (通常候補では出さない). */}
          {pending.overcapacity ? (
            <div className="mt-2">
              <label className="mb-1 block text-xs font-semibold text-yellow-700 dark:text-yellow-400">
                定員超過の採用理由（必須）
              </label>
              <textarea
                className="w-full resize-none rounded border border-yellow-400 p-1.5 text-xs dark:border-yellow-600"
                rows={2}
                maxLength={500}
                placeholder="例: ◯◯様の受け入れ希望が強く、スタッフ稼働に余裕があるため"
                value={overcapacityReason}
                onChange={(e) => setOvercapacityReason(e.target.value)}
                disabled={confirmMut.isPending || placeAndFixMut.isPending}
                data-testid="pool-overcapacity-reason-input"
              />
            </div>
          ) : null}
          <div className="mt-2 flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                setPending(null);
                setScopeChoice('pattern');
                setOvercapacityReason('');
              }}
              disabled={confirmMut.isPending || placeAndFixMut.isPending}
              className="h-7 px-3 text-xs"
              data-testid="pool-candidate-confirm-cancel"
            >
              <X className="mr-1 h-3.5 w-3.5" aria-hidden />
              やめる
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={handleConfirmAdopt}
              disabled={
                confirmMut.isPending ||
                placeAndFixMut.isPending ||
                (pending.overcapacity === true && !overcapacityReason.trim())
              }
              className="h-7 px-3 text-xs"
              data-testid="pool-candidate-confirm-apply"
            >
              {confirmMut.isPending || placeAndFixMut.isPending ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <CheckCircle2 className="mr-1 h-3.5 w-3.5" aria-hidden />
              )}
              この枠で確定
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
