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
import { CheckCircle2, Loader2, Plus, Sparkles, X } from 'lucide-react';
import { toast } from 'sonner';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

import { fetcher } from '@/lib/api/fetcher';
import { useProposeSlots, proposeWarningLabel } from '@/lib/queries/fieldBoard';
import { useConfirmFixedVisits } from '@/lib/queries/propose_confirm';
import { useFixedVisits } from '@/lib/queries/patient_fixed_visits';
import { coerceWeeklyPattern, type PatientRead } from '@/lib/schemas/patient';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type {
  PatientFixedVisitV2Base,
  PatientFixedVisitV2Read,
  PatientFixedVisitsBulkPut,
} from '@/lib/schemas/v2/patient_fixed_visit';
import type {
  ProposeMiniScheduleEntry,
  ProposeSlotItem,
  ProposeTimeType,
  WeekdayCode,
} from '@/lib/schemas/v2/propose_slots';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

function trimSeconds(t: string | null | undefined): string {
  if (!t) return '';
  return t.length >= 5 ? t.slice(0, 5) : t;
}

/** 枠の start_time〜end_time から所要分を算出. パース不能なら null. */
function slotDurationMin(s: ProposeSlotItem): number | null {
  const parse = (hm: string | null | undefined): number | null => {
    if (!hm) return null;
    const m = /^(\d{1,2}):(\d{2})/.exec(hm);
    if (!m) return null;
    return Number(m[1]) * 60 + Number(m[2]);
  };
  const a = parse(s.start_time);
  const b = parse(s.end_time);
  if (a === null || b === null || b <= a) return null;
  return b - a;
}

const slotKey = (s: ProposeSlotItem): string =>
  `${s.office_id}-${s.weekday}-${s.course_code}-${s.start_time}`;

/**
 * ミニスケジュール 1 行 (= そのコース当日の既存訪問 or 提案枠).
 * is_here の行は「ここに入れますか」と強調し、 コース全体の中での挿入位置を見せる
 * (ProposeNewModal の MiniRow と同じ視覚言語のコンパクト版).
 */
