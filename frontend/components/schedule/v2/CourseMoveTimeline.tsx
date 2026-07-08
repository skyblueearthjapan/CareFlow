'use client';

/**
 * CourseMoveTimeline — 提案のコースタイムライン (UI 統一).
 *
 * 範囲最適化 (ScopeOptimizeDialog の step) と患者詳細の改善提案
 * (ImprovementSuggestionsSection) で **同一の見せ方** を共有する:
 *   - 同一コース内の手 = 1 枚のタイムラインで「元の位置 (打消し) → ここへ移動 (強調)」
 *   - 別コースへの手   = 移動元 / 移動先の 2 枚を並列表示して比較
 *   - swap は双方の抜き差しを両パネルに描く
 *
 * データはスナップショット (CourseSnapshot: 提案生成時点のコース訪問列) を使う。
 * スナップショットが無い場合 (旧 BE レスポンス等) は何も描かない (互換)。
 *
 * デザイン: Warm & Human トークンのみ。数字は tabular-nums。
 * T-4 (2026-07-08): 行を日リスト (TimelineDayList.VisitRow) と同じカード視覚言語へ統一
 * (性別ウォッシュ地 + 性別左帯 + 性別ドット + 角丸 + 条件ピル + 📍住所 + PushPin)。
 * 性別等のメタは optional (patientMetaById) — 無い行は中立色でそのまま成立する。
 */
import * as React from 'react';

import { PushPin } from '@/components/ui/push-pin';
import type { CourseSnapshot, ImprovementSuggestion } from '@/lib/schemas/v2/improvementSuggestion';
import { genderPalette } from '@/lib/scheduling/timeline';
import { cn } from '@/lib/utils';

function hhmmToMin(t: string): number {
  const [h, m] = t.slice(0, 5).split(':').map(Number);
  return (h ?? 0) * 60 + (m ?? 0);
}

