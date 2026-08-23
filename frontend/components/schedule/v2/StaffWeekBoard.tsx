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
import type { CockpitEventRead } from '@/lib/schemas/v2/cockpit';

import { compareByStaffCode } from '@/lib/kana-sort';
import { genderPalette } from '@/lib/scheduling/timeline';

import { getStaffEventsForWeekday } from './courseGrid';
import {
  applyCourseDragImage,
  COURSE_DND_MIME,
  readCourseDragPayload,
  readVisitDragPayload,
  UNASSIGNED_ROW_KEY,
  VISIT_DND_MIME,
  type BoardDragState,
} from './courseDnd';
import {
  weekdayOfIso,
  type CockpitMarker,
  type CockpitMarkersByCell,
} from './cockpit/reconcileMarkers';
import type { TimelineVisit } from './cockpit/StaffTimelineView';
import { suggestionBadgeClass, suggestionBadgeView } from './cockpit/suggestionBadge';
import { SyncedHScroll } from '@/components/ui/synced-h-scroll';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;

export interface StaffWeekBoardProps {
  templates: CourseTemplateRead[];
  officeNameById: Map<string, string>;
  /**
   * 全曜日 × 全 template の visits (CourseWeekOverview と同じ入力 +
   * 運転席で使う `status`(今週だけ取消) / `course_label`)。
   */
  visits: TimelineVisit[];
  /** `${templateId}:${weekday}` → assigned_staff_id。 */
  assignedStaffByTemplateWeekday: Map<string, string>;
  staffMap: Map<string, StaffRead>;
  staffEventsByStaff: Map<string, CockpitEventRead[]>;
  /** 対象週の月曜。列ヘッダの日付と event の日付一致判定に使う。 */
  weekStart: Date;
  /**
   * 患者名クリック → 患者詳細。運転席では訪問行そのものが
   * `renderVisitMenu` のトリガーになるため、患者名リンクは「Shift+クリック /
   * 中クリック相当」ではなく**行内の別ボタン**として共存する。
   */
  onPatientClick?: (patientId: string) => void;
  /** 職員スケジュールタブ: 訪問/イベントが無くても在籍スタッフ全員を行に出す。 */
  showAllStaff?: boolean;
  /** セルの「＋ イベント」→ 追加ダイアログ (スタッフ・日付は文脈から確定)。 */
  onAddEvent?: (staffId: string, date: Date) => void;
  /** イベント帯クリック → 編集/削除ダイアログ。 */
  onEventClick?: (ev: CockpitEventRead, staffId: string) => void;
  /**
   * 週空間 Phase E: 訪問行を `VisitActionMenu` (ポップオーバー) で包む。
   * 盤面は API を知らないままメニューだけ親から差し込める (結線は FE-C)。
   * 省略時は行をそのまま描く (従来どおり)。
   */
  renderVisitMenu?: (
    visit: TimelineVisit,
    weekday: number,
    trigger: React.ReactElement,
  ) => React.ReactNode;
  /** セルの「🛌 休みにする」→ 代替候補パネル (急な休み)。 */
  onMarkOff?: (staffId: string, weekday: number) => void;
  /** セルの「＋訪問」→ 今週だけの訪問追加ダイアログ。 */
  onAddVisit?: (staffId: string, weekday: number) => void;
  /**
   * ●未送信 (カイポケへまだ送っていない) の訪問 id / イベント id。
   * `null` = 同期バーがまだ数えていない (ドットを出さない = 断定しない)。
   */
  unsentVisitIds?: Set<string> | null;
  unsentEventIds?: Set<string> | null;
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
  /**
   * 「（担当なし）」行を未割当ゼロでも常に出す (パレット撤去後の置き場・
   * 担当解除のドロップ先。PO判断 2026-08-21: コースの表は不要)。
   */
  alwaysShowUnassignedRow?: boolean;
  /** 「（担当なし）」行へのコース帯ドロップ = 担当解除。 */
  onCourseUnassignDrop?: (courseId: string, fromStaffId?: string) => void;
  /** 「（担当なし）」行への訪問ドロップ = その 1 件だけ担当解除。 */
  onVisitUnassignDrop?: (visitId: string) => void;
  /**
   * カイポケ突合 (C1/Phase E) のゴーストマーカー。`${staffId}:${weekday}` で引く。
   * イベントはチップ、訪問 (kind='visit') はコース枠+患者カード風に描く:
   *   before = 青点線「今ここ」 / after = 紫実線「こう変わる・ここに入る」
   *   delete = 青の打消し。SyncBar (同期バー) が選択中の 1 件を供給する。
   */
  reconcileMarkersByCell?: CockpitMarkersByCell | null;
  /**
   * 「担当なし」からの投入提案 (Phase 2-B)。キーは `${templateId}:${weekday}`
   * (`courseIdByTemplateWeekday` と同じ引き方)。
   *   `'calc'` = 問い合わせ中 / `{ok:n}` = 丸ごと引き受けられる人数。
   * 未登録のコース帯は「提案を見る」ボタンのまま (自動計算はしない)。
   */
  suggestionBadges?: Map<string, { ok: number } | 'calc'>;
  /** バッジのクリック → コース提案ポップオーバー (親が開く)。 */
  onSuggestCourse?: (courseId: string, weekday: number, anchorEl: HTMLElement) => void;
  /** 2-D: 提案の候補行を hover 中のスタッフ。その行を薄くブランド色にする。 */
  highlightStaffId?: string | null;
}

