'use client';

/**
 * CourseWeekOverview — Wave 18 Phase B-6: 週間一覧ビュー.
 *
 * 全曜日 (Mon-Sat) × 全コース (template) を 1 ペインで横スクロール表示する
 * 縮小サマリ。曜日タブと別タブ「週」で切替できる。
 *
 * 表現:
 *   - 各 (template, weekday) を 1 セル = 縮小ミニテーブル化。
 *   - 各セル内: ヘッダ (label) + 患者氏名の縦リスト + 容量バッジ "x/N"。
 *   - 時刻軸は省略 (Excel の Phase B-1 メイン表は CourseDayTablePanel で参照)。
 *   - クリックで `onJumpToDay(weekday)` を呼ぶ → 親側で曜日タブに切替できる。
 */
import * as React from 'react';

import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import { capacityForWeekday, type CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { EventRead } from '@/lib/schemas/staff-events';
import type { StaffRead } from '@/lib/schemas/staff';
import { formatEventLabelLines, getStaffEventsForWeekday } from './CourseDayTable';

const WEEKDAYS = [0, 1, 2, 3, 4, 5] as const;
const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;

/** CourseWeekOverview が必要とする 1 セル分の visit 表示データ. */
export interface WeekOverviewVisit {
  id: string;
  patient_id: string;
  patient_name: string | null;
  /** 0=Mon..5=Sat. */
  weekday: number;
  /** 紐づく course_template_id. CourseDayTablePanel で逆引きして埋める。 */
  course_template_id: string;
  /** 開始時刻 ('HH:MM' or 'HH:MM:SS' or null). */
  start_time: string | null;
}

export interface CourseWeekOverviewProps {
  /** 表示対象 templates (拠点フィルタ済み). */
  templates: CourseTemplateRead[];
  /** office_id → 拠点名 lookup. 表示順は親で済ませる. */
  officeNameById: Map<string, string>;
  /** 全曜日 × 全 template に対する visits (フラットに渡す)。 */
  visits: WeekOverviewVisit[];
  /** ヘッダーセルクリック時に呼ばれる (親が `activeWeekday` を切替). */
  onJumpToDay: (weekday: number) => void;
  /**
   * Wave 28 Phase B-2/B-3: staffId → EventRead[] のマップ。
   * (template_id, weekday) ごとの担当スタッフ ID との組み合わせで event を表示する。
   */
  staffEventsByStaff?: Map<string, EventRead[]>;
  /**
   * Wave 28 Phase B-2: (template_id, weekday) → assigned_staff_id のルックアップ。
   * CourseWeekOverview は course 行を直接持たないため、親から変換済みで渡す。
   */
  assignedStaffByTemplateWeekday?: Map<string, string>;
  /**
   * Wave 32: staff_id → StaffRead のルックアップ。
   * 各セル冒頭に「担当: ○○」を表示するために使用する。
   */
  staffMap?: Map<string, StaffRead>;
  /**
   * 2026-W20: patient_id → 同住所バケット key の Map (lat/lng を 0.001 桁で
   * round した文字列). 同じ key の visit を黄色背景 + 📍 で強調する.
   * null = lat/lng 未登録 (グルーピング対象外).
   * 親 (CourseDayTablePanel) で patients から構築して渡す.
   */
  sameAddressKeyByPatientId?: Map<string, string | null>;
  /**
   * 2026-W20: 縦幅切替モード.
   * - 'compact' (既存挙動 / default): 1 セル 7 件で省略し「+N 名」表示.
   * - 'full': 省略せず全件表示 (overflow-y 自動).
   */
  displayMode?: 'compact' | 'full';
  /**
   * 患者氏名クリック時のハンドラ (患者詳細ダイアログを開く).
   * 指定された場合のみ氏名が button になる。閲覧専用ロールでも詳細は見たいので
   * canEdit に依らない。
   */
  onPatientClick?: (patientId: string) => void;
}

export function CourseWeekOverview({
  templates,
  officeNameById,
  visits,
  onJumpToDay,
  staffEventsByStaff,
  assignedStaffByTemplateWeekday,
  staffMap,
  sameAddressKeyByPatientId,
  displayMode = 'compact',
  onPatientClick,
}: CourseWeekOverviewProps) {
  // (template_id, weekday) → visits[] (start_time 昇順)
  const cellMap = React.useMemo(() => {
    const m = new Map<string, WeekOverviewVisit[]>();
    for (const v of visits) {
      const key = `${v.course_template_id}:${v.weekday}`;
      const arr = m.get(key) ?? [];
      arr.push(v);
      m.set(key, arr);
    }
    for (const [key, arr] of m.entries()) {
      m.set(
        key,
        [...arr].sort((a, b) => (a.start_time ?? '').localeCompare(b.start_time ?? '')),
      );
    }
    return m;
  }, [visits]);

  // 表示順: officeName -> label
  const sortedTemplates = React.useMemo(() => {
    return [...templates].sort((a, b) => {
      const oa = (officeNameById.get(a.office_id) ?? '').localeCompare(
        officeNameById.get(b.office_id) ?? '',
        'ja',
      );
      if (oa !== 0) return oa;
      return (a.label || '').localeCompare(b.label || '', 'ja');
    });
  }, [templates, officeNameById]);

  if (sortedTemplates.length === 0) {
    return (
      <Card className="p-4 text-sm text-text-muted" data-testid="course-week-overview-empty">
        週間表示対象のコースがありません。
      </Card>
    );
  }

  // 列幅: 行ヘッダ (course label) 144px + 各曜日 minmax(120px, 1fr).
  const gridCols = `144px repeat(${WEEKDAYS.length}, minmax(120px, 1fr))`;

  return (
    <Card className="overflow-hidden p-0" data-testid="course-week-overview">
      <div className="overflow-x-auto">
        <div
          className="grid border-b border-border-default text-[11px]"
          style={{ gridTemplateColumns: gridCols }}
        >
          {/* ヘッダー行: コーナー + 曜日ラベル */}
          <div className="border-b border-r border-border-default bg-bg-muted px-2 py-1 text-[10px] font-semibold text-text-muted">
            コース \ 曜日
          </div>
          {WEEKDAYS.map((wd) => (
            <button
              key={`h-${wd}`}
              type="button"
              onClick={() => onJumpToDay(wd)}
              className="border-b border-r border-border-default bg-bg-muted px-2 py-1 text-center text-[10px] font-semibold text-text-secondary hover:bg-brand-primary/10"
              data-testid={`course-week-overview-header-${wd}`}
              aria-label={`${WEEKDAY_LABELS[wd]}曜日タブにジャンプ`}
              title={`${WEEKDAY_LABELS[wd]}曜日タブにジャンプ`}
            >
              {WEEKDAY_LABELS[wd]}
            </button>
          ))}

          {/* 行: 各 template × 各 weekday */}
          {sortedTemplates.map((tpl) => {
            const officeName = officeNameById.get(tpl.office_id) ?? '';
            return (
              <React.Fragment key={tpl.id}>
                <div
                  className="border-b border-r border-border-default bg-bg-base px-2 py-1 text-[11px] font-semibold text-text-primary"
                  data-testid={`course-week-overview-row-header-${tpl.id}`}
                >
                  {officeName ? `${officeName}-${tpl.label}` : tpl.label}
                </div>
                {WEEKDAYS.map((wd) => {
                  const cap = capacityForWeekday(tpl, wd);
                  const visitList = cellMap.get(`${tpl.id}:${wd}`) ?? [];

                  // Wave 28 Phase B-2: 担当スタッフの event をマージ
                  const eventsMap = staffEventsByStaff ?? new Map<string, EventRead[]>();
                  const assignedStaffId =
                    assignedStaffByTemplateWeekday?.get(`${tpl.id}:${wd}`) ?? null;
                  const staffDayEvents = assignedStaffId
                    ? getStaffEventsForWeekday(assignedStaffId, wd, eventsMap)
                    : [];

                  // Wave 32: 担当スタッフ名
                  const assignedStaffName = assignedStaffId
                    ? (staffMap?.get(assignedStaffId)?.name ?? null)
                    : null;

                  // 2026-W20: cell 内で同住所グルーピングを判定する.
                  //   - 同 cell 内に同じ sameAddressKey を持つ visit が 2 件以上 → 黄色背景.
                  //   - sameAddressKey は patient_id → lat/lng bucket map (親が構築).
                  const sameAddressKeyCountInCell = new Map<string, number>();
                  if (sameAddressKeyByPatientId) {
                    for (const v of visitList) {
                      const k = sameAddressKeyByPatientId.get(v.patient_id) ?? null;
                      if (!k) continue;
                      sameAddressKeyCountInCell.set(k, (sameAddressKeyCountInCell.get(k) ?? 0) + 1);
                    }
                  }

                  // visit + event を時刻順でマージ
                  // 2026-W20: visit ラベルは患者名のみ (時刻は省略).
                  type OverviewItem =
                    | {
                        kind: 'visit';
                        id: string;
                        patient_id: string;
                        time: string | null;
                        label: string;
                        sameAddressGroup: boolean;
                      }
                    | {
                        kind: 'event';
                        id: string;
                        time: string;
                        titleLine: string;
                        timeLine: string;
                      };
                  const items: OverviewItem[] = [
                    ...visitList.map((v) => {
                      const k = sameAddressKeyByPatientId?.get(v.patient_id) ?? null;
                      const inGroup = k != null && (sameAddressKeyCountInCell.get(k) ?? 0) >= 2;
                      return {
                        kind: 'visit' as const,
                        id: v.id,
                        patient_id: v.patient_id,
                        time: v.start_time,
                        label: v.patient_name ?? v.patient_id,
                        sameAddressGroup: inGroup,
                      };
                    }),
                    ...staffDayEvents.map((e) => {
                      const lines = formatEventLabelLines(e);
                      return {
                        kind: 'event' as const,
                        id: e.id,
                        time: e.start_time,
                        titleLine: lines.title,
                        timeLine: lines.time,
                      };
                    }),
                  ].sort((a, b) => (a.time ?? '').localeCompare(b.time ?? ''));

                  // 2026-W20: 縦幅切替.
                  //  - compact: 既存どおり 7 件で切る (「+N 名」).
                  //  - full   : slice せず全件 (overflow-y-auto / max-h でスクロール可).
                  const COMPACT_LIMIT = 7;
                  const visibleItems =
                    displayMode === 'full' ? items : items.slice(0, COMPACT_LIMIT);
                  const overflowVisits =
                    displayMode === 'full' ? 0 : Math.max(0, visitList.length - COMPACT_LIMIT);

                  return (
                    <div
                      key={`c-${tpl.id}-${wd}`}
                      className={cn(
                        'border-b border-r border-border-default px-2 py-1 align-top',
                        cap === 0 ? 'bg-bg-muted/40' : 'bg-bg-base',
                      )}
                      data-testid={`course-week-overview-cell-${tpl.id}-${wd}`}
                      data-capacity={cap}
                      data-occupant-count={visitList.length}
                      data-display-mode={displayMode}
                    >
                      {cap === 0 ? (
                        <span className="text-[10px] text-text-muted">休</span>
                      ) : (
                        <>
                          {/* Wave 32: 担当スタッフ名 */}
                          <div
                            className="text-[10px] font-semibold text-text-secondary border-b border-border-default/40 pb-0.5 mb-0.5 truncate"
                            data-testid={`course-week-overview-staff-${tpl.id}-${wd}`}
                          >
                            {assignedStaffName ? `担当: ${assignedStaffName}` : '担当: 未割当'}
                          </div>
                          <div className="mb-0.5 flex items-center justify-between">
                            <span
                              className={cn(
                                'rounded px-1 text-[10px] tnum',
                                visitList.length >= cap
                                  ? 'bg-warning/20 text-warning'
                                  : 'bg-bg-muted text-text-muted',
                              )}
                              data-testid={`course-week-overview-capacity-${tpl.id}-${wd}`}
                            >
                              {visitList.length} 名 / 上限 6
                            </span>
                          </div>
                          {items.length === 0 ? (
                            <span className="text-[10px] text-text-muted">—</span>
                          ) : (
                            <ul
                              className={cn(
                                'space-y-0.5',
                                // full モード時のみ overflow を許可.
                                displayMode === 'full' && 'max-h-[480px] overflow-y-auto',
                              )}
                            >
                              {visibleItems.map((item) =>
                                item.kind === 'visit' ? (
                                  <li
                                    key={item.id}
                                    className={cn(
                                      'truncate text-[10px] text-text-primary',
                                      // 2026-W20: 同住所ペアを黄色背景 + 📍 で強調.
                                      item.sameAddressGroup &&
                                        'rounded bg-yellow-50/80 px-1 ring-1 ring-yellow-300',
                                    )}
                                    title={item.label}
                                    data-testid={`course-week-overview-name-${item.id}`}
                                    data-same-address-group={
                                      item.sameAddressGroup ? 'true' : undefined
                                    }
                                  >
                                    {item.sameAddressGroup ? (
                                      <span aria-hidden className="mr-0.5 text-yellow-700">
                                        📍
                                      </span>
                                    ) : null}
                                    {onPatientClick ? (
                                      <button
                                        type="button"
                                        className="underline-offset-2 hover:underline focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          onPatientClick(item.patient_id);
                                        }}
                                        aria-label={`${item.label} の詳細を開く`}
                                      >
                                        {item.label}
                                      </button>
                                    ) : (
                                      item.label
                                    )}
                                  </li>
                                ) : (
                                  <li
                                    key={item.id}
                                    className="text-[10px] leading-tight text-yellow-700"
                                    title={`${item.titleLine} ${item.timeLine}`}
                                    data-testid={`course-week-overview-event-${item.id}`}
                                  >
                                    <div className="truncate">{item.titleLine}</div>
                                    <div className="text-text-muted">{item.timeLine}</div>
                                  </li>
                                ),
                              )}
                              {overflowVisits > 0 ? (
                                <li className="text-[10px] text-text-muted">
                                  …他 {overflowVisits} 名
                                </li>
                              ) : null}
                            </ul>
                          )}
                        </>
                      )}
                    </div>
                  );
                })}
              </React.Fragment>
            );
          })}
        </div>
      </div>
    </Card>
  );
}
