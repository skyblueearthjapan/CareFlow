'use client';

/**
 * SpecialVisitPlaceLauncher — 特別訪問週間の ○ を「その日の配置先を決める」ための
 * `AddVisitAnywhereDialog` ラッパ。
 *
 * 正典 = `docs/plans/special-visit-week-ux-investigation-2026-09-07.md` §3-2 2。
 *
 * 盤面 (`CourseDayTablePanel`) の外 = 患者画面からモーダルを開くため、盤面が
 * 持っていた材料 (患者 / コーステンプレート / 拠点 / 職員 / 提案 / 週の訪問) を
 * ここで揃える。**配線は盤面と同じ**で、違うのは 3 点だけ:
 *   ① 患者・日付・反映先 (新しく 1 件追加) を固定して開く
 *   ② 登録できたら、その訪問 id で `POST /special-visit-marks/{id}/place`
 *      (`visit_id` モード = 訪問は作らずマークを紐付けるだけ) を呼び、
 *      ○ を ● にする
 *   ③ ● の付け替え (`replacingMarkId`) は **新しい訪問ができてから**
 *      いまの配置を取り消す (先に壊さない = 途中で失敗しても予定が消えない)
 *
 * M 受け皿・段階的緩和 (サブ担当拠点) は `AddVisitAnywhereDialog` の提案ロジック
 * をそのまま使うので、ここには判定を書かない。
 */
import * as React from 'react';
import { useQueries, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { toast } from 'sonner';

import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';
import { useOffices } from '@/lib/queries/offices';
import { usePatient } from '@/lib/queries/patients';
import { useProposeSlots } from '@/lib/queries/fieldBoard';
import { usePlaceAndFix } from '@/lib/queries/place_and_fix';
import { useStaffList } from '@/lib/queries/staff';
import {
  useCreateSpecialVisitMark,
  useDeleteSpecialVisitMark,
  usePlaceSpecialMark,
} from '@/lib/queries/specialVisitWeek';
import { useCreateVisit } from '@/lib/queries/visits';
import {
  coerceWeeklyPattern,
  formatPreferredTimeLabel,
  normalizePatientSexRestriction,
} from '@/lib/schemas/patient';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { PatientFixedVisitV2Read } from '@/lib/schemas/v2/patient_fixed_visit';
import type { VisitRead } from '@/lib/schemas/visit';
import { mondayOfIsoWeek } from '@/lib/format/isoWeek';
import { executeAddVisitPlan } from '@/lib/scheduling/addVisitExecutor';
import type { AddVisitPlan, VisitLite } from '@/lib/scheduling/addVisitPlan';
import { ConstraintOverrideConfirmDialog } from './ConstraintOverrideConfirmDialog';
import { useConstraintConfirmRetry } from './useConstraintConfirmRetry';
import {
  AddVisitAnywhereDialog,
  type AddVisitPatientOption,
} from './cockpit/AddVisitAnywhereDialog';

/** 保留プールの印はこの導線では使わない (患者は 1 人に固定されている)。 */
const NO_POOL_IDS: ReadonlySet<string> = new Set<string>();

/** 特別枠の配置は「新しく 1 件追加」だけなので、他の反映先の実行系は使わない。 */
const UNUSED_SCOPE_ERROR = 'この導線では新しく 1 件追加する以外の反映先は使いません';

/** NG スタッフ / 性別制限 (422) の確認文言。盤面の配置と同じ動詞に揃える。 */
const PLACE_CONSTRAINT_TEXT = {
  title: 'それでも配置しますか？',
  description: 'この配置先の担当者は、次の制約に抵触します',
  confirmLabel: '配置する',
} as const;

/**
 * 422 を確認ダイアログへ回した時に ＋訪問モーダルへ返す文言。
 * モーダルは開いたまま (確認ダイアログがその上に乗る)。
 */
const CONSTRAINT_PENDING_MESSAGE = '担当者の制約を確認してください（確認画面を開きました）';

/**
 * 入れ替えで同じ日・同じ時刻を選んだとき。旧訪問が生きたまま新しい訪問を作るため
 * (patient, date, start_time) の部分ユニーク制約に当たる (BE は 409/500)。
 */
const SAME_START_TIME_MESSAGE =
  '同じ時刻のままでは入れ替えられません。時刻を変えるか、いったん「配置を取り消す」で消してから付け直してください';

/** 入れ替え中の重複エラー (BE は 409、DB まで届くと 500)。 */
function isDuplicateSlotError(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 409 || err.status === 500);
}