/** 「（担当なし）」行のキー (courseDnd の単一ソース)。 */
const UNASSIGNED_KEY = UNASSIGNED_ROW_KEY;

function hhmm(t: string | null | undefined): string {
  if (!t) return '';
  return t.slice(0, 5);
}

/**
 * このセル (`rowKey` × `wd`) にゴーストのどちら側を描くか。
 * `toMarkersByCell` は before/after 両方のセルに同じマーカーを置くため、
 * セル側で「自分はどちらの側か」を解き直す (両方が同セルなら 2 つ描く)。
 */
function ghostSidesFor(mk: CockpitMarker, rowKey: string, wd: number): ('before' | 'after')[] {
  const sides: ('before' | 'after')[] = [];
  if (mk.before && mk.before.staff_id === rowKey && weekdayOfIso(mk.before.date) === wd) {
    sides.push('before');
  }
  if (mk.after && mk.after.staff_id === rowKey && weekdayOfIso(mk.after.date) === wd) {
    sides.push('after');
  }
  return sides;
}

/** セル内のコース1枠 (見出し + 訪問明細)。 */
interface CellCourse {
  templateId: string;
  label: string; // 例: "稲毛A"
  visits: TimelineVisit[];
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
  alwaysShowUnassignedRow = false,
  onCourseUnassignDrop,
  onVisitUnassignDrop,
  reconcileMarkersByCell,
  renderVisitMenu,
  onMarkOff,
  onAddVisit,
  unsentVisitIds,
  unsentEventIds,
  suggestionBadges,
  onSuggestCourse,
  highlightStaffId,
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
    // パレット撤去後の置き場: 未割当ゼロでも「（担当なし）」行を常設し、
    // コース/訪問の「戻し先」(担当解除ドロップ) として機能させる。
    if (alwaysShowUnassignedRow) rows.add(UNASSIGNED_KEY);

