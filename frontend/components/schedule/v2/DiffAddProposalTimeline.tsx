'use client';

/**
 * DiffAddProposalTimeline — 差分追加 (機能 A) の提案候補に紐付く
 * 「対象コースの 1 日タイムライン」表示用サブコンポーネント.
 *
 * 仕様書: ``docs/plans/auto-schedule-v2.md`` v0.2 §13.5.1 (拡張: 既存訪問との
 * 位置関係を視覚化)
 *
 * 役割:
 *   - proposal の (office_id, weekday, course_code) に対応する Course を引き、
 *     その course に属する visits を start_time 昇順で並べる
 *   - 提案の start_time / end_time 行を「(新規)」として既存 visit の中に挿入し、
 *     黄色背景で強調表示
 *   - 前後の visit との時刻ギャップ (= 移動可能時間) をコメントとして添える
 *
 * Backend 改修なし: 取得は既存の useCourses / useVisits / usePatients に集約.
 * Course にまだ visits.course_id が紐付いていない場合 (Layer 1 未確定週) は
 * 「既存 visits 不明」プレースホルダーを表示してフェイルセーフ.
 *
 * DRY: タイムライン描画は共通 ``WeekdayScheduleCard`` を流用 (黄色枠 + 📍
 * の視覚言語を踏襲). 「(新規)」行は ``same_address_group_id`` を流用せず、
 * 別ロジック (synthesized visit に専用 key) で highlight する.
 */
import * as React from 'react';
import { Sparkles } from 'lucide-react';

import { useCourses, type CourseV2Read } from '@/lib/queries/courses';
import { useOffices } from '@/lib/queries/offices';
import { usePatients } from '@/lib/queries/patients';
import { useVisits } from '@/lib/queries/visits';
import type { PatientRead } from '@/lib/schemas/patient';
import type { DiffAddProposal, V2VisitPlan } from '@/lib/schemas/v2/autoScheduleV2';

import {
  WeekdayScheduleCard,
  buildSameAddressKey,
  type CourseListItem,
  type VisitListItem,
} from '../WeekdayScheduleCard';

import { trimSeconds } from './_autoScheduleUtils';

// ─────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

/** ISO (year, week) → 当該週の月曜日 (UTC) を返す. */
function isoWeekToMonday(isoYear: number, isoWeek: number): Date {
  const jan4 = new Date(Date.UTC(isoYear, 0, 4));
  const jan4Day = jan4.getUTCDay() || 7; // Mon=1..Sun=7
  const week1Monday = new Date(jan4);
  week1Monday.setUTCDate(jan4.getUTCDate() - (jan4Day - 1));
  const target = new Date(week1Monday);
  target.setUTCDate(week1Monday.getUTCDate() + (isoWeek - 1) * 7);
  return target;
}

