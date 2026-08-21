'use client';

/**
 * StaffWeekBoard — 「職員スケジュール」タブ (スタッフ×月〜土グリッド)。
 *
 * カイポケの職員スケジュール (職員×月〜土グリッド) と同じ構造で、
 * 行 = スタッフ (拠点→名前順)、セル = その日に担当するコース見出し +
 * 訪問明細 (時刻+患者名・常時表示 = 案B) + イベント (緑・📝メモ含む)。
 *
 * 2026-08-20 昇格 (kaipoke-event-two-way-design.md §6-c): 週ビュー内の
 * 読み取り専用サブモード (取り込み結果の突き合わせ用・PO要望 2026-07-26 案B) から、
 * トップレベルタブ「職員スケジュール」= イベント運用の家へ。二層の描き分け:
 *   - 投影 (読むだけ): 訪問・コースチップ — コースで決まる予定。編集はコース盤面で。
 *   - 正典 (ここが家): イベント — `onAddEvent`(セルの＋) / `onEventClick`(帯クリック
 *     → 編集/削除) を渡すと編集可能になる。未指定なら従来どおり読み取り専用。
 *
 * データはすべて CourseDayTablePanel が既に持つものを受け取るだけ (BE 追加なし)。
 * 「ズレは隠さず警告」の原則に従い、担当スタッフの居ないコースの訪問も
 * 「（担当なし）」行として必ず表示する。
 */
import * as React from 'react';
import { addDays, format } from 'date-fns';

import type { WeekOverrideRead } from '@/lib/queries/staff-overrides';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { StaffRead } from '@/lib/schemas/staff';
import type { EventRead } from '@/lib/schemas/staff-events';

import { genderPalette } from '@/lib/scheduling/timeline';