/** JST の「今日」(BE の過去日ガードと同じ Asia/Tokyo 基準・盤面と同じ式)。 */
export function todayIsoJst(): string {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Tokyo' }).format(new Date());
}

/** "2026-09-14" → "9/14"。トーストの文言用。 */
function formatMd(dateIso: string): string {
  const m = dateIso.match(/^\d{4}-(\d{2})-(\d{2})$/);
  if (!m) return dateIso;
  return `${Number(m[1])}/${Number(m[2])}`;
}

export interface SpecialVisitPlaceLauncherProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 配置する患者 (期間の患者)。 */
  patientId: string;
  /** 期間 id (付け替えで追加枠を作り直すのに使う)。 */
  periodId: string;
  /**
   * 紐付ける未配置の追加枠 (○)。
   * ● の付け替え (`replacingMarkId` あり) では作り直すので null。
   */
  markId: string | null;
  /**
   * 付け替える配置済みマーク (●)。**新しい訪問ができてから** force 削除し、
   * 同じ日に追加枠を作り直して紐付ける。
   */
  replacingMarkId?: string | null;
  /** 付け替え対象の配置済み訪問 (途中で失敗したときの状態説明に使う)。 */
  replacingVisitId?: string | null;
  /**
   * 付け替え対象の開始時刻 ("HH:MM")。入れ替えは **古い訪問が生きたまま**
   * 新しい訪問を作るので、同じ時刻だと (patient, date, start_time) の
   * 部分ユニーク制約に当たる。同時刻は実行前に止める。
   */
  replacingStartHM?: string | null;
  /** マークの日付 (YYYY-MM-DD)。モーダルの日付はこれに固定する。 */
  date: string;
  /** マークの ISO 週 / 曜日 (カレンダーの初期表示・提案の対象週・作り直し)。 */
  isoYear: number;
  isoWeek: number;
  weekday: number;
  /** 配置できたとき (呼出元がマークの状態を閉じる)。 */
  onPlaced?: () => void;
}

