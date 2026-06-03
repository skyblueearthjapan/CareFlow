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
import {
  COURSE_CODES_MAX,
  capacityForWeekday,
  effectiveCapacity,
  type CourseTemplateRead,
} from '@/lib/schemas/v2/course_template';
import type { EventRead } from '@/lib/schemas/staff-events';
import type { StaffRead } from '@/lib/schemas/staff';
import type { FreeGap } from '@/lib/scheduling/freeGaps';
import { formatEventLabelLines, getStaffEventsForWeekday } from './CourseDayTable';
import { PinScopeMenu, type PinScope } from './PinScopeMenu';

const WEEKDAYS = [0, 1, 2, 3, 4, 5] as const;
const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;
// G-54: 1 スタッフあたりの受入可能患者数 (1 コース = 6 名). 受入可能数 = 出勤スタッフ全員 × 6.
const CAPACITY_PER_STAFF = 6;

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
  /**
   * Phase G-15: 性別制限 ('female_only' / 'male_only' / null).
   * 女性のみ → 患者名 赤 / 男性のみ → 青 / その他 → 標準.
   */
  patient_sex_restriction?: 'female_only' | 'male_only' | null;
  /**
   * Phase G-22: 当該 visit のソース PFV id (= 🔒 toggle 対象).
   * 指定があれば 🔒 toggle が enable.
   */
  fixed_visit_id?: string | null;
  /** Phase G-22: PFV.is_pinned のミラー値. */
  is_pinned?: boolean | null;
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
   * 患者氏名クリック時のハンドラ (患者詳細ダイアログを開く).
   * 指定された場合のみ氏名が button になる。閲覧専用ロールでも詳細は見たいので
   * canEdit に依らない。
   */
  onPatientClick?: (patientId: string) => void;
  /**
   * Phase G-22 / Phase G-47: 🔒 完全固定 toggle ハンドラ (週ビュー).
   * 指定時のみ各 visit の患者名後ろに 🔒 / 🔓 button が描画され、 click で
   * PinScopeMenu (= 曜日のみ / 全曜日) が開く.
   * visit.fixed_visit_id が無い場合は disabled.
   */
  onTogglePin?: (pfvId: string, nextPinned: boolean, scope: PinScope, patientId: string) => void;
  /**
   * スタッフ数連動 (auto-schedule 統一): (office_id, weekday) → 稼働スタッフ数.
   * A-E コースの「休」/定員はこの値で判定する
   * (`effectiveCapacity`: index < min(staffCount, courseCodesMax) なら定員6, else 0).
   * 未指定時は 0 を返すフォールバックで全 A-E が「休」になる (= API 未取得時).
   */
  staffCountFor?: (officeId: string, weekday: number) => number;
  /** A-E 発行上限 (API 由来の course_codes_max. 省略時は COURSE_CODES_MAX=5). */
  courseCodesMax?: number;
  /**
   * Phase G-53: (office_id, weekday) → 稼働マネージャー数 (role='manager').
   * 曜日ヘッダーの「拠点別 S/M」表示で M の値に使う. 未指定時は 0 扱い.
   */
  managerCountFor?: (officeId: string, weekday: number) => number;
  /**
   * Phase G-53: 曜日ヘッダーの拠点別 S/M を出す対象拠点の表示順 + 短縮ラベル.
   * 例: [{ id: <INAGE uuid>, label: '稲' }, { id: <TSUGA uuid>, label: '津' }].
   * 親 (CourseDayTablePanel) で office.code (INAGE→稲 / TSUGA→津) or name から構築.
   * 未指定時はヘッダーに拠点別 S/M を出さない (患者総数のみ).
   */
  staffSummaryOffices?: { id: string; label: string }[];
  /**
   * Phase G-55: (template_id, weekday) → 空き時間帯 (≥60分, start 昇順) の Map。
   * key 形式は `${template.id}:${weekday}` (cellMap と同形式)。
   * 親 (CourseDayTablePanel) が (course, weekday) ごとに computeFreeGaps した結果を
   * template×weekday に解決して渡す。各セルの患者名リストに時刻順 interleave で
   * 「HH:MM〜HH:MM 空き時間」マーカーが挿入される。
   * 頭数ゲート: cap - 配置済 <= 0 (満員) のセルでは時間 gap があっても出さない。
   */
  freeGapsByCell?: Map<string, FreeGap[]>;
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
  onPatientClick,
  onTogglePin,
  staffCountFor,
  courseCodesMax = COURSE_CODES_MAX,
  managerCountFor,
  staffSummaryOffices,
  freeGapsByCell,
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

  // Phase G-53: weekday → 患者総数 (全コース行合計). visits は (template, weekday)
  // でフラットに渡されるため、weekday 別に visit 件数を数えるだけで全コース合計に
  // なる. 同住所ペアも各 1 件 (= 実患者数) として数える (= visit 1 行 = 1 名).
  const patientTotalByWeekday = React.useMemo(() => {
    const m = new Map<number, number>();
    for (const v of visits) {
      m.set(v.weekday, (m.get(v.weekday) ?? 0) + 1);
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
          {WEEKDAYS.map((wd) => {
            // Phase G-53 / G-54: 患者総数 / 受入可能数 + 拠点別 S/M をヘッダーに表示.
            const patientTotal = patientTotalByWeekday.get(wd) ?? 0;
            const officePartsAll = (staffSummaryOffices ?? []).map((o) => {
              const s = staffCountFor ? staffCountFor(o.id, wd) : 0;
              const m = managerCountFor ? managerCountFor(o.id, wd) : 0;
              return { label: o.label, s, m };
            });
            // G-54: 受入可能患者数 = 出勤スタッフ全員(全拠点 S + M) × 6.
            const totalStaff = officePartsAll.reduce((acc, p) => acc + p.s + p.m, 0);
            const acceptCapacity = totalStaff * CAPACITY_PER_STAFF;
            // 当該曜日に S も M も 0 の拠点は省略 (出勤がある拠点のみ表示).
            const officeParts = officePartsAll.filter((p) => p.s > 0 || p.m > 0);
            return (
              <button
                key={`h-${wd}`}
                type="button"
                onClick={() => onJumpToDay(wd)}
                className="flex flex-col items-center gap-0.5 border-b border-r border-border-default bg-bg-muted px-2 py-1 text-center text-[10px] font-semibold text-text-secondary hover:bg-brand-primary/10"
                data-testid={`course-week-overview-header-${wd}`}
                aria-label={`${WEEKDAY_LABELS[wd]}曜日タブにジャンプ (患者 ${patientTotal} / 受入 ${acceptCapacity} 名)`}
                title={`${WEEKDAY_LABELS[wd]}曜日タブにジャンプ`}
              >
                <span>{WEEKDAY_LABELS[wd]}</span>
                <span
                  className="flex flex-col items-center gap-0 text-[9px] font-normal leading-tight text-text-muted"
                  data-testid={`weekday-header-totals-${wd}`}
                >
                  <span className="tnum" data-testid={`weekday-header-patients-${wd}`}>
                    患者 {patientTotal} / {acceptCapacity} 名
                  </span>
                  {officeParts.length > 0 ? (
                    <span className="tnum" data-testid={`weekday-header-staff-${wd}`}>
                      {officeParts
                        .map((p) =>
                          p.m > 0 ? `${p.label} S:${p.s} M:${p.m}` : `${p.label} S:${p.s}`,
                        )
                        .join(' / ')}
                    </span>
                  ) : null}
                </span>
              </button>
            );
          })}

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
                  // スタッフ数連動 (auto-schedule 統一): staffCountFor が渡されたときのみ
                  // A-E を staff_count で開講判定 (M系は静的 capacity). staffCountFor が
                  // 無い (= 旧呼出 / API 未配線) 場合は従来通り静的 capacity を使う.
                  const cap = staffCountFor
                    ? effectiveCapacity(tpl, wd, staffCountFor(tpl.office_id, wd), courseCodesMax)
                    : capacityForWeekday(tpl, wd);
                  const visitList = cellMap.get(`${tpl.id}:${wd}`) ?? [];

                  // Wave 28 Phase B-2: 担当スタッフの event をマージ
                  const eventsMap = staffEventsByStaff ?? new Map<string, EventRead[]>();
                  const assignedStaffId =
                    assignedStaffByTemplateWeekday?.get(`${tpl.id}:${wd}`) ?? null;
                  const staffDayEvents = assignedStaffId
                    ? getStaffEventsForWeekday(assignedStaffId, wd, eventsMap)
                    : [];

                  // Wave 32: 担当スタッフ名
                  const assignedStaff = assignedStaffId
                    ? (staffMap?.get(assignedStaffId) ?? null)
                    : null;
                  const assignedStaffName = assignedStaff?.name ?? null;
                  // Phase G-16: 担当スタッフ性別色 (女性=赤 / 男性=青)
                  const staffSexStyle: React.CSSProperties =
                    assignedStaff?.sex === 'female'
                      ? { color: '#dc2626', fontWeight: 700 }
                      : assignedStaff?.sex === 'male'
                        ? { color: '#2563eb', fontWeight: 700 }
                        : {};

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
                  // 2026-W20 後期: visit にはペア cluster 情報 (groupKey) を持たせ、
                  //   連続する同 groupKey を 1 つの「📍 同住所」囲みで包む.
                  type OverviewItem =
                    | {
                        kind: 'visit';
                        id: string;
                        patient_id: string;
                        time: string | null;
                        label: string;
                        /**
                         * 同住所グループキー (lat/lng bucket). cell 内に同 key が 2 件以上
                         * 存在する visit にだけ非 null. pair 囲み判定で連続性チェックに使う.
                         */
                        groupKey: string | null;
                        /** Phase G-15: 性別制限. female_only=赤 / male_only=青. */
                        sexRestriction: 'female_only' | 'male_only' | null;
                        /** Phase G-22: 🔒 toggle 対象 PFV id. null = weekly_pattern 由来. */
                        fixedVisitId: string | null;
                        /** Phase G-22: PFV.is_pinned. */
                        isPinned: boolean;
                      }
                    | {
                        kind: 'event';
                        id: string;
                        time: string;
                        titleLine: string;
                        timeLine: string;
                      }
                    | {
                        // Phase G-55: 空き時間帯 (≥60分) マーカー。頭数ゲート (満員=非表示)
                        // を通過したときのみ items に含める。pair cluster には属さない (single 扱い)。
                        kind: 'gap';
                        id: string;
                        time: string;
                        label: string;
                        startMin: number;
                      };
                  // Phase G-55: 頭数ゲート — cap - 配置済 <= 0 (満員) なら gap を出さない。
                  const remainingForGaps = Math.max(0, cap - visitList.length);
                  const cellGaps =
                    remainingForGaps > 0 ? (freeGapsByCell?.get(`${tpl.id}:${wd}`) ?? []) : [];
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
                        groupKey: inGroup ? k : null,
                        sexRestriction: v.patient_sex_restriction ?? null,
                        fixedVisitId: v.fixed_visit_id ?? null,
                        isPinned: v.is_pinned === true,
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
                    ...cellGaps.map((g) => ({
                      kind: 'gap' as const,
                      id: `gap-${g.startMin}`,
                      // 開始時刻を 'HH:MM' に正規化して既存の time 文字列比較ソートに乗せる。
                      time: g.label.slice(0, 5),
                      label: g.label,
                      startMin: g.startMin,
                    })),
                  ].sort((a, b) => (a.time ?? '').localeCompare(b.time ?? ''));

                  // 2026-W20 後期: 連続する同 groupKey の visit を pair cluster に集約.
                  //   - event 要素は cluster を跨がない (= 分割点になる).
                  //   - 同 groupKey でも 1 件しかない / 不連続 → single.
                  //   - 3 名以上の同 groupKey → 先頭 2 名のみペア、残りは single (BE H2 漏れ視覚化).
                  type OverviewCluster =
                    | {
                        kind: 'pair';
                        groupKey: string;
                        visits: Extract<OverviewItem, { kind: 'visit' }>[];
                      }
                    | { kind: 'single'; item: OverviewItem };
                  const clusters: OverviewCluster[] = [];
                  {
                    let j = 0;
                    while (j < items.length) {
                      const cur = items[j]!;
                      if (
                        cur.kind === 'visit' &&
                        cur.groupKey &&
                        j + 1 < items.length &&
                        items[j + 1]!.kind === 'visit' &&
                        (items[j + 1] as Extract<OverviewItem, { kind: 'visit' }>).groupKey ===
                          cur.groupKey
                      ) {
                        clusters.push({
                          kind: 'pair',
                          groupKey: cur.groupKey,
                          visits: [cur, items[j + 1] as Extract<OverviewItem, { kind: 'visit' }>],
                        });
                        j += 2;
                      } else {
                        clusters.push({ kind: 'single', item: cur });
                        j += 1;
                      }
                    }
                  }

                  // 2026-W20 改: 「全員表示」モードのみ.
                  //   全 cluster を slice せず描画 (overflow-y-auto / max-h でスクロール可).
                  const visibleClusters: OverviewCluster[] = clusters;

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
                    >
                      {cap === 0 ? (
                        <span className="text-[10px] text-text-muted">休</span>
                      ) : (
                        <>
                          {/* Wave 32: 担当スタッフ名 (Phase G-16: 性別色付け) */}
                          <div
                            className="text-[10px] border-b border-border-default/40 pb-0.5 mb-0.5 truncate"
                            data-testid={`course-week-overview-staff-${tpl.id}-${wd}`}
                          >
                            {assignedStaffName ? (
                              <>
                                <span className="font-semibold text-text-secondary">担当: </span>
                                <span style={staffSexStyle}>{assignedStaffName}</span>
                              </>
                            ) : (
                              <span className="font-semibold text-text-secondary">
                                担当: 未割当
                              </span>
                            )}
                          </div>
                          <div className="mb-0.5 flex items-center justify-between gap-1">
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
                            {/* Phase G-55: 頭数の空き枠 (= cap - 配置済). remaining>0 のとき
                                「残N枠」小バッジを teal 系で出す。満員 (<=0) は出さない
                                (= 上の容量バッジが warning 色になり満員を示す)。 */}
                            {(() => {
                              const remaining = Math.max(0, cap - visitList.length);
                              return remaining > 0 ? (
                                <span
                                  className="rounded bg-brand-primary/10 px-1 text-[10px] font-semibold tnum text-brand-primary"
                                  data-testid={`course-week-overview-remaining-${tpl.id}-${wd}`}
                                  data-remaining={remaining}
                                  title={`空き ${remaining} 枠`}
                                >
                                  残{remaining}枠
                                </span>
                              ) : null;
                            })()}
                          </div>
                          {items.length === 0 ? (
                            <span className="text-[10px] text-text-muted">—</span>
                          ) : (
                            <ul className="space-y-0.5 max-h-[480px] overflow-y-auto">
                              {visibleClusters.map((cluster, ci) => {
                                if (cluster.kind === 'single') {
                                  const item = cluster.item;
                                  if (item.kind === 'gap') {
                                    // Phase G-55: 空き時間帯マーカー (週ビュー セル内 時刻順)。
                                    // 親機意匠: 薄い teal 背景 + amber 左ボーダー + 等幅時刻。
                                    return (
                                      <li
                                        key={item.id}
                                        className="rounded border-l-2 border-amber-500 bg-brand-primary/5 px-1 py-0.5 text-[9px] font-medium text-brand-primary"
                                        data-testid={`course-week-overview-free-gap-${tpl.id}-${wd}-${item.startMin}`}
                                        data-free-gap-start={item.startMin}
                                        title={`空き時間帯 ${item.label}`}
                                      >
                                        <span className="tnum">{item.label}</span>
                                        <span className="ml-1 text-text-secondary">空き時間</span>
                                      </li>
                                    );
                                  }
                                  return item.kind === 'visit' ? (
                                    <li
                                      key={item.id}
                                      className="truncate text-[10px] text-text-primary"
                                      title={item.label}
                                      data-testid={`course-week-overview-name-${item.id}`}
                                    >
                                      {/*
                                        2026-W20 後期 C: 週ビューは情報を最小限に絞る.
                                        表示: 患者名 (click 可) + 開始時刻のみ.
                                        非表示: 実動時間 / time_type / sex_restriction / 距離 / 住所詳細.
                                      */}
                                      {item.time ? (
                                        <span className="mr-1 tnum text-text-muted">
                                          {item.time.slice(0, 5)}
                                        </span>
                                      ) : null}
                                      {(() => {
                                        // Phase G-15: 性別制限色
                                        const sexStyle: React.CSSProperties =
                                          item.sexRestriction === 'female_only'
                                            ? { color: '#dc2626', fontWeight: 600 }
                                            : item.sexRestriction === 'male_only'
                                              ? { color: '#2563eb', fontWeight: 600 }
                                              : {};
                                        return onPatientClick ? (
                                          <button
                                            type="button"
                                            className="underline-offset-2 hover:underline focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary"
                                            style={sexStyle}
                                            onClick={(e) => {
                                              e.stopPropagation();
                                              onPatientClick(item.patient_id);
                                            }}
                                            aria-label={`${item.label} の詳細を開く`}
                                          >
                                            {item.label}
                                          </button>
                                        ) : (
                                          <span style={sexStyle}>{item.label}</span>
                                        );
                                      })()}
                                      {/* Phase G-22 / G-47: 🔒 完全固定 toggle (週ビュー) — scope 選択メニュー化 */}
                                      {onTogglePin && (
                                        <PinIconButton
                                          fixedVisitId={item.fixedVisitId}
                                          isPinned={item.isPinned}
                                          label={item.label}
                                          patientId={item.patient_id}
                                          onTogglePin={onTogglePin}
                                          testIdPrefix="course-week-overview-pin"
                                          visitId={item.id}
                                        />
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
                                  );
                                }
                                // pair cluster — 2 名を 1 つの黄色囲みで wrap.
                                return (
                                  <li
                                    key={`pair-${cluster.groupKey}-${ci}`}
                                    className="rounded-md border-2 border-yellow-400 bg-yellow-50/60 px-1 py-1"
                                    data-testid={`course-week-overview-pair-${cluster.groupKey}`}
                                    data-same-address-group-key={cluster.groupKey}
                                    data-pair-size={cluster.visits.length}
                                  >
                                    <div className="mb-0.5 text-[9px] font-semibold text-yellow-700">
                                      📍 同住所 ({cluster.visits.length} 名)
                                    </div>
                                    <ul className="divide-y divide-yellow-200/70">
                                      {cluster.visits.map((v) => (
                                        <li
                                          key={v.id}
                                          className="truncate py-0.5 text-[10px] text-text-primary"
                                          title={v.label}
                                          data-testid={`course-week-overview-name-${v.id}`}
                                          data-same-address-group="true"
                                        >
                                          {v.time ? (
                                            <span className="mr-1 tnum text-text-muted">
                                              {v.time.slice(0, 5)}
                                            </span>
                                          ) : null}
                                          {(() => {
                                            // Phase G-15: 性別制限色 (pair cluster 内)
                                            const sexStyle: React.CSSProperties =
                                              v.sexRestriction === 'female_only'
                                                ? { color: '#dc2626', fontWeight: 600 }
                                                : v.sexRestriction === 'male_only'
                                                  ? { color: '#2563eb', fontWeight: 600 }
                                                  : {};
                                            return onPatientClick ? (
                                              <button
                                                type="button"
                                                className="underline-offset-2 hover:underline focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary"
                                                style={sexStyle}
                                                onClick={(e) => {
                                                  e.stopPropagation();
                                                  onPatientClick(v.patient_id);
                                                }}
                                                aria-label={`${v.label} の詳細を開く`}
                                              >
                                                {v.label}
                                              </button>
                                            ) : (
                                              <span style={sexStyle}>{v.label}</span>
                                            );
                                          })()}
                                          {/* Phase G-22 / G-47: 🔒 完全固定 toggle (週ビュー pair cluster) — scope 選択メニュー化 */}
                                          {onTogglePin && (
                                            <PinIconButton
                                              fixedVisitId={v.fixedVisitId}
                                              isPinned={v.isPinned}
                                              label={v.label}
                                              patientId={v.patient_id}
                                              onTogglePin={onTogglePin}
                                              testIdPrefix="course-week-overview-pin"
                                              visitId={v.id}
                                            />
                                          )}
                                        </li>
                                      ))}
                                    </ul>
                                  </li>
                                );
                              })}
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

// ─────────────────────────────────────────────────────────────────────────
// Phase G-22: PinIconButton (週ビュー 用)
// ─────────────────────────────────────────────────────────────────────────

interface PinIconButtonProps {
  fixedVisitId: string | null;
  isPinned: boolean;
  label: string;
  /** Phase G-47: bulk (全曜日) スコープ選択時に patient の全 PFV 対象として渡る. */
  patientId: string;
  visitId: string;
  testIdPrefix: string;
  onTogglePin: (pfvId: string, nextPinned: boolean, scope: PinScope, patientId: string) => void;
}

function PinIconButton({
  fixedVisitId,
  isPinned,
  label,
  patientId,
  visitId,
  testIdPrefix,
  onTogglePin,
}: PinIconButtonProps) {
  const disabled = !fixedVisitId;
  // Phase G-47: click 即時 toggle を廃し、 PinScopeMenu で「曜日のみ / 全曜日」 2 択を提示.
  return (
    <PinScopeMenu
      pfvId={fixedVisitId ?? ''}
      patientId={patientId}
      isPinned={isPinned}
      onSelect={onTogglePin}
      disabled={disabled}
      testIdSuffix={`${testIdPrefix}-${visitId}`}
    >
      {({ isOpen }) => (
        <button
          type="button"
          onPointerDown={(e) => e.stopPropagation()}
          disabled={disabled}
          aria-label={
            isPinned
              ? `${label} の完全固定スコープを選択 (解除)`
              : disabled
                ? `${label} は固定枠が無いため完全固定できません`
                : `${label} の完全固定スコープを選択 (ロック)`
          }
          title={
            disabled
              ? '先に固定枠登録が必要'
              : isPinned
                ? '完全固定の解除スコープを選択 (この曜日のみ / 全曜日)'
                : '完全固定のロックスコープを選択 (この曜日のみ / 全曜日)'
          }
          aria-pressed={isPinned}
          aria-haspopup="menu"
          aria-expanded={isOpen}
          data-testid={`${testIdPrefix}-${visitId}`}
          data-pinned={isPinned ? 'true' : 'false'}
          data-pfv-id={fixedVisitId ?? ''}
          className={cn(
            'ml-1 inline-flex items-center justify-center rounded px-0.5 text-[9px] leading-none align-middle',
            isPinned ? 'text-yellow-700 hover:bg-yellow-100' : 'text-text-muted hover:bg-bg-muted',
            disabled ? 'cursor-not-allowed opacity-30' : '',
          )}
        >
          <span aria-hidden="true">{isPinned ? '🔒' : '🔓'}</span>
        </button>
      )}
    </PinScopeMenu>
  );
}