function addMinutes(t: string, minutes: number): string {
  const total = hhmmToMin(t) + minutes;
  return `${String(Math.floor(total / 60) % 24).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

/**
 * T-4: タイムラインのカード視覚言語用メタ (すべて optional・後方互換)。
 * 未指定の行は中立色 (性別不明=砂色) で描画される。
 */
export interface TimelineRowMeta {
  /** 患者性別 (male/female/unknown)。ウォッシュ地・左帯・ドットの色。 */
  sex?: string | null;
  /** 条件ピル (例: 👩女性のみ / 🕐午前 / 2名)。表示文字列を呼び出し側で確定して渡す。 */
  condLabel?: string | null;
  /** 📍住所 (小さく truncate 表示)。 */
  address?: string | null;
  /** 完全固定 (PushPin 表示・読み取り専用)。 */
  pinned?: boolean;
}

export interface TimelineRow extends TimelineRowMeta {
  key: string;
  name: string;
  start: string; // HH:MM
  end?: string; // HH:MM (無ければ開始時刻のみ表示: mini_schedule は終了時刻を持たない)
  kind: 'normal' | 'out' | 'in';
}

export function TimelinePanel({
  title,
  rows,
  testId,
}: {
  title: string;
  rows: TimelineRow[];
  testId: string;
}) {
  const sorted = [...rows].sort(
    (a, b) => hhmmToMin(a.start) - hhmmToMin(b.start) || (a.kind === 'in' ? 1 : -1),
  );
  return (
    <div
      className="min-w-0 rounded border border-border-default bg-bg-base p-2"
      data-testid={testId}
    >
      <div className="mb-1 text-[11px] font-medium text-text-secondary">{title}</div>
      <div className="space-y-1">
        {sorted.map((row) => {
          // T-4: 日リスト (TimelineDayList.VisitRow) と同じカード視覚言語。
          // 性別ウォッシュ地 + 性別左帯 + 性別ドット。メタ無し行は中立色。
          const pal = genderPalette(row.sex);
          const isIn = row.kind === 'in';
          const isOut = row.kind === 'out';
          return (
            <div
              key={row.key}
              className={cn(
                'flex items-center gap-1.5 rounded-md border border-l-[3px] px-1.5 py-0.5 text-[11px]',
                isIn && 'font-medium text-text-primary ring-1 ring-brand-primary',
                isOut && 'text-text-muted line-through',
                !isIn && !isOut && 'text-text-primary',
              )}
              style={
                // 打消し行 (移動元の旧位置) はウォッシュを敷かず、退場感を出す。
                isOut
                  ? { borderColor: 'transparent', borderLeftColor: pal.bar }
                  : { borderColor: pal.ln, borderLeftColor: pal.bar, background: pal.bg }
              }
              data-kind={row.kind}
            >
              <i
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ background: pal.bar }}
                aria-hidden="true"
              />
              <span className={cn('tabular-nums', !isOut && 'font-semibold')}>
                {row.end ? `${row.start}–${row.end}` : row.start}
              </span>
              <span className="truncate">{row.name} 様</span>
              {row.pinned ? <PushPin className="h-3 w-3 shrink-0" aria-label="ピン留め" /> : null}
              {row.condLabel ? (
                <span className="shrink-0 text-[9px] no-underline text-text-secondary">
                  {row.condLabel}
                </span>
              ) : null}
              {row.address ? (
                <span
                  className="min-w-0 truncate text-[9px] no-underline text-text-muted"
                  title={row.address}
                >
                  📍{row.address}
                </span>
              ) : null}
              {row.kind === 'in' ? (
                <span className="ml-auto shrink-0 whitespace-nowrap text-[10px] font-semibold text-brand-primary">
                  ← ここへ移動
                </span>
              ) : row.kind === 'out' ? (
                <span className="ml-auto shrink-0 whitespace-nowrap text-[10px] no-underline">
                  移動元
                </span>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** スナップショットから patient の所要分数を引く (見つからなければ fallback). */
function durationOf(snapshot: CourseSnapshot, patientId: string, fallback: number): number {
  const v = snapshot.visits.find((x) => x.patient_id === patientId);
  if (!v) return fallback;
  return Math.max(0, hhmmToMin(v.end_time) - hhmmToMin(v.start_time));
}

export interface CourseMoveTimelineProps {
  suggestion: ImprovementSuggestion;
  /** 視点主 (X) の患者 ID (スナップショット内のハイライト対象). */
  targetPatientId: string;
  /** 視点主 (X) の氏名 (挿入行の表示). */
  patientName: string;
  /** 移動元コースのスナップショット (無ければ何も描かない). */
  sourceCourse: CourseSnapshot | null | undefined;
  /** 移動先コースのスナップショット (同一コース内は null). */
  destinationCourse: CourseSnapshot | null | undefined;
  /**
   * T-4: 患者 ID → 表示メタ (性別ウォッシュ・条件ピル・住所)。optional —
   * 未指定なら全行が中立色で描画される (旧呼び出し互換)。
   */
  patientMetaById?: ReadonlyMap<string, TimelineRowMeta>;
}

export function CourseMoveTimeline({
  suggestion: sug,
  targetPatientId,
  patientName,
  sourceCourse: src,
  destinationCourse: dst,
  patientMetaById,
}: CourseMoveTimelineProps) {
  if (!src) return null; // 旧 BE レスポンス互換: スナップショットが無ければ出さない.
  const cp = sug.swap_counterpart;
  const metaOf = (patientId: string): TimelineRowMeta => patientMetaById?.get(patientId) ?? {};

  // X (視点主) の新枠.
  const xIn: TimelineRow = {
    key: 'in-x',
    name: patientName,
    start: sug.candidate.start_time.slice(0, 5),
    end: sug.candidate.end_time.slice(0, 5),
    kind: 'in',
    ...metaOf(targetPatientId),
  };
  // Y (swap 相手) の新枠 = X の旧コースへ。end はスナップショットの所要分数から導出.
  const yIn: TimelineRow | null = cp
    ? {
        key: 'in-y',
        name: cp.patient_name,
        start: cp.new_start_time.slice(0, 5),
        end: addMinutes(cp.new_start_time.slice(0, 5), durationOf(dst ?? src, cp.patient_id, 30)),
        kind: 'in',
        ...metaOf(cp.patient_id),
      }
    : null;

  const srcRows: TimelineRow[] = src.visits.map((v) => ({
    key: `s-${v.patient_id}-${v.start_time}`,
    name: v.patient_name,
    start: v.start_time.slice(0, 5),
    end: v.end_time.slice(0, 5),
    kind:
      v.patient_id === targetPatientId || (!dst && cp && v.patient_id === cp.patient_id)
        ? 'out'
        : 'normal',
    ...metaOf(v.patient_id),
  }));

  if (!dst) {
    // 同一コース内: 1 枚に out (旧位置) と in (新位置) を同居させる.
    srcRows.push(xIn);
    if (yIn) srcRows.push(yIn);
    return (
      <div data-testid="course-move-timeline-single">
        <TimelinePanel
          title={`コース内の動き（${src.course_label}${src.staff_name ? `・${src.staff_name}` : ''}）`}
          rows={srcRows}
          testId="course-move-timeline-src"
        />
      </div>
    );
  }

  // 別コース: 移動元 / 移動先を並列表示.
  if (yIn) srcRows.push(yIn); // swap: Y は X の旧コース (src) へ入る.
  const dstRows: TimelineRow[] = dst.visits.map((v) => ({
    key: `d-${v.patient_id}-${v.start_time}`,
    name: v.patient_name,
    start: v.start_time.slice(0, 5),
    end: v.end_time.slice(0, 5),
    kind: cp && v.patient_id === cp.patient_id ? 'out' : 'normal',
    ...metaOf(v.patient_id),
  }));
  dstRows.push(xIn);
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2" data-testid="course-move-timeline-pair">
      <TimelinePanel
        title={`移動元（${src.course_label}${src.staff_name ? `・${src.staff_name}` : ''}）`}
        rows={srcRows}
        testId="course-move-timeline-src"
      />
      <TimelinePanel
        title={`移動先（${dst.course_label}${dst.staff_name ? `・${dst.staff_name}` : ''}）`}
        rows={dstRows}
        testId="course-move-timeline-dst"
      />
    </div>
  );
}

/**
 * BeforeAfterCourseTimeline — 「変更前 → 変更後」の 2 列タイムライン (UI 統一・W-13b)。
 *
 * CourseMoveTimeline は改善提案 (ImprovementSuggestion) 起点の move を描くのに対し、
 * こちらは **任意の before/after 行列** を受け取り同じ視覚言語で 2 列に描く汎用版:
 *   - 詰まり解消プランカード: 影響コースの before/after スナップショット (patient_id で差分)
 *   - 個別配置提案の採用確認パネル: mini_schedule の before(是入前)/after(是入後)
 * kind (out=打消し / in=強調 / normal) は **呼び出し側が確定** して渡す
 * (差分ロジックはデータ形により異なるため)。色・レイアウト・「← ここへ移動」表現は共有。
 */
export function BeforeAfterCourseTimeline({
  title,
  beforeRows,
  afterRows,
  testIdPrefix,
}: {
  /** コース見出し (例: 稲B・火曜・山田)。変更前/変更後の () 内に出す。 */
  title: string;
  beforeRows: TimelineRow[];
  afterRows: TimelineRow[];
  testIdPrefix: string;
}) {
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2" data-testid={testIdPrefix}>
      <TimelinePanel
        title={`変更前（${title}）`}
        rows={beforeRows}
        testId={`${testIdPrefix}-before`}
      />
      <TimelinePanel
        title={`変更後（${title}）`}
        rows={afterRows}
        testId={`${testIdPrefix}-after`}
      />
    </div>
  );
}