export function SpecialVisitPlaceLauncher({
  open,
  onOpenChange,
  patientId,
  periodId,
  markId,
  replacingMarkId = null,
  replacingVisitId = null,
  replacingStartHM = null,
  date,
  isoYear,
  isoWeek,
  weekday,
  onPlaced,
}: SpecialVisitPlaceLauncherProps) {
  const { data: session, status: sessionStatus } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  const patientQuery = usePatient(open ? patientId : null);
  const { offices } = useOffices();
  const staffQuery = useStaffList({ limit: 200 });
  const proposeSlotsMut = useProposeSlots();
  const placeAndFixMut = usePlaceAndFix();
  const createVisitMut = useCreateVisit();
  const placeMarkMut = usePlaceSpecialMark();
  const deleteMarkMut = useDeleteSpecialVisitMark();
  const createMarkMut = useCreateSpecialVisitMark();
  /** NG スタッフ / 性別制限の 422 は確認してから acknowledge 再送する (盤面と同じ)。 */
  const constraintConfirm = useConstraintConfirmRetry();
  // 途中で失敗したときは mutation の onSuccess が走らないので、ここで失効させる。
  const queryClient = useQueryClient();

  // 拠点ごとのコーステンプレート (盤面と同じ並列 fetch)。提案の候補は他拠点にも
  // 出るため、全拠点ぶん引いておく。
  const officeIds = React.useMemo(() => offices.map((o) => o.id), [offices]);
  const templatesQueries = useQueries({
    queries: officeIds.map((oid) => ({
      queryKey: ['course-templates', 'list', oid],
      enabled: open && sessionStatus === 'authenticated' && Boolean(oid),
      queryFn: () =>
        fetcher<CourseTemplateRead[]>(
          `/api/v1/course-templates?office_id=${encodeURIComponent(oid)}`,
          { accessToken, refreshToken },
        ),
    })),
  });
  // `useQueries` は毎レンダー新しい配列を返すので、状態の識別子を deps にする
  // (盤面 `CourseDayTablePanel` と同じ回避策)。
  const templatesDepKey = templatesQueries.map((q) => `${q.dataUpdatedAt}:${q.status}`).join(',');
  const courseTemplates = React.useMemo(
    () =>
      templatesQueries
        .flatMap((q) => q.data ?? [])
        .filter((t) => !t.deleted_at)
        .map((t) => ({ id: t.id, label: t.label, office_id: t.office_id })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [templatesDepKey],
  );

  const patients = React.useMemo<AddVisitPatientOption[]>(() => {
    const p = patientQuery.data;
    if (!p) return [];
    const wp = coerceWeeklyPattern(p.weekly_pattern);
    return [
      {
        id: p.id,
        name: p.name,
        // モーダルは active だけを候補に出す。この導線は患者が決まっているので
        // 状態にかかわらず選べるようにする (特別訪問週間は active 患者のみ作れる)。
        status: 'active',
        primary_office_id: p.primary_office_id ?? null,
        lat: p.lat ?? null,
        lng: p.lng ?? null,
        service_minutes: wp.service_minutes ?? null,
        hint: formatPreferredTimeLabel(wp) || null,
        sex_restriction: normalizePatientSexRestriction(p.sex_restriction),
        requires_multiple_staff:
          (p as { requires_multiple_staff?: boolean | null }).requires_multiple_staff === true,
      },
    ];
  }, [patientQuery.data]);

  const staffOptions = React.useMemo(
    () => (staffQuery.data ?? []).map((s) => ({ id: s.id, name: s.name })),
    [staffQuery.data],
  );

  /**
   * (b)「その週を変える」用の候補。反映先は 'new' 固定なのでモーダルからは
   * 呼ばれないが、契約上必須なので盤面と同じ読み方で用意する
   * (コース名は盤面のテンプレート対応表が要るためここでは付けない)。
   */
  const loadPatientWeekVisits = React.useCallback(
    async (
      targetPatientId: string,
      targetIsoYear: number,
      targetIsoWeek: number,
    ): Promise<VisitLite[]> => {
      const monday = mondayOfIsoWeek(targetIsoYear, targetIsoWeek);
      const isoOf = (offset: number) => {
        const d = new Date(monday);
        d.setUTCDate(monday.getUTCDate() + offset);
        return d.toISOString().slice(0, 10);
      };
      const qs = new URLSearchParams({
        limit: '500',
        offset: '0',
        week_start: isoOf(0),
        week_end: isoOf(6),
        patient_id: targetPatientId,
      });
      const items = await fetcher<VisitRead[]>(`/api/v1/visits?${qs.toString()}`, {
        accessToken,
        refreshToken,
      });
      return (items ?? []).map((v) => ({
        id: v.id,
        visit_date: v.visit_date,
        start_time: (v.start_time ?? '').slice(0, 5),
        end_time: (v.end_time ?? '').slice(0, 5),
        primary_staff_id: v.primary_staff_id ?? null,
        staff_name: null,
        course_id: v.course_id ?? null,
        course_label: null,
        week_pinned: v.week_pinned === true,
        status: v.status ?? 'planned',
        source: v.source ?? 'auto',
      }));
    },
    [accessToken, refreshToken],
  );

  /** 段階的緩和 2 段目の対象拠点 (盤面と同じ = 通常の固定訪問行の sub_office_id)。 */
  const loadPatientSubOfficeIds = React.useCallback(
    async (targetPatientId: string): Promise<string[]> => {
      const rows = await fetcher<PatientFixedVisitV2Read[]>(
        `/api/v1/patients/${targetPatientId}/fixed-visits?mode=normal`,
        { accessToken, refreshToken },
      );
      const primary = patientQuery.data?.primary_office_id ?? null;
      const out = new Set<string>();
      for (const row of rows ?? []) {
        const sub = row.sub_office_id ?? null;
        if (sub && sub !== primary) out.add(sub);
      }
      return Array.from(out);
    },
    [accessToken, refreshToken, patientQuery.data?.primary_office_id],
  );

  /** 途中で終わったときの後始末 (盤面 / カレンダー / プールを読み直す)。 */
  const invalidateAll = React.useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['special-visit'] });
    void queryClient.invalidateQueries({ queryKey: ['visits'] });
    void queryClient.invalidateQueries({ queryKey: ['courses'] });
    void queryClient.invalidateQueries({ queryKey: ['field-board'] });
  }, [queryClient]);

  /**
   * 訪問ができたあとの後半 (取り消し → 作り直し → 紐付け)。
   *
   * **訪問は既にできている**ので、ここから先の失敗で throw して再試行させると
   * もう 1 件作ってしまう。どこまで進んだかを文言で伝えて閉じるだけにする。
   */
  const finishLink = React.useCallback(
    async (visitId: string): Promise<void> => {
      let targetMarkId = markId;

      if (replacingMarkId) {
        // ① いまの配置 (マーク + 訪問) を取り消す。**新しい訪問ができた後**に行う。
        try {
          await deleteMarkMut.mutateAsync({ markId: replacingMarkId, force: true });
        } catch {
          // 旧マークに訪問が付いていたかで、残っているものの説明を変える。
          toast.error(
            replacingVisitId
              ? '新しい訪問は登録できましたが、いまの配置を取り消せませんでした。この日に予定が 2 件あります（重複にご注意ください）'
              : '新しい訪問は登録できましたが、いまの追加枠を取り消せませんでした。カレンダーをご確認ください',
          );
          invalidateAll();
          onOpenChange(false);
          return;
        }
        // ② 同じ日に追加枠を作り直す (取り消しでマークごと消えるため)。
        try {
          const fresh = await createMarkMut.mutateAsync({
            periodId,
            payload: { iso_year: isoYear, iso_week: isoWeek, weekday },
          });
          targetMarkId = fresh.id;
        } catch {
          toast.error(
            'いまの配置を取り消し、新しい訪問は登録しました。追加枠（○）を作り直せませんでした。カレンダーで付け直してください',
          );
          invalidateAll();
          onOpenChange(false);
          return;
        }
      }

      if (!targetMarkId) {
        toast.error('紐付ける追加枠が特定できませんでした。カレンダーをご確認ください');
        invalidateAll();
        onOpenChange(false);
        return;
      }

      // ③ visit_id モード = 訪問は作らず、このマークへ紐付けて ● にするだけ。
      try {
        await placeMarkMut.mutateAsync({ markId: targetMarkId, payload: { visit_id: visitId } });
      } catch {
        toast.error(
          '訪問は登録できましたが追加枠への紐付けに失敗しました。盤面に予定は残っています（重複登録に注意）',
        );
        invalidateAll();
        onOpenChange(false);
        return;
      }
      toast.success(`${formatMd(date)} に配置しました（この週のみ）`);
      onPlaced?.();
    },
    [
      markId,
      replacingMarkId,
      replacingVisitId,
      deleteMarkMut,
      createMarkMut,
      placeMarkMut,
      periodId,
      isoYear,
      isoWeek,
      weekday,
      date,
      invalidateAll,
      onOpenChange,
      onPlaced,
    ],
  );

  /**
   * 計画の実行。NG スタッフ / 性別制限の 422 は確認ダイアログを通して
   * **止まった 1 件だけ** acknowledge して再開する (盤面と同じ・自己再帰)。
   */
  const runPlan = React.useCallback(
    async function run(
      plan: AddVisitPlan,
      opGroupId: string,
      acknowledgeIndex?: number,
    ): Promise<void> {
      // 入れ替えは旧訪問が生きたまま新しい訪問を作る。同じ日・同じ時刻は
      // (patient, date, start_time) の部分ユニーク制約に当たるので実行前に止める。
      if (
        replacingMarkId &&
        replacingStartHM &&
        plan.items.some((i) => i.startHM === replacingStartHM)
      ) {
        toast.error(SAME_START_TIME_MESSAGE);
        throw new Error(SAME_START_TIME_MESSAGE);
      }

      const result = await executeAddVisitPlan(
        plan,
        {
          placeAndFix: (req) => placeAndFixMut.mutateAsync(req),
          createVisit: (body) => createVisitMut.mutateAsync(body),
          patchVisitNote: (visitId, note) =>
            fetcher(`/api/v1/visits/${visitId}`, {
              method: 'PATCH',
              body: JSON.stringify({ note }),
              accessToken,
              refreshToken,
            }),
          // 反映先は 'new' 固定 (lockedScope) なので到達しない。
          moveWeekOnly: () => Promise.reject(new Error(UNUSED_SCOPE_ERROR)),
          putFixedVisits: () => Promise.reject(new Error(UNUSED_SCOPE_ERROR)),
          getFixedVisits: () => Promise.reject(new Error(UNUSED_SCOPE_ERROR)),
        },
        {
          opGroupId,
          ...(acknowledgeIndex !== undefined
            ? { startIndex: acknowledgeIndex, acknowledgeIndex }
            : {}),
        },
      );

      const failed = result.failed;
      if (failed) {
        // acknowledge 済みの再送では capture しない (無限ループ防止)。
        if (failed.kind === 'constraint' && acknowledgeIndex === undefined) {
          const captured = constraintConfirm.capture(
            failed.error,
            () => run(plan, opGroupId, failed.index),
            PLACE_CONSTRAINT_TEXT,
          );
          if (captured) throw new Error(CONSTRAINT_PENDING_MESSAGE);
        }
        // 入れ替えの 409/500 は「同じ時刻の重複」がほぼ唯一の原因 (BE のメッセージは
        // 不透明)。事前チェックを抜けた場合 (秒つき等) も同じ案内に寄せる。
        if (replacingMarkId && isDuplicateSlotError(failed.error)) {
          toast.error(SAME_START_TIME_MESSAGE);
          throw new Error(SAME_START_TIME_MESSAGE);
        }
        toast.error(`配置できませんでした: ${failed.message}`);
        throw new Error(failed.message);
      }

      // 2 名体制は place-and-fix が 2 件 (同じ visit_group_id) 作る。紐付けるのは
      // **先頭の 1 件だけ** (BE がグループを解決する)。二度紐付けない。
      const createdIds = result.done.flatMap((d) => d.visitIds ?? []);
      const visitId = createdIds[0] ?? null;
      if (!visitId) {
        const message = '登録した訪問を特定できませんでした';
        toast.error(message);
        throw new Error(message);
      }

      await finishLink(visitId);
    },
    [
      placeAndFixMut,
      createVisitMut,
      accessToken,
      refreshToken,
      constraintConfirm,
      finishLink,
      replacingMarkId,
      replacingStartHM,
    ],
  );

  /**
   * 登録 → マークへ紐付け。
   *
   * 訪問を作る前の失敗だけ throw する (モーダルは開いたまま = 条件を変えて
   * やり直せる)。作った後の失敗は `finishLink` が閉じて状態を伝える。
   */
  const handleExecute = React.useCallback(
    (plan: AddVisitPlan) => runPlan(plan, crypto.randomUUID()),
    [runPlan],
  );

  const initial = React.useMemo(
    () => ({ patientId, dates: [date], lockedScope: 'new' as const, lockedDates: true }),
    [patientId, date],
  );

  return (
    <>
      <AddVisitAnywhereDialog
        open={open}
        onOpenChange={onOpenChange}
        isoYear={isoYear}
        isoWeek={isoWeek}
        patients={patients}
        poolPatientIds={NO_POOL_IDS}
        staffOptions={staffOptions}
        offices={offices.map((o) => ({ id: o.id, name: o.name }))}
        courseTemplates={courseTemplates}
        initial={initial}
        todayIso={todayIsoJst()}
        proposeSlots={(req) => proposeSlotsMut.mutateAsync(req)}
        loadPatientWeekVisits={loadPatientWeekVisits}
        loadPatientSubOfficeIds={loadPatientSubOfficeIds}
        onExecute={handleExecute}
      />
      {/* NG スタッフ / 性別制限 (422) の「確認して通す」。 */}
      <ConstraintOverrideConfirmDialog {...constraintConfirm.dialogProps} />
    </>
  );
}