    // 並び: スタッフコード順 (PO 要望 2026-08-23・旧: 拠点名 → 氏名)。担当なしは末尾。
    // 行の並びは固定で、タイムラインの「入れ替え」は予定だけが行き来する。
    const sorted = Array.from(rows).sort((a, b) => {
      if (a === UNASSIGNED_KEY) return 1;
      if (b === UNASSIGNED_KEY) return -1;
      const sa = staffMap.get(a);
      const sb = staffMap.get(b);
      return compareByStaffCode(
        { code: sa?.code, name: sa?.name },
        { code: sb?.code, name: sb?.name },
      );
    });
    return { cellMap: cells, rowKeys: sorted };
  }, [
    visits,
    assignedStaffByTemplateWeekday,
    staffEventsByStaff,
    staffMap,
    officeNameById,
    courseLabel,
    showAllStaff,
    alwaysShowUnassignedRow,
  ]);

  if (rowKeys.length === 0) {
    return (
      <p className="px-3 py-6 text-center text-sm text-text-muted" data-testid="staff-week-empty">
        この週にはまだ訪問がありません（「週を生成」または取り込みを実行してください）
      </p>
    );
  }

  return (
    <SyncedHScroll data-testid="staff-week-board">
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
                className={[
                  'border-b border-border-subtle align-top',
                  // 2-D: 提案の候補を hover 中 = その人の行を薄くブランド色に。
                  highlightStaffId === rowKey ? 'bg-brand-primary/10' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
                data-highlight={highlightStaffId === rowKey ? 'true' : undefined}
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
                      {onCourseUnassignDrop || onVisitUnassignDrop ? (
                        <span className="block font-normal text-text-muted">
                          ⤵ ここへドラッグで担当解除
                        </span>
                      ) : null}
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
                  // getStaffEventsForWeekday は EventRead[] を返すが、渡している配列は
                  // CockpitEventRead[] (cancelled_at/source 付き) なので絞り戻す。
                  // 盤面だけは includeCancelled=true — 「今週だけ外した」ことを
                  // 打消線で見せるのがここの仕事 (他の経路は予定から除外する)。
                  const events =
                    rowKey !== UNASSIGNED_KEY
                      ? (getStaffEventsForWeekday(
                          rowKey,
                          wd,
                          staffEventsByStaff,
                          undefined,
                          true,
                        ) as CockpitEventRead[])
                      : [];
                  // 週空間 A1: 休み網掛け + コースドロップ受け入れ。
                  const off =
                    rowKey !== UNASSIGNED_KEY
                      ? offByStaffWeekday?.get(`${rowKey}:${wd}`)
                      : undefined;
                  const assignDroppable =
                    (!!onCourseDrop || !!onVisitDrop) && rowKey !== UNASSIGNED_KEY;
                  // 「（担当なし）」行は逆向き = 担当解除のドロップ先 (戻し先)。
                  const unassignDroppable =
                    rowKey === UNASSIGNED_KEY && (!!onCourseUnassignDrop || !!onVisitUnassignDrop);
                  const droppable = assignDroppable || unassignDroppable;
                  // A2後段: 曜日跨ぎ移動に対応したため全曜日のセルがドロップ先。
                  const dropHighlight = droppable && activeCourseDrag != null;
                  const cellKey = `${rowKey}:${wd}`;
                  const dragOverHere = dropHighlight && dragOverCell === cellKey;
                  return (
                    <td
                      key={wd}
                      className={[
                        'group/cell border-r border-border-subtle px-2 py-1.5 transition-colors',
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
                              if (unassignDroppable) {
                                // 戻し先 (担当なし行): コース帯/訪問を未割当へ。
                                const cp = onCourseUnassignDrop
                                  ? readCourseDragPayload(e.dataTransfer)
                                  : null;
                                if (cp) {
                                  e.preventDefault();
                                  onCourseUnassignDrop?.(cp.courseId, cp.fromStaffId);
                                  return;
                                }
                                const vp = onVisitUnassignDrop
                                  ? readVisitDragPayload(e.dataTransfer)
                                  : null;
                                if (vp) {
                                  e.preventDefault();
                                  onVisitUnassignDrop?.(vp.visitId);
                                }
                                return;
                              }
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
                        {/* カイポケ突合 (C1/Phase E): 差分ゴースト。
                            訪問 (kind='visit') は「今ここ(青点線)→こう変わる(紫実線)」の
                            2 枚をコース枠+患者カード風に描く。イベント/旧形式は従来のチップ。 */}
                        {rowKey !== UNASSIGNED_KEY &&
                          (reconcileMarkersByCell?.get(`${rowKey}:${wd}`) ?? []).flatMap((mk) => {
                            const cockpit = mk as CockpitMarker;
                            const sides =
                              cockpit.kind === 'visit' ? ghostSidesFor(cockpit, rowKey, wd) : [];
                            if (sides.length > 0) {
                              return sides.map((side) => {
                                const s = side === 'before' ? cockpit.before! : cockpit.after!;
                                const isBefore = side === 'before';
                                const cancelled = isBefore && cockpit.action === 'delete';
                                const head = isBefore
                                  ? cancelled
                                    ? '🔵 取消（カイポケ側に無し）'
                                    : '🔵 今ここ'
                                  : cockpit.action === 'add'
                                    ? '🟣 ここに入る'
                                    : '🟣 こう変わる';
                                return (
                                  <div
                                    key={`rc-${cockpit.externalId}-${side}`}
                                    className="rounded px-1.5 py-0.5 text-[10px] font-medium"
                                    style={{
                                      border: isBefore
                                        ? '1px dashed var(--sched-ghost-before)'
                                        : '1px solid var(--sched-ghost-after)',
                                      background: isBefore
                                        ? 'var(--sched-ghost-before-bg)'
                                        : 'var(--sched-ghost-after-bg)',
                                      color: isBefore
                                        ? 'var(--sched-ghost-before)'
                                        : 'var(--sched-ghost-after)',
                                    }}
                                    title={
                                      isBefore
                                        ? 'らく助の今の予定（差分の「前」）'
                                        : 'この差分を取り込むとこうなります（差分の「後」）'
                                    }
                                    data-testid={`reconcile-ghost-${cockpit.externalId}-${side}`}
                                  >
                                    <span className="block">{head}</span>
                                    {s.course_label ? (
                                      <span className="block font-bold opacity-80">
                                        {s.course_label}
                                      </span>
                                    ) : null}
                                    <span className={cancelled ? 'block line-through' : 'block'}>
                                      <span className="tnum mr-1">
                                        {hhmm(s.start)}
                                        {s.end ? `〜${hhmm(s.end)}` : ''}
                                      </span>
                                      {cockpit.patient_name ?? cockpit.title}
                                    </span>
                                  </div>
                                );
                              });
                            }
                            const styles =
                              mk.action === 'add'
                                ? 'border-violet-400 bg-violet-50 text-violet-800'
                                : mk.action === 'update'
                                  ? 'border-amber-400 bg-amber-50 text-amber-800'
                                  : 'border-sky-400 bg-sky-50 text-sky-800';
                            const label =
                              mk.action === 'add'
                                ? `🟣 ${hhmm(mk.start)}〜${hhmm(mk.end)} ${mk.title}`
                                : mk.action === 'update'
                                  ? `🟡 ${mk.beforeStart ?? ''}→${hhmm(mk.start)}〜${hhmm(mk.end)} ${mk.title}`
                                  : `🔵 ${hhmm(mk.start)}〜${hhmm(mk.end)} ${mk.title}`;
                            return (
                              <div
                                key={`rc-${mk.externalId}`}
                                className={`rounded border border-dashed px-1.5 py-0.5 text-[10px] font-medium ${styles}`}
                                title={
                                  mk.action === 'add'
                                    ? 'カイポケにだけ存在します（突合パネルから取込できます）'
                                    : mk.action === 'update'
                                      ? 'カイポケ側と内容が違います（突合パネルから取込できます）'
                                      : 'らく助にだけ存在します（カイポケ側にありません）'
                                }
                                data-testid={`reconcile-ghost-${mk.externalId}`}
                              >
                                {label}
                              </div>
                            );
                          })}
                        {/* イベント (緑・カイポケの個別業務と同じ立ち位置)。📝 = ゼロ長メモ。
                            onEventClick があれば「正典」としてクリック編集可能 (§昇格)。 */}
                        {events.map((ev) => {
                          const isMemo = ev.start_time === ev.end_time;
                          // 今週だけ外したイベント (mig 0075) は行が残るので打消線で示す。
                          const cancelled = ev.cancelled_at != null;
                          const label = isMemo
                            ? `📝 ${ev.title || ev.type}`
                            : `${hhmm(ev.start_time)}〜${hhmm(ev.end_time)} ${ev.title || ev.type}`;
                          const chipStyle: React.CSSProperties = {
                            background: 'var(--sched-event-bg)',
                            borderColor: 'var(--sched-event-ln)',
                            borderLeftColor: 'var(--sched-event-bar)',
                            color: 'var(--sched-event-ink)',
                            ...(cancelled
                              ? { textDecoration: 'line-through', opacity: 0.6 }
                              : null),
                          };
                          const body = (
                            <>
                              {unsentEventIds?.has(ev.id) ? (
                                <span
                                  className="mr-1 inline-block h-1.5 w-1.5 rounded-full align-middle"
                                  style={{ background: 'var(--sched-unsent-dot)' }}
                                  title="カイポケへまだ送っていません（同期バーの ●未送信）"
                                  aria-label="未送信"
                                  data-testid={`staff-week-event-unsent-${ev.id}`}
                                />
                              ) : null}
                              {label}
                              {cancelled ? (
                                <span className="ml-1 rounded bg-warning-bg px-1 text-[10px] font-bold no-underline text-warning-strong">
                                  今週除外
                                </span>
                              ) : null}
                            </>
                          );
                          const editable = onEventClick && rowKey !== UNASSIGNED_KEY;
                          return editable ? (
                            <button
                              key={`ev-${ev.id}`}
                              type="button"
                              onClick={() => onEventClick(ev, rowKey)}
                              // イベント緑トークンで統一 (PO確定 2026-07-26)。
                              className="block w-full rounded border border-l-[3px] px-1.5 py-0.5 text-left text-[11px] font-medium hover:brightness-95 focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary"
                              style={chipStyle}
                              title={
                                cancelled
                                  ? '今週だけ外しています（クリックで編集）'
                                  : ev.note
                                    ? `${ev.note}（クリックで編集）`
                                    : 'クリックで編集'
                              }
                              data-testid={`staff-week-event-${ev.id}`}
                            >
                              {body}
                            </button>
                          ) : (
                            <div
                              key={`ev-${ev.id}`}
                              className="rounded border border-l-[3px] px-1.5 py-0.5 text-[11px] font-medium"
                              style={chipStyle}
                              title={ev.note ?? undefined}
                              data-testid={`staff-week-event-${ev.id}`}
                            >
                              {body}
                            </div>
                          );
                        })}
                        {/* コース見出し + 訪問明細 (案B: 常時表示)。
                            週空間 A1: 見出しチップは course_id が引ければドラッグ元になる
                            (別スタッフのセル/パレットへ = 今週のみの担当付替/解除)。 */}
                        {courses.map((cc) => {
                          const bandKey = `${cc.templateId}:${wd}`;
                          const bandCourseId = courseIdByTemplateWeekday?.get(bandKey) ?? null;
                          const chipCourseId = onCourseDrop ? bandCourseId : null;
                          // 提案バッジは「（担当なし）」行のコース帯だけ。コース行が
                          // 引けない帯 (臨時等) は assign-candidates の course_id が
                          // 作れないので出さない (訪問クリックの 1 件ずつ提案で拾う)。
                          const suggest =
                            onSuggestCourse && rowKey === UNASSIGNED_KEY && bandCourseId
                              ? suggestionBadgeView(suggestionBadges?.get(bandKey))
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
                                {/* 「担当なし」= 誰に入れられるかの提案 (Phase 2-B)。
                                    帯の右端に置く (モックの `.badge` と同じ位置)。 */}
                                {suggest && bandCourseId ? (
                                  <button
                                    type="button"
                                    disabled={suggest.busy}
                                    className={suggestionBadgeClass(suggest.tone)}
                                    onClick={(e) =>
                                      onSuggestCourse?.(bandCourseId, wd, e.currentTarget)
                                    }
                                    title={`${cc.label} を引き受けられる人を調べる（今週だけ・型は変わりません）`}
                                    data-testid={`staff-week-suggest-${cc.templateId}-${wd}`}
                                  >
                                    {suggest.label}
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
                                    // 今週だけ取消 (D1) はドラッグ不可・打消線 + 「取消」バッジ。
                                    const cancelled = v.status === 'cancelled';
                                    const visitDraggable = !!onVisitDrop && !cancelled;
                                    const row = (
                                      <li
                                        key={v.id}
                                        className={[
                                          'flex items-center gap-1 rounded border border-l-[3px] px-1 py-0.5 text-[10px] text-text-primary transition-opacity',
                                          visitDraggable
                                            ? 'cursor-grab active:cursor-grabbing'
                                            : '',
                                          renderVisitMenu && !visitDraggable
                                            ? 'cursor-pointer'
                                            : '',
                                          cancelled ? 'line-through opacity-60' : '',
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
                                          visitDraggable
                                            ? () => onCourseDragChange?.(null)
                                            : undefined
                                        }
                                        title={
                                          visitDraggable
                                            ? `${v.patient_name ?? ''} — ドラッグでこの訪問だけ担当を付け替え（今週のみ）`
                                            : (v.patient_name ?? undefined)
                                        }
                                        data-testid={`staff-week-visit-${v.id}`}
                                        // 運転席: 行そのものがメニューのトリガー。
                                        // キーボードでも開けるようボタン相当にする。
                                        {...(renderVisitMenu
                                          ? { role: 'button', tabIndex: 0 }
                                          : {})}
                                      >
                                        {/* 行頭の性別ドット (週リストと同じ視覚言語)。 */}
                                        <i
                                          className="h-1.5 w-1.5 shrink-0 rounded-full"
                                          style={{ background: pal.bar }}
                                          aria-hidden="true"
                                        />
                                        {/* ●未送信 (カイポケへまだ送っていない)。 */}
                                        {unsentVisitIds?.has(v.id) ? (
                                          <i
                                            className="h-1.5 w-1.5 shrink-0 rounded-full"
                                            style={{ background: 'var(--sched-unsent-dot)' }}
                                            title="カイポケへまだ送っていません（同期バーの ●未送信）"
                                            aria-label="未送信"
                                            data-testid={`staff-week-visit-unsent-${v.id}`}
                                          />
                                        ) : null}
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
                                              onClick={(e) => {
                                                // 行そのものが VisitActionMenu のトリガーなので、
                                                // 患者名クリックはメニューを開かず詳細だけ出す。
                                                e.stopPropagation();
                                                onPatientClick(v.patient_id);
                                              }}
                                              aria-label={`${v.patient_name ?? ''} の詳細を開く`}
                                            >
                                              {v.patient_name ?? '（無名）'}
                                            </button>
                                          ) : (
                                            <span style={sexStyle}>
                                              {v.patient_name ?? '（無名）'}
                                            </span>
                                          )}
                                        </span>
                                        {cancelled ? (
                                          <span
                                            className="shrink-0 rounded bg-error-bg px-1 text-[9px] font-bold text-error no-underline"
                                            data-testid={`staff-week-visit-cancelled-${v.id}`}
                                          >
                                            取消
                                          </span>
                                        ) : null}
                                      </li>
                                    );
                                    // 運転席: 行そのものが VisitActionMenu のトリガー。
                                    // 患者名リンク (onPatientClick) は行内の別ボタンとして共存する。
                                    return renderVisitMenu ? (
                                      <React.Fragment key={v.id}>
                                        {renderVisitMenu(v, wd, row)}
                                      </React.Fragment>
                                    ) : (
                                      row
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
                        {/* セルのアクション (運転席): 🛌休みにする / ＋訪問 / ＋イベント。
                            常時出すと盤面が賑やかになるため、hover / フォーカス時のみ
                            濃く出す (DOM には常にあるのでキーボードでも届く)。 */}
                        {rowKey !== UNASSIGNED_KEY && (onAddEvent || onMarkOff || onAddVisit) ? (
                          <div
                            className="flex flex-wrap gap-1 opacity-40 transition-opacity focus-within:opacity-100 group-hover/cell:opacity-100"
                            data-testid={`staff-week-cell-actions-${rowKey}-${wd}`}
                          >
                            {onMarkOff ? (
                              <button
                                type="button"
                                onClick={() => onMarkOff(rowKey, wd)}
                                className="rounded border border-dashed border-border-default px-1 py-0.5 text-[10px] text-text-muted/80 transition-colors hover:border-brand-primary hover:text-brand-primary focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary"
                                title={`${staff?.name ?? ''} をこの日休みにして、予定の渡し先を選びます`}
                                aria-label={`${staff?.name ?? ''} ${format(addDays(weekStart, wd), 'M/d')} を休みにする`}
                                data-testid={`staff-week-off-action-${rowKey}-${wd}`}
                              >
                                🛌 休みにする
                              </button>
                            ) : null}
                            {onAddVisit ? (
                              <button
                                type="button"
                                onClick={() => onAddVisit(rowKey, wd)}
                                className="rounded border border-dashed border-border-default px-1 py-0.5 text-[10px] text-text-muted/80 transition-colors hover:border-brand-primary hover:text-brand-primary focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary"
                                title="今週だけの訪問を追加します（毎週の型は変わりません）"
                                aria-label={`${staff?.name ?? ''} ${format(addDays(weekStart, wd), 'M/d')} に訪問を追加`}
                                data-testid={`staff-week-add-visit-${rowKey}-${wd}`}
                              >
                                ＋訪問
                              </button>
                            ) : null}
                            {onAddEvent ? (
                              <button
                                type="button"
                                onClick={() => onAddEvent(rowKey, addDays(weekStart, wd))}
                                className="rounded border border-dashed border-border-default px-1 py-0.5 text-[10px] text-text-muted/80 transition-colors hover:border-brand-primary hover:text-brand-primary focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary"
                                aria-label={`${staff?.name ?? ''} ${format(addDays(weekStart, wd), 'M/d')} にイベントを追加`}
                                data-testid={`staff-week-add-${rowKey}-${wd}`}
                              >
                                ＋イベント
                              </button>
                            ) : null}
                          </div>
                        ) : null}
                      </div>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </SyncedHScroll>
  );
}