import { getStaffEventsForWeekday } from './courseGrid';
import type { WeekOverviewVisit } from './CourseWeekOverview';
import {
  applyCourseDragImage,
  COURSE_DND_MIME,
  readCourseDragPayload,
  readVisitDragPayload,
  VISIT_DND_MIME,
  type BoardDragState,
} from './WeekCoursePalette';

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
  /** 職員スケジュールタブ: 訪問/イベントが無くても在籍スタッフ全員を行に出す。 */
  showAllStaff?: boolean;
  /** セルの「＋ イベント」→ 追加ダイアログ (スタッフ・日付は文脈から確定)。 */
  onAddEvent?: (staffId: string, date: Date) => void;
  /** イベント帯クリック → 編集/削除ダイアログ。 */
  onEventClick?: (ev: EventRead, staffId: string) => void;
  // ─── 週空間 A1 (weekly-space-design.md §4): コース貼り付け DnD ───
  /** `${templateId}:${weekday}` → course_id。コース帯のドラッグ元解決に使う。 */
  courseIdByTemplateWeekday?: Map<string, string>;
  /** パレット/他セルからのコースドロップ = 今週のコース担当変更 (週のみ)。 */
  onCourseDrop?: (courseId: string, staffId: string, weekday: number) => void;
  /** 訪問 1 件のドロップ = その訪問だけ担当付け替え (週空間 A2・週のみ)。 */
  onVisitDrop?: (visitId: string, staffId: string, weekday: number) => void;
  /** ドラッグ中 payload (ドロップ可能セルのハイライト用・コース/訪問共通)。 */
  activeCourseDrag?: BoardDragState | null;
  /** セル内コース帯/訪問行のドラッグ開始/終了 (パレットと同じ通知)。 */
  onCourseDragChange?: (drag: BoardDragState | null) => void;
  /**
   * コース帯の「×」= 担当解除 (未割当へ戻す・今週のみ)。PO要望 2026-08-21。
   * courseId はコース行が引けないとき null (臨時テンプレ等)。呼び出し側は
   * コース行の担当が無い/一致しないとき visitIds の個別解除でフォールバックする
   * (取込由来 = visits.primary だけで帯が出ているケースで×が無反応になる不具合対応)。
   */
  onCourseUnassign?: (args: {
    courseId: string | null;
    staffId: string;
    weekday: number;
    visitIds: string[];
  }) => void;
  /** `${staffId}:${weekday}` → 休み/時間変更 (セル網掛け + ドロップ警告の表示根拠)。 */
  offByStaffWeekday?: Map<string, WeekOverrideRead>;
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
  showAllStaff = false,
  onAddEvent,
  onEventClick,
  courseIdByTemplateWeekday,
  onCourseDrop,
  onVisitDrop,
  activeCourseDrag,
  onCourseDragChange,
  onCourseUnassign,
  offByStaffWeekday,
}: StaffWeekBoardProps) {
  // ドラッグ中に実際に重なっているセル (`${rowKey}:${wd}`)。候補セルの
  // 淡い破線に対し、重なり中のセルだけ強く光らせる (PO指摘 2026-08-21)。
  const [dragOverCell, setDragOverCell] = React.useState<string | null>(null);
  React.useEffect(() => {
    // ドラッグが終わったら残像を消す。
    if (!activeCourseDrag) setDragOverCell(null);
  }, [activeCourseDrag]);
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
      // 行の帰属は訪問自身の primary を最優先 (2026-07-26)。コース担当経由だと
      // 臨時テンプレ (臨・臨2… を束ねる) で他スタッフの訪問が混ざるため。
      const staffId =
        v.primary_staff_id ??
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
    // 職員スケジュールタブ: 空の週でも在籍スタッフ全員を行に出す
    // (空セルからイベントを登録できるように)。退職・休職は出さない。
    if (showAllStaff) {
      for (const [staffId, s] of staffMap) {
        if (s.status === 'active') rows.add(staffId);
      }
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
  }, [visits, assignedStaffByTemplateWeekday, staffEventsByStaff, staffMap, officeNameById, courseLabel, showAllStaff]);

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
                  // 週空間 A1: 休み網掛け + コースドロップ受け入れ。
                  const off =
                    rowKey !== UNASSIGNED_KEY
                      ? offByStaffWeekday?.get(`${rowKey}:${wd}`)
                      : undefined;
                  const droppable = (!!onCourseDrop || !!onVisitDrop) && rowKey !== UNASSIGNED_KEY;
                  const dropHighlight =
                    droppable && activeCourseDrag != null && activeCourseDrag.weekday === wd;
                  const cellKey = `${rowKey}:${wd}`;
                  const dragOverHere = dropHighlight && dragOverCell === cellKey;
                  return (
                    <td
                      key={wd}
                      className={[
                        'border-r border-border-subtle px-2 py-1.5 transition-colors',
                        off ? 'bg-amber-50' : '',
                        // 候補セル (同じ曜日の列) は淡い破線、重なり中は強く光らせる。
                        dropHighlight && !dragOverHere
                          ? 'bg-brand-primary/5 outline-dashed outline-1 -outline-offset-2 outline-brand-primary/40'
                          : '',
                        dragOverHere
                          ? 'bg-brand-primary/15 outline outline-2 -outline-offset-2 outline-brand-primary'
                          : '',
                      ]
                        .filter(Boolean)
                        .join(' ')}
                      data-testid={`staff-week-cell-${rowKey}-${wd}`}
                      onDragOver={
                        droppable
                          ? (e) => {
                              if (
                                !e.dataTransfer.types.includes(COURSE_DND_MIME) &&
                                !e.dataTransfer.types.includes(VISIT_DND_MIME)
                              )
                                return;
                              e.preventDefault();
                              e.dataTransfer.dropEffect = 'move';
                              setDragOverCell(cellKey);
                            }
                          : undefined
                      }
                      onDragLeave={
                        droppable
                          ? () => {
                              setDragOverCell((cur) => (cur === cellKey ? null : cur));
                            }
                          : undefined
                      }
                      onDrop={
                        droppable
                          ? (e) => {
                              setDragOverCell(null);
                              const coursePayload = onCourseDrop
                                ? readCourseDragPayload(e.dataTransfer)
                                : null;
                              if (coursePayload) {
                                e.preventDefault();
                                onCourseDrop?.(coursePayload.courseId, rowKey, wd);
                                return;
                              }
                              const visitPayload = onVisitDrop
                                ? readVisitDragPayload(e.dataTransfer)
                                : null;
                              if (visitPayload) {
                                e.preventDefault();
                                onVisitDrop?.(visitPayload.visitId, rowKey, wd);
                              }
                            }
                          : undefined
                      }
                    >
                      <div className="space-y-1.5">
                        {/* 休み/時間変更バッジ (weekly-space-design.md §4-2: 隠さず表示・貼り付け自体は止めない) */}
                        {off ? (
                          <div
                            className="inline-flex items-center rounded bg-amber-100 px-1.5 py-px text-[10px] font-bold text-amber-800"
                            title={off.note ?? undefined}
                            data-testid={`staff-week-off-${rowKey}-${wd}`}
                          >
                            {off.type}
                            {off.type === '時間変更' && off.start_time && off.end_time
                              ? ` ${off.start_time}〜${off.end_time}`
                              : ''}
                          </div>
                        ) : null}
                        {/* イベント (緑・カイポケの個別業務と同じ立ち位置)。📝 = ゼロ長メモ。
                            onEventClick があれば「正典」としてクリック編集可能 (§昇格)。 */}
                        {events.map((ev) => {
                          const isMemo = ev.start_time === ev.end_time;
                          const label = isMemo
                            ? `📝 ${ev.title || ev.type}`
                            : `${hhmm(ev.start_time)}〜${hhmm(ev.end_time)} ${ev.title || ev.type}`;
                          const chipStyle: React.CSSProperties = {
                            background: 'var(--sched-event-bg)',
                            borderColor: 'var(--sched-event-ln)',
                            borderLeftColor: 'var(--sched-event-bar)',
                            color: 'var(--sched-event-ink)',
                          };
                          const editable = onEventClick && rowKey !== UNASSIGNED_KEY;
                          return editable ? (
                            <button
                              key={`ev-${ev.id}`}
                              type="button"
                              onClick={() => onEventClick(ev, rowKey)}
                              // イベント緑トークンで統一 (PO確定 2026-07-26)。
                              className="block w-full rounded border border-l-[3px] px-1.5 py-0.5 text-left text-[11px] font-medium hover:brightness-95 focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary"
                              style={chipStyle}
                              title={ev.note ? `${ev.note}（クリックで編集）` : 'クリックで編集'}
                              data-testid={`staff-week-event-${ev.id}`}
                            >
                              {label}
                            </button>
                          ) : (
                            <div
                              key={`ev-${ev.id}`}
                              className="rounded border border-l-[3px] px-1.5 py-0.5 text-[11px] font-medium"
                              style={chipStyle}
                              title={ev.note ?? undefined}
                            >
                              {label}
                            </div>
                          );
                        })}
                        {/* コース見出し + 訪問明細 (案B: 常時表示)。
                            週空間 A1: 見出しチップは course_id が引ければドラッグ元になる
                            (別スタッフのセル/パレットへ = 今週のみの担当付替/解除)。 */}
                        {courses.map((cc) => {
                          const chipCourseId =
                            onCourseDrop && courseIdByTemplateWeekday
                              ? (courseIdByTemplateWeekday.get(`${cc.templateId}:${wd}`) ?? null)
                              : null;
                          return (
                          <div key={cc.templateId}>
                            <div className="mb-0.5 inline-flex items-center gap-0.5">
                              <div
                                className={[
                                  'inline-flex items-center rounded bg-bg-muted px-1.5 py-px text-[10px] font-bold text-text-secondary transition-opacity',
                                  chipCourseId ? 'cursor-grab active:cursor-grabbing' : '',
                                  // 持ち出し中のコースは半透明 (どれを掴んでいるか明示)。
                                  chipCourseId && activeCourseDrag?.courseId === chipCourseId
                                    ? 'opacity-40'
                                    : '',
                                ]
                                  .filter(Boolean)
                                  .join(' ')}
                                draggable={!!chipCourseId}
                                onDragStart={
                                  chipCourseId
                                    ? (e) => {
                                        const payload = {
                                          courseId: chipCourseId,
                                          weekday: wd,
                                          // パレット戻し時の個別解除フォールバック用。
                                          ...(rowKey !== UNASSIGNED_KEY
                                            ? { fromStaffId: rowKey }
                                            : {}),
                                        };
                                        e.dataTransfer.setData(
                                          COURSE_DND_MIME,
                                          JSON.stringify(payload),
                                        );
                                        e.dataTransfer.effectAllowed = 'move';
                                        applyCourseDragImage(
                                          e.dataTransfer,
                                          cc.label,
                                          `${cc.visits.length}件 — 別スタッフへ付け替え / コースの表へ戻して解除`,
                                        );
                                        onCourseDragChange?.(payload);
                                      }
                                    : undefined
                                }
                                onDragEnd={
                                  chipCourseId ? () => onCourseDragChange?.(null) : undefined
                                }
                                title={
                                  chipCourseId
                                    ? `${cc.label} — 別のスタッフのセルへドラッグで担当付け替え（今週のみ）`
                                    : undefined
                                }
                                data-testid={`staff-week-course-chip-${cc.templateId}-${wd}`}
                              >
                                {chipCourseId ? '⠿ ' : ''}
                                {cc.label}
                              </div>
                              {/* × = 担当解除 (未割当へ戻す・今週のみ)。ドラッグより
                                  直感的な「戻す」入口 (PO要望 2026-08-21)。undo は
                                  ツールバーの「戻る」でも可能。担当なし行には出さない。
                                  コース行が引けない帯 (臨時等) でも出す — 呼び出し側が
                                  訪問の個別解除でフォールバックする。 */}
                              {onCourseUnassign && rowKey !== UNASSIGNED_KEY ? (
                                <button
                                  type="button"
                                  onClick={() =>
                                    onCourseUnassign({
                                      courseId: chipCourseId,
                                      staffId: rowKey,
                                      weekday: wd,
                                      visitIds: cc.visits.map((vv) => vv.id),
                                    })
                                  }
                                  className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-red-300 bg-red-50 text-[11px] font-bold leading-none text-red-600 shadow-sm transition-colors hover:border-red-600 hover:bg-red-600 hover:text-white focus:outline-none focus-visible:ring-1 focus-visible:ring-red-500"
                                  title={`${cc.label} の担当を解除して「コースの表」へ戻す（今週のみ）`}
                                  aria-label={`${cc.label} の担当を解除`}
                                  data-testid={`staff-week-course-unassign-${cc.templateId}-${wd}`}
                                >
                                  ×
                                </button>
                              ) : null}
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
                                  // 週空間 A2: 訪問行 1 件を掴んで別スタッフへ
                                  // (患者個別の貼り替え・今週のみ)。
                                  const visitDraggable = !!onVisitDrop;
                                  return (
                                    <li
                                      key={v.id}
                                      className={[
                                        'flex items-center gap-1 rounded border border-l-[3px] px-1 py-0.5 text-[10px] text-text-primary transition-opacity',
                                        visitDraggable ? 'cursor-grab active:cursor-grabbing' : '',
                                        activeCourseDrag?.visitId === v.id ? 'opacity-40' : '',
                                      ]
                                        .filter(Boolean)
                                        .join(' ')}
                                      style={{
                                        background: pal.bg,
                                        borderColor: pal.ln,
                                        borderLeftColor: pal.bar,
                                      }}
                                      draggable={visitDraggable}
                                      onDragStart={
                                        visitDraggable
                                          ? (e) => {
                                              const payload = { visitId: v.id, weekday: wd };
                                              e.dataTransfer.setData(
                                                VISIT_DND_MIME,
                                                JSON.stringify(payload),
                                              );
                                              e.dataTransfer.effectAllowed = 'move';
                                              applyCourseDragImage(
                                                e.dataTransfer,
                                                `${v.patient_name ?? '患者'}様 ${hhmm(v.start_time)}`,
                                                'この訪問だけ別スタッフへ / コースの表へ戻して解除',
                                              );
                                              onCourseDragChange?.(payload);
                                            }
                                          : undefined
                                      }
                                      onDragEnd={
                                        visitDraggable ? () => onCourseDragChange?.(null) : undefined
                                      }
                                      title={
                                        visitDraggable
                                          ? `${v.patient_name ?? ''} — ドラッグでこの訪問だけ担当を付け替え（今週のみ）`
                                          : (v.patient_name ?? undefined)
                                      }
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
                          );
                        })}
                        {courses.length === 0 &&
                          events.length === 0 &&
                          !(onAddEvent && rowKey !== UNASSIGNED_KEY) && (
                            <span className="text-[10px] text-text-muted">—</span>
                          )}
                        {/* 正典の入口: セルの「＋ イベント」(スタッフ×日が文脈から確定)。 */}
                        {onAddEvent && rowKey !== UNASSIGNED_KEY && (
                          <button
                            type="button"
                            onClick={() => onAddEvent(rowKey, addDays(weekStart, wd))}
                            className="block w-full rounded border border-dashed border-border-default px-1 py-0.5 text-[10px] text-text-muted/70 transition-colors hover:border-brand-primary hover:text-brand-primary focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary"
                            aria-label={`${staff?.name ?? ''} ${format(addDays(weekStart, wd), 'M/d')} にイベントを追加`}
                            data-testid={`staff-week-add-${rowKey}-${wd}`}
                          >
                            ＋
                          </button>
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
