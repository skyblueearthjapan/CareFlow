'use client';

/**
 * StaffWeekBoard — 週ビューの「スタッフ別」表示 (PO要望 2026-07-26・案B)。
 *
 * カイポケの職員スケジュール (職員×月〜土グリッド) と同じ構造で、
 * 行 = スタッフ (拠点→名前順)、セル = その日に担当するコース見出し +
 * 訪問明細 (時刻+患者名・常時表示 = 案B) + イベント (緑・📝メモ含む)。
 * 取り込み結果をカイポケ画面と1対1で突き合わせる用途が主眼。
 *
 * 読み取り専用 (操作はコース別ビュー/タイムラインの責務)。データはすべて
 * CourseDayTablePanel が既に持つものを受け取るだけ (BE 追加なし)。
 * 「ズレは隠さず警告」の原則に従い、担当スタッフの居ないコースの訪問も
 * 「（担当なし）」行として必ず表示する。
 */
import * as React from 'react';
import { addDays, format } from 'date-fns';

import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { StaffRead } from '@/lib/schemas/staff';
import type { EventRead } from '@/lib/schemas/staff-events';

import { genderPalette } from '@/lib/scheduling/timeline';

import { getStaffEventsForWeekday } from './courseGrid';
import type { WeekOverviewVisit } from './CourseWeekOverview';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;

export interface StaffWeekBoardProps {
  templates: CourseTemplateRead[];
  officeNameById: Map<string, string>;
  /** 全曜日 × 全 template の visits (CourseWeekOverview と同じ入力)。 */
  visits: WeekOverviewVisit[];
  /** `${templateId}:${weekday}` → assigned_staff_id。 */
  assignedStaffByTemplateWeekday: Map<string, string>;
  staffMap: Map<string, StaffRead>;
  staffEventsByStaff: Map<string, EventRead[]>;
  /** 対象週の月曜。列ヘッダの日付と event の日付一致判定に使う。 */
  weekStart: Date;
  onPatientClick?: (patientId: string) => void;
}

const UNASSIGNED_KEY = '__unassigned__';

function hhmm(t: string | null | undefined): string {
  if (!t) return '';
  return t.slice(0, 5);
}

/** セル内のコース1枠 (見出し + 訪問明細)。 */
interface CellCourse {
  templateId: string;
  label: string; // 例: "稲毛A"
  visits: WeekOverviewVisit[];
}