/** Date → 'YYYY-MM-DD' (UTC ベース). */
function toIsoDate(d: Date): string {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const day = String(d.getUTCDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/** "HH:MM[:SS]" → 分 (深夜跨ぎは想定外). 不正なら null. */
function toMinutes(t: string | null | undefined): number | null {
  if (!t || t.length < 5) return null;
  const hh = Number(t.slice(0, 2));
  const mm = Number(t.slice(3, 5));
  if (!Number.isFinite(hh) || !Number.isFinite(mm)) return null;
  return hh * 60 + mm;
}

function formatGapMin(gapMin: number | null): string | null {
  if (gapMin === null) return null;
  if (gapMin <= 0) return '隙間なし';
  return `${gapMin} 分`;
}

// ─────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────

export interface DiffAddProposalTimelineProps {
  proposal: DiffAddProposal;
  isoYear: number;
  isoWeek: number;
  /** suggested に加え、複数曜日に展開された全 visit を timeline として描く. */
  visits: V2VisitPlan[];
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export function DiffAddProposalTimeline({
  proposal,
  isoYear,
  isoWeek,
  visits,
}: DiffAddProposalTimelineProps) {
  // 提案された visit 群を曜日ごとにまとめる. 通常は 1 件 (suggested) のみ.
  const visitsByWeekday = React.useMemo(() => {
    const m = new Map<number, V2VisitPlan[]>();
    for (const v of visits) {
      const arr = m.get(v.weekday) ?? [];
      arr.push(v);
      m.set(v.weekday, arr);
    }
    return m;
  }, [visits]);

  return (
    <div className="space-y-2 px-3 py-2" data-testid={`diff-add-timeline-${proposal.proposal_id}`}>
      {[...visitsByWeekday.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([weekday, group]) => (
          <SingleWeekdayTimeline
            key={`${proposal.proposal_id}-${weekday}`}
            proposal={proposal}
            isoYear={isoYear}
            isoWeek={isoWeek}
            weekday={weekday}
            proposedVisits={group}
          />
        ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// SingleWeekdayTimeline — 1 曜日分のタイムライン
// ─────────────────────────────────────────────────────────────────────────

interface SingleWeekdayTimelineProps {
  proposal: DiffAddProposal;
  isoYear: number;
  isoWeek: number;
  weekday: number;
  /** この曜日に挿入される proposed visit (通常 1 件). */
  proposedVisits: V2VisitPlan[];
}

function SingleWeekdayTimeline({
  proposal,
  isoYear,
  isoWeek,
  weekday,
  proposedVisits,
}: SingleWeekdayTimelineProps) {
  // 代表 visit (= 表示順位の決定 / course 引きに使う 1 件).
  // 仕様上、同 weekday に複数候補が並ぶことは稀だが、最初の 1 件を代表とする.
  const primary = proposedVisits[0];

  // 1 曜日分のデータを fetch.
  // - courses: 対象週の全 course (office_id × weekday × code でフィルタ).
  // - visits : 該当日のみ (week_start = week_end = その日).
  // - patients: 名前 / 住所 / lat,lng の引き当て.
  // - offices : office_id → 表示名.
  const coursesQuery = useCourses({ iso_year: isoYear, iso_week: isoWeek, limit: 200 });
  const courses = React.useMemo(() => coursesQuery.data ?? [], [coursesQuery.data]);

  const dayDate = React.useMemo(() => {
    const monday = isoWeekToMonday(isoYear, isoWeek);
    monday.setUTCDate(monday.getUTCDate() + weekday);
    return toIsoDate(monday);
  }, [isoYear, isoWeek, weekday]);

  const visitsQuery = useVisits({ week_start: dayDate, week_end: dayDate });
  const dayVisits = React.useMemo(() => visitsQuery.data?.items ?? [], [visitsQuery.data]);

  const patientsQuery = usePatients({ limit: 500 });
  const patientById = React.useMemo(() => {
    const m = new Map<string, PatientRead>();
    for (const p of patientsQuery.data?.items ?? []) m.set(p.id, p);
    return m;
  }, [patientsQuery.data]);

  const officesQuery = useOffices({ limit: 50 });
  const officeNameById = React.useMemo(() => {
    const m = new Map<string, string>();
    for (const o of officesQuery.allOffices ?? []) m.set(o.id, o.name);
    return m;
  }, [officesQuery.allOffices]);

  // ─── 対象コース解決 ────────────────────────────────────────────
  const targetCourse: CourseV2Read | null = React.useMemo(() => {
    if (!primary) return null;
    const code = primary.course_code.toUpperCase();
    return (
      courses.find(
        (c) =>
          c.office_id === primary.office_id &&
          c.weekday === weekday &&
          String(c.code).toUpperCase() === code &&
          !c.deleted_at,
      ) ?? null
    );
  }, [courses, primary, weekday]);

  // ─── 既存 visits を course / start_time でフィルタ + ソート ──────
  const existingCourseVisits = React.useMemo(() => {
    if (!targetCourse) return [];
    return dayVisits
      .filter((v) => v.course_id === targetCourse.id)
      .sort((a, b) => (a.start_time ?? '').localeCompare(b.start_time ?? ''));
  }, [dayVisits, targetCourse]);

  // ─── 表示用 VisitListItem 列を構築 ─────────────────────────────
  // 既存 visit を VisitListItem に変換しつつ、proposed visit を時刻順に差し込む.
  const courseListItem: CourseListItem = React.useMemo(() => {
    const officeName = officeNameById.get(primary?.office_id ?? '') ?? '不明';
    const code = primary?.course_code ?? '?';

    // 既存 visit → VisitListItem.
    const existingItems: VisitListItem[] = existingCourseVisits.map((v) => {
      const p = patientById.get(v.patient_id);
      return {
        key: `existing-${v.id}`,
        start_time: v.start_time,
        patient_name: p?.name ?? v.patient_name ?? '(不明)',
        address: p?.address ?? null,
        same_address_group_id: buildSameAddressKey(p?.lat ?? null, p?.lng ?? null),
        distance_to_next_km: null,
      };
    });

    // proposed visit → VisitListItem (新規バッジ + 黄色強調用にユニーク key prefix).
    const proposedPatient = patientById.get(proposal.patient_id);
    const proposedItems: VisitListItem[] = proposedVisits.map((pv, i) => ({
      key: `proposed-${proposal.proposal_id}-${i}`,
      start_time: pv.start_time,
      patient_name: `${proposal.patient_name} (新規)`,
      address: proposedPatient?.address ?? null,
      // 強制的に同住所グループ扱いさせない (= 既存 group との混線を避ける).
      same_address_group_id: null,
      distance_to_next_km: null,
    }));

    // 結合 → start_time 昇順ソート.
    const merged = [...existingItems, ...proposedItems].sort((a, b) =>
      (a.start_time ?? '').localeCompare(b.start_time ?? ''),
    );

    return {
      key: `${primary?.office_id ?? '?'}-${code}-${weekday}`,
      title: `${officeName} ${code} コース`,
      summary: `${existingCourseVisits.length}件 既存 / +${proposedVisits.length}件 提案`,
      visits: merged,
    };
  }, [
    primary,
    existingCourseVisits,
    proposedVisits,
    proposal.patient_name,
    proposal.patient_id,
    proposal.proposal_id,
    patientById,
    officeNameById,
    weekday,
  ]);

  // ─── 前後 visit 移動ギャップ (= 最初の提案行のみ表示) ───────────
  const insertionGap = React.useMemo(() => {
    if (!primary) return null;
    const startMin = toMinutes(primary.start_time);
    const endMin = toMinutes(primary.end_time);
    if (startMin === null || endMin === null) return null;
    let beforeName: string | null = null;
    let beforeGap: number | null = null;
    let afterName: string | null = null;
    let afterGap: number | null = null;
    // Phase G-98 (懸念②): 提案区間と重なる既存訪問を「重複」として収集する.
    // 旧実装は前 (vEnd<=startMin) でも後 (vStart>=endMin) でもない重なり訪問を
    // 無言で除外しており、 例えば 15:30-16:05 の提案に対し 16:00 開始の既存訪問が
    // 「後」条件を満たさず脱落し「後:(なし)」と誤表示されていた. 重なりは
    // 明示的に衝突として surface する (= ユーザーに「実は埋まっている」を見せる).
    const conflicts: { name: string; range: string }[] = [];
    for (const v of existingCourseVisits) {
      const vStart = toMinutes(v.start_time);
      const vEnd = toMinutes(v.end_time);
      if (vStart === null || vEnd === null) continue;
      const vName = patientById.get(v.patient_id)?.name ?? v.patient_name ?? '(不明)';
      if (vEnd <= startMin) {
        // 候補より前.
        if (beforeGap === null || startMin - vEnd < beforeGap) {
          beforeGap = startMin - vEnd;
          beforeName = vName;
        }
      } else if (vStart >= endMin) {
        // 候補より後.
        if (afterGap === null || vStart - endMin < afterGap) {
          afterGap = vStart - endMin;
          afterName = vName;
        }
      } else {
        // 提案区間と重なる = 同時刻帯がすでに埋まっている (旧実装は無言除外).
        conflicts.push({
          name: vName,
          range: `${trimSeconds(v.start_time)}–${trimSeconds(v.end_time)}`,
        });
      }
    }
    return { beforeName, beforeGap, afterName, afterGap, conflicts };
  }, [primary, existingCourseVisits, patientById]);

  // ─── ローディング / フォールバック ────────────────────────────
  const isLoading = coursesQuery.isLoading || visitsQuery.isLoading || patientsQuery.isLoading;
  const isCourseMissing = !isLoading && targetCourse === null;
  const isVisitsEmpty = !isLoading && targetCourse !== null && existingCourseVisits.length === 0;

  // ─── Render ────────────────────────────────────────────────────
  if (!primary) return null;

  return (
    <div
      className="rounded border border-border-default bg-bg-muted/30 p-2"
      data-testid={`diff-add-timeline-${proposal.proposal_id}-wd-${weekday}`}
    >
      <div className="mb-1 flex items-center gap-2 text-[11px] font-semibold text-text-secondary">
        <Sparkles className="h-3 w-3 text-yellow-600" aria-hidden />
        <span>
          {WEEKDAY_LABELS[weekday] ?? '?'} の 1 日タイムライン ({trimSeconds(primary.start_time)}{' '}
          に挿入予定)
        </span>
      </div>

      {isLoading ? (
        <div className="py-3 text-center text-[11px] text-text-muted">タイムライン取得中…</div>
      ) : isCourseMissing ? (
        <div className="py-2 text-[11px] text-text-muted">
          対象コース ({primary.course_code} コース) が当該週にまだ生成されていません。
          まずは「週を生成」してください。
        </div>
      ) : (
        <>
          <WeekdayScheduleCard
            title="既存訪問 + 提案 (黄色: 新規挿入)"
            tone="muted"
            courses={[courseListItem]}
            maxVisitsPerCourse={0}
            testIdPrefix={`diff-add-timeline-${proposal.proposal_id}`}
          />
          {/* 「(新規)」行を黄色強調する CSS 追補. 同住所グループは disable してあるため、
             別 selector で background をかける. */}
          <style>{`
            [data-testid^="diff-add-timeline-${proposal.proposal_id}-visit-proposed-"] {
              border-left: 2px solid rgb(250 204 21);
              background-color: rgba(254, 240, 138, 0.5);
              padding-left: 0.5rem;
            }
          `}</style>
          {isVisitsEmpty ? (
            <div className="mt-1 text-[10px] text-text-muted">
              ※ 同コースの既存訪問はまだ DB に入っていません (Layer 1 未確定の可能性)。
            </div>
          ) : null}
          {insertionGap && insertionGap.conflicts.length > 0 ? (
            <div
              className="mt-1 rounded border border-error/40 bg-error/5 px-2 py-1 text-[10px] text-error"
              data-testid={`diff-add-timeline-${proposal.proposal_id}-conflicts`}
            >
              <span className="font-semibold">⚠ 同時刻に重複: </span>
              {insertionGap.conflicts.map((c, i) => (
                <span key={i} className="tnum">
                  {i > 0 ? '、' : ''}
                  {c.name} ({c.range})
                </span>
              ))}
              <span className="text-text-muted"> — この時間帯はすでに埋まっています</span>
            </div>
          ) : null}
          {insertionGap ? (
            <div className="mt-1 grid grid-cols-2 gap-2 text-[10px] text-text-secondary">
              <div>
                <span className="font-semibold">前: </span>
                {insertionGap.beforeName ?? '(なし)'}
                {insertionGap.beforeName ? (
                  <span className="tnum text-text-muted">
                    {' '}
                    (隙間 {formatGapMin(insertionGap.beforeGap) ?? '—'})
                  </span>
                ) : null}
              </div>
              <div>
                <span className="font-semibold">後: </span>
                {insertionGap.afterName ?? '(なし)'}
                {insertionGap.afterName ? (
                  <span className="tnum text-text-muted">
                    {' '}
                    (隙間 {formatGapMin(insertionGap.afterGap) ?? '—'})
                  </span>
                ) : null}
              </div>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
