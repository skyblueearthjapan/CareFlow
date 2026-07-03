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
 */
import * as React from 'react';

import type { CourseSnapshot, ImprovementSuggestion } from '@/lib/schemas/v2/improvementSuggestion';

function hhmmToMin(t: string): number {
  const [h, m] = t.slice(0, 5).split(':').map(Number);
  return (h ?? 0) * 60 + (m ?? 0);
}

function addMinutes(t: string, minutes: number): string {
  const total = hhmmToMin(t) + minutes;
  return `${String(Math.floor(total / 60) % 24).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

interface TimelineRow {
  key: string;
  name: string;
  start: string; // HH:MM
  end: string; // HH:MM
  kind: 'normal' | 'out' | 'in';
}

function TimelinePanel({
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
      <div className="space-y-0.5">
        {sorted.map((row) => (
          <div
            key={row.key}
            className={
              row.kind === 'in'
                ? 'flex items-center gap-1.5 rounded border-l-2 border-brand-primary bg-bg-muted px-1.5 py-0.5 text-[11px] font-medium text-text-primary'
                : row.kind === 'out'
                  ? 'flex items-center gap-1.5 px-1.5 py-0.5 text-[11px] text-text-muted line-through'
                  : 'flex items-center gap-1.5 px-1.5 py-0.5 text-[11px] text-text-primary'
            }
            data-kind={row.kind}
          >
            <span className="tabular-nums">
              {row.start}–{row.end}
            </span>
            <span className="truncate">{row.name} 様</span>
            {row.kind === 'in' ? (
              <span className="ml-auto shrink-0 whitespace-nowrap text-[10px] text-brand-primary">
                ← ここへ移動
              </span>
            ) : row.kind === 'out' ? (
              <span className="ml-auto shrink-0 whitespace-nowrap text-[10px] no-underline">
                移動元
              </span>
            ) : null}
          </div>
        ))}
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
}

export function CourseMoveTimeline({
  suggestion: sug,
  targetPatientId,
  patientName,
  sourceCourse: src,
  destinationCourse: dst,
}: CourseMoveTimelineProps) {
  if (!src) return null; // 旧 BE レスポンス互換: スナップショットが無ければ出さない.
  const cp = sug.swap_counterpart;

  // X (視点主) の新枠.
  const xIn: TimelineRow = {
    key: 'in-x',
    name: patientName,
    start: sug.candidate.start_time.slice(0, 5),
    end: sug.candidate.end_time.slice(0, 5),
    kind: 'in',
  };
  // Y (swap 相手) の新枠 = X の旧コースへ。end はスナップショットの所要分数から導出.
  const yIn: TimelineRow | null = cp
    ? {
        key: 'in-y',
        name: cp.patient_name,
        start: cp.new_start_time.slice(0, 5),
        end: addMinutes(cp.new_start_time.slice(0, 5), durationOf(dst ?? src, cp.patient_id, 30)),
        kind: 'in',
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