export function StaffWeekBoard({
  templates,
  officeNameById,
  visits,
  assignedStaffByTemplateWeekday,
  staffMap,
  staffEventsByStaff,
  weekStart,
  onPatientClick,
}: StaffWeekBoardProps) {
  const templateById = React.useMemo(() => {
    const m = new Map<string, CourseTemplateRead>();
    for (const t of templates) m.set(t.id, t);
    return m;
  }, [templates]);

  const courseLabel = React.useCallback(
    (templateId: string): string => {
      const tpl = templateById.get(templateId);
      if (!tpl) return '?';
      const office = officeNameById.get(tpl.office_id) ?? '';
      return `${office} ${tpl.label}`;
    },
    [templateById, officeNameById],
  );

  // (rowKey, weekday) → CellCourse[]。rowKey = staffId or UNASSIGNED_KEY。
  const { cellMap, rowKeys } = React.useMemo(() => {
    const cells = new Map<string, Map<string, CellCourse>>(); // `${row}:${wd}` → tplId → CellCourse
    const rows = new Set<string>();

    for (const v of visits) {
      const staffId =
        assignedStaffByTemplateWeekday.get(`${v.course_template_id}:${v.weekday}`) ??
        UNASSIGNED_KEY;
      rows.add(staffId);
      const cellKey = `${staffId}:${v.weekday}`;
      let byTpl = cells.get(cellKey);
      if (!byTpl) {
        byTpl = new Map<string, CellCourse>();
        cells.set(cellKey, byTpl);
      }
      let cc = byTpl.get(v.course_template_id);
      if (!cc) {
        cc = {
          templateId: v.course_template_id,
          label: courseLabel(v.course_template_id),
          visits: [],
        };
        byTpl.set(v.course_template_id, cc);
      }
      cc.visits.push(v);
    }
    // 訪問は無いがイベントだけある週のスタッフも行に出す (休み週の可視化)。
    for (const [staffId, events] of staffEventsByStaff) {
      if (events.length > 0 && staffMap.has(staffId)) rows.add(staffId);
    }

    // 並び: 拠点名 → スタッフ名。担当なしは末尾。
    const sorted = Array.from(rows).sort((a, b) => {
      if (a === UNASSIGNED_KEY) return 1;
      if (b === UNASSIGNED_KEY) return -1;
      const sa = staffMap.get(a);
      const sb = staffMap.get(b);
      const oa = sa ? (officeNameById.get(sa.primary_office_id ?? '') ?? '') : '';
      const ob = sb ? (officeNameById.get(sb.primary_office_id ?? '') ?? '') : '';
      if (oa !== ob) return oa.localeCompare(ob, 'ja');
      return (sa?.name ?? '').localeCompare(sb?.name ?? '', 'ja');
    });
    return { cellMap: cells, rowKeys: sorted };
  }, [visits, assignedStaffByTemplateWeekday, staffEventsByStaff, staffMap, officeNameById, courseLabel]);

  if (rowKeys.length === 0) {
    return (
      <p className="px-3 py-6 text-center text-sm text-text-muted" data-testid="staff-week-empty">
        この週にはまだ訪問がありません（「週を生成」または取り込みを実行してください）
      </p>
    );
  }

  return (
    <div className="overflow-x-auto" data-testid="staff-week-board">
      <table className="min-w-full border-collapse text-xs">
        <thead>
          <tr className="border-b border-border-default bg-bg-muted">
            <th className="sticky left-0 z-[1] min-w-[9rem] border-r border-border-default bg-bg-muted px-3 py-2 text-left font-medium text-text-secondary">
              職員氏名
            </th>
            {WEEKDAY_LABELS.map((label, wd) => (
              <th
                key={label}
                className="min-w-[11rem] border-r border-border-subtle px-2 py-2 text-left font-medium text-text-secondary"
              >
                {format(addDays(weekStart, wd), 'M/d')}（{label}）
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rowKeys.map((rowKey) => {
            const staff = rowKey === UNASSIGNED_KEY ? null : staffMap.get(rowKey);
            const officeName = staff
              ? (officeNameById.get(staff.primary_office_id ?? '') ?? '')
              : '';
            return (
              <tr
                key={rowKey}
                className="border-b border-border-subtle align-top"
                data-testid={`staff-week-row-${rowKey}`}
              >
                <td className="sticky left-0 z-[1] border-r border-border-default bg-bg-base px-3 py-2">
                  <span className="block font-bold text-text-primary">
                    {staff ? staff.name : '（担当なし）'}
                  </span>
                  {staff ? (
                    <span className="block text-[10px] text-text-muted">
                      {officeName}
                      {staff.is_trainee ? '・⚠新人' : ''}
                    </span>
                  ) : (
                    <span className="block text-[10px] text-warning-strong">
                      スタッフ未割当の訪問
                    </span>
                  )}
                </td>
                {WEEKDAY_LABELS.map((_, wd) => {
                  const byTpl = cellMap.get(`${rowKey}:${wd}`);
                  const courses = byTpl
                    ? Array.from(byTpl.values()).sort((a, b) =>
                        a.label.localeCompare(b.label, 'ja'),
                      )
                    : [];
                  // 曜日一致で判定 (CourseWeekOverview と同じ呼び方)。
                  // staffEventsByStaff は親が当該週で取得済みのため週跨ぎ混入はない。
                  const events =
                    rowKey !== UNASSIGNED_KEY
                      ? getStaffEventsForWeekday(rowKey, wd, staffEventsByStaff)
                      : [];
                  return (
                    <td
                      key={wd}
                      className="border-r border-border-subtle px-2 py-1.5"
                      data-testid={`staff-week-cell-${rowKey}-${wd}`}
                    >
                      <div className="space-y-1.5">
                        {/* イベント (緑・カイポケの個別業務と同じ立ち位置)。📝 = ゼロ長メモ */}
                        {events.map((ev) => {
                          const isMemo = ev.start_time === ev.end_time;
                          return (
                            <div
                              key={`ev-${ev.id}`}
                              className="rounded bg-success-bg px-1.5 py-0.5 text-[11px] font-medium text-success"
                              title={ev.note ?? undefined}
                            >
                              {isMemo
                                ? `📝 ${ev.title || ev.type}`
                                : `${hhmm(ev.start_time)}〜${hhmm(ev.end_time)} ${ev.title || ev.type}`}
                            </div>
                          );
                        })}
                        {/* コース見出し + 訪問明細 (案B: 常時表示) */}
                        {courses.map((cc) => (
                          <div key={cc.templateId}>
                            <div className="mb-0.5 inline-flex items-center rounded bg-bg-muted px-1.5 py-px text-[10px] font-bold text-text-secondary">
                              {cc.label}
                            </div>
                            <ul className="space-y-0.5">
                              {cc.visits
                                .slice()
                                .sort((a, b) =>
                                  (a.start_time ?? '').localeCompare(b.start_time ?? ''),
                                )
                                .map((v) => {
                                  // 週ビュー(リスト)と同じ視覚言語の性別ウォッシュカード行
                                  // (CourseWeekOverview の visit 行と同一スタイル・PO要望)。
                                  const pal = genderPalette(v.patient_sex);
                                  const sexStyle: React.CSSProperties =
                                    v.patient_sex_restriction === 'female_only'
                                      ? { color: '#dc2626', fontWeight: 600 }
                                      : v.patient_sex_restriction === 'male_only'
                                        ? { color: '#2563eb', fontWeight: 600 }
                                        : {};
                                  return (
                                    <li
                                      key={v.id}
                                      className="flex items-center gap-1 rounded border border-l-[3px] px-1 py-0.5 text-[10px] text-text-primary"
                                      style={{
                                        background: pal.bg,
                                        borderColor: pal.ln,
                                        borderLeftColor: pal.bar,
                                      }}
                                      title={v.patient_name ?? undefined}
                                      data-testid={`staff-week-visit-${v.id}`}
                                    >
                                      {/* 行頭の性別ドット (週リストと同じ視覚言語)。 */}
                                      <i
                                        className="h-1.5 w-1.5 shrink-0 rounded-full"
                                        style={{ background: pal.bar }}
                                        aria-hidden="true"
                                      />
                                      <span className="min-w-0 flex-1 truncate">
                                        <span className="mr-1 tnum text-text-muted">
                                          {hhmm(v.start_time)}
                                          {v.end_time ? `〜${hhmm(v.end_time)}` : ''}
                                        </span>
                                        {onPatientClick ? (
                                          <button
                                            type="button"
                                            className="underline-offset-2 hover:underline focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary"
                                            style={sexStyle}
                                            onClick={() => onPatientClick(v.patient_id)}
                                            aria-label={`${v.patient_name ?? ''} の詳細を開く`}
                                          >
                                            {v.patient_name ?? '（無名）'}
                                          </button>
                                        ) : (
                                          <span style={sexStyle}>{v.patient_name ?? '（無名）'}</span>
                                        )}
                                      </span>
                                    </li>
                                  );
                                })}
                            </ul>
                          </div>
                        ))}
                        {courses.length === 0 && events.length === 0 && (
                          <span className="text-[10px] text-text-muted">—</span>
                        )}
                      </div>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
