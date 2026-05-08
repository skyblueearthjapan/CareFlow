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
}

export function CourseWeekOverview({
  templates,
  officeNameById,
  visits,
  onJumpToDay,
}: CourseWeekOverviewProps) {
  // (template_id, weekday) → visits[]
  const cellMap = React.useMemo(() => {
    const m = new Map<string, WeekOverviewVisit[]>();
    for (const v of visits) {
      const key = `${v.course_template_id}:${v.weekday}`;
      const arr = m.get(key) ?? [];
      arr.push(v);
      m.set(key, arr);
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

  // 列幅: 行ヘッダ (cource label) 144px + 各曜日 minmax(120px, 1fr).
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
                  const list = cellMap.get(`${tpl.id}:${wd}`) ?? [];
                  return (
                    <div
                      key={`c-${tpl.id}-${wd}`}
                      className={cn(
                        'border-b border-r border-border-default px-2 py-1 align-top',
                        cap === 0 ? 'bg-bg-muted/40' : 'bg-bg-base',
                      )}
                      data-testid={`course-week-overview-cell-${tpl.id}-${wd}`}
                      data-capacity={cap}
                      data-occupant-count={list.length}
                    >
                      {cap === 0 ? (
                        <span className="text-[10px] text-text-muted">休</span>
                      ) : (
                        <>
                          <div className="mb-0.5 flex items-center justify-between">
                            <span
                              className={cn(
                                'rounded px-1 text-[10px] tnum',
                                list.length >= cap
                                  ? 'bg-warning/20 text-warning'
                                  : 'bg-bg-muted text-text-muted',
                              )}
                              data-testid={`course-week-overview-capacity-${tpl.id}-${wd}`}
                            >
                              {list.length}/{cap}
                            </span>
                          </div>
                          {list.length === 0 ? (
                            <span className="text-[10px] text-text-muted">—</span>
                          ) : (
                            <ul className="space-y-0.5">
                              {list.slice(0, 5).map((v) => (
                                <li
                                  key={v.id}
                                  className="truncate text-[10px] text-text-primary"
                                  title={v.patient_name ?? v.patient_id}
                                  data-testid={`course-week-overview-name-${v.id}`}
                                >
                                  {v.patient_name ?? v.patient_id}
                                </li>
                              ))}
                              {list.length > 5 ? (
                                <li className="text-[10px] text-text-muted">
                                  …他 {list.length - 5} 名
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