function MiniRow({ row }: { row: ProposeMiniScheduleEntry }) {
  if (row.is_here) {
    return (
      <div className="flex items-center gap-2">
        <span className="tnum w-11 shrink-0 text-[11px] font-medium text-brand-primary">
          {trimSeconds(row.time)}
        </span>
        <div className="flex flex-1 items-center gap-1.5 rounded border border-dashed border-brand-primary bg-brand-primary/5 px-2 py-1">
          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-brand-primary text-white">
            <Plus className="h-2.5 w-2.5" aria-hidden />
          </span>
          <span className="text-[11px] font-semibold text-brand-primary">
            {row.is_pair ? 'ここに一緒に入れますか' : 'ここに入れますか'}
          </span>
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <span className="tnum w-11 shrink-0 text-[11px] text-text-muted">
        {trimSeconds(row.time)}
      </span>
      <div className="flex-1 rounded border-l-2 border-brand-primary/50 bg-bg-muted/50 px-2 py-1 text-[11px] text-text-primary">
        {row.name}
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
}

export function PoolCandidateList({
  patient,
  isoYear,
  isoWeek,
  officeId,
  canEdit,
  onAdopted,
}: PoolCandidateListProps) {
  const { data: session, status: sessionStatus } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  // 候補一覧を要求したか (on-demand: ボタン押下で初めて propose-slots を回す).
  const [requested, setRequested] = React.useState(false);
  // 採用確認対象の候補. null = 未確認.
  const [pending, setPending] = React.useState<ProposeSlotItem | null>(null);

  const proposeMut = useProposeSlots();
  const confirmMut = useConfirmFixedVisits();
  // マージ確定用に既存 normal 固定枠を取得 (採用しなかった曜日を保持するため).
  const existingFixedVisitsQuery = useFixedVisits(patient.id, 'normal');

  const result = proposeMut.data;
  const slots = React.useMemo(() => result?.slots ?? [], [result]);

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
  const resolveCourseTemplateId = React.useMemo(() => {
    const m = new Map<string, string>();
    for (const q of templatesQueries) {
      for (const t of q.data ?? []) {
        if (t.deleted_at) continue;
        m.set(`${t.office_id}:${(t.label ?? '').trim().toUpperCase()}`, t.id);
      }
    }
    return (oid: string, code: string): string | null =>
      m.get(`${oid}:${code.trim().toUpperCase()}`) ?? null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templatesDepKey]);

  const handleRun = React.useCallback(() => {
    setRequested(true);
    setPending(null);
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
      },
      { onError: () => toast.error('候補の取得に失敗しました') },
    );
  }, [patient, isoYear, isoWeek, officeId, proposeMut]);

  // 採用枠 1 件 → bulk PUT item (mode='normal').
  const adoptedToItem = React.useCallback(
    (s: ProposeSlotItem): PatientFixedVisitV2Base => {
      const wp = coerceWeeklyPattern(patient.weekly_pattern);
      const duration = slotDurationMin(s) ?? wp.service_minutes;
      const tplId = resolveCourseTemplateId(s.office_id, s.course_code);
      return {
        weekday: s.weekday,
        start_time: s.start_time,
        duration_min: duration,
        slot_index: 0,
        ...(tplId ? { course_template_id: tplId } : {}),
      };
    },
    [patient.weekly_pattern, resolveCourseTemplateId],
  );

  const existingToItem = (v: PatientFixedVisitV2Read): PatientFixedVisitV2Base => ({
    weekday: v.weekday,
    start_time: v.start_time,
    duration_min: v.duration_min,
    slot_index: v.slot_index ?? 0,
    ...(v.course_template_id ? { course_template_id: v.course_template_id } : {}),
    ...(v.sub_office_id ? { sub_office_id: v.sub_office_id } : {}),
    ...(typeof v.is_pinned === 'boolean' ? { is_pinned: v.is_pinned } : {}),
  });

  // 選択候補 1 件を既存 normal 枠にマージ (採用曜日の slot_index=0 を置換, 他は保持).
  const buildMergedPut = (
    existing: PatientFixedVisitV2Read[],
    s: ProposeSlotItem,
  ): PatientFixedVisitsBulkPut => {
    const preserved = existing
      .filter((v) => v.weekday !== s.weekday || (v.slot_index ?? 0) !== 0)
      .map(existingToItem);
    const items = [...preserved, adoptedToItem(s)].sort(
      (a, b) => a.weekday - b.weekday || a.start_time.localeCompare(b.start_time),
    );
    return { mode: 'normal', items };
  };

  const handleConfirmAdopt = () => {
    const slot = pending;
    if (!slot) return;
    if (existingFixedVisitsQuery.isLoading || existingFixedVisitsQuery.isFetching) {
      toast.warning('既存の固定枠を読み込み中です。少し待ってからお試しください');
      return;
    }
    if (existingFixedVisitsQuery.isError) {
      toast.error('既存の固定枠の読み込みに失敗しました。他曜日の枠を保護できないため中止しました');
      return;
    }
    // course_template_id 解決のための course-templates がまだ読み込み中なら待つ
    // (未解決のまま確定すると course 紐付かない PFV になり配置がずれるため).
    if (templatesQueries.some((q) => q.isLoading)) {
      toast.warning('コース情報を読み込み中です。少し待ってからお試しください');
      return;
    }
    const existing = existingFixedVisitsQuery.data ?? [];
    confirmMut.mutate(
      { patientId: patient.id, body: buildMergedPut(existing, slot) },
      {
        onSuccess: () => {
          toast.success(
            `${patient.name} 様を ${WEEKDAY_LABELS[slot.weekday] ?? '?'} ` +
              `${trimSeconds(slot.start_time)} ${slot.course_label} に採用しました（他曜日は維持）`,
          );
          // MVP 制約: 同住所ペア候補でも相方 (slot_index=1) は自動作成しない.
          if (slot.is_pair) {
            toast.warning(
              '同住所ペアの相方枠は自動作成されません。必要に応じて個別に追加してください',
            );
          }
          setPending(null);
          onAdopted?.();
        },
        onError: () => toast.error('採用に失敗しました'),
      },
    );
  };

  const isBusy = proposeMut.isPending || confirmMut.isPending;

  // ─── Render ───────────────────────────────────────────────────────
  if (!requested) {
    return (
      <div className="mt-2 border-t border-border-default/60 pt-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={handleRun}
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
        <h4 className="text-xs font-semibold text-text-primary">他の空き枠（候補一覧）</h4>
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
            onClick={handleRun}
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

      {!proposeMut.isPending && result && slots.length === 0 ? (
        <div
          className="py-3 text-center text-xs text-text-muted"
          data-testid="pool-candidate-empty"
        >
          実現可能な空き枠が見つかりませんでした。
          {result.message ? <div className="mt-1">{result.message}</div> : null}
        </div>
      ) : null}

      {slots.length > 0 ? (
        <ul className="space-y-1.5" data-testid="pool-candidate-slots">
          {slots.map((s, i) => (
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

      {/* 採用確認 (インライン). 他曜日を保持するマージ確定. */}
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
            に固定枠として追加します（他曜日の固定枠は維持されます）。
          </div>
          <div className="mt-2 flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setPending(null)}
              disabled={confirmMut.isPending}
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
              disabled={confirmMut.isPending}
              className="h-7 px-3 text-xs"
              data-testid="pool-candidate-confirm-apply"
            >
              {confirmMut.isPending ? (
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
