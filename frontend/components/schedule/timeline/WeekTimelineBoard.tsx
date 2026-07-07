'use client';

/**
 * WeekTimelineBoard — 週タイムライン (T-3・読み取り専用)。
 *
 * docs/plans/schedule-timeline-redesign-design.md / schedule-timeline-production-fit.md。
 * 原則「時間は下へ・列は比べたいもの」の週版: **列=曜日(月〜土)・縦=時間(9:00〜18:00)**、
 * 1コースを選んで一週間の形と余白を俯瞰する。日タイムラインと同じ視覚言語・同じ算法
 * (行高は TL_WEEK_ROW_PX。縦は圧縮せず画面の高さをいっぱいに使う)。
 *
 * 週の「全コース・全拠点の受入可能数俯瞰/開講判定」は既存の CourseWeekOverview(一覧)が
 * 担い続ける。本コンポーネントは「1コースの深掘り」に徹する (2枚持ち)。
 * 表示専用: カードクリックで既存の患者詳細を開くのみ。API/ソルバは持たない。
 */

import { useMemo } from 'react';

import type { WeekOverviewVisit } from '@/components/schedule/v2/CourseWeekOverview';
import { parseHM } from '@/lib/scheduling/freeGaps';
import {
  assignLanes,
  durationToHeightScaled,
  genderPalette,
  minutesToYScaled,
  TL_DAY_END_MIN,
  TL_DAY_START_MIN,
  TL_MIN_CARD_PX,
  TL_SHOW_SVC_PX,
  TL_WEEK_ROW_PX,
} from '@/lib/scheduling/timeline';
import { cn } from '@/lib/utils';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;
const COL_MIN_W = 150;
const TIME_RAIL_W = 50;
const ROW_PX = TL_WEEK_ROW_PX; // 週も縦を圧縮せず余裕を持たせる

export interface WeekTimelineOption {
  templateId: string;
  label: string; // 例: 稲毛A・田中 一郎
}

export interface WeekTimelineBoardProps {
  /** 選択中コースの course_template_id。 */
  selectedTemplateId: string;
  options: WeekTimelineOption[];
  onSelectTemplate: (templateId: string) => void;
  /** 全曜日 × 全 template の visits (フラット)。本盤は selectedTemplateId で絞る。 */
  visits: WeekOverviewVisit[];
  /** 曜日ヘッダの日付ラベル (0=Mon..5=Sat)。省略時は曜日のみ。 */
  weekdayDates?: (string | null)[];
  /** カードクリック → 患者詳細 (既存ダイアログ)。 */
  onPatientClick?: (patientId: string) => void;
  /** 週の容量 (曜日ごとの受入可能数)。ヘッダの「n/N件」に使う。省略可。 */
  capacityByWeekday?: (weekday: number) => number;
}

function PersonMark() {
  return (
    <svg viewBox="0 0 10 10" width="8" height="8" aria-hidden="true" className="inline-block">
      <circle cx="5" cy="3" r="2.1" fill="currentColor" />
      <path d="M1.2 9.4c.5-2 2-3 3.8-3s3.3 1 3.8 3z" fill="currentColor" />
    </svg>
  );
}

function height(): number {
  return ((TL_DAY_END_MIN - TL_DAY_START_MIN) / 30) * ROW_PX;
}

interface CardLane {
  lane: number;
  laneCount: number;
}

function WeekCard({
  v,
  onClick,
  laneInfo,
}: {
  v: WeekOverviewVisit;
  onClick?: () => void;
  laneInfo?: CardLane;
}) {
  const s = parseHM(v.start_time);
  // 終了が無い場合は既定 35 分で描く (週ビューは終了を持たない訪問もあるため寛容)。
  const e = parseHM(v.end_time) ?? (s !== null ? s + 35 : null);
  if (s === null || e === null || e <= s) return null;
  const cs = Math.max(s, TL_DAY_START_MIN);
  const ce = Math.min(e, TL_DAY_END_MIN);
  if (ce <= cs) return null; // 9:00〜18:00 の範囲外 (幽霊カード防止・MED-1)。
  const pal = genderPalette(v.patient_sex);
  const top = minutesToYScaled(cs, ROW_PX) + 1;
  const h = Math.max(durationToHeightScaled(ce - cs, ROW_PX) - 2, TL_MIN_CARD_PX);
  const isMulti = v.patient_requires_multiple_staff === true;
  // 重なり時のみ左右に分割 (MED-2)。laneCount=1 は全幅。
  const lanes = laneInfo?.laneCount ?? 1;
  const lane = laneInfo?.lane ?? 0;
  const laneStyle =
    lanes > 1
      ? {
          left: `calc(2px + ${(lane / lanes) * 100}% - ${(lane / lanes) * 4}px)`,
          width: `calc(${100 / lanes}% - ${4 / lanes}px)`,
          right: 'auto' as const,
        }
      : { left: '2px', right: '2px' };
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={`wtl-visit-${v.id}`}
      className="absolute flex flex-col gap-px overflow-hidden rounded-md border border-l-[3px] px-1.5 py-0.5 text-left shadow-[var(--shadow-xs)] transition-shadow hover:z-[4] hover:shadow-[var(--shadow-md)] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
      style={{
        top,
        height: h,
        ...laneStyle,
        background: pal.bg,
        borderColor: pal.ln,
        borderLeftColor: pal.bar,
        color: pal.ink,
      }}
    >
      {/* 1行目: アイコン + 患者名 (フル表示)。 */}
      <span className="flex min-w-0 items-center gap-0.5">
        {v.is_pinned && (
          <span className="shrink-0 text-[9px]" aria-label="ピン留め">
            🔒
          </span>
        )}
        {isMulti && (
          <span className="inline-flex shrink-0 text-brand-primary" aria-label="2名体制">
            <PersonMark />
          </span>
        )}
        <span className="truncate text-[12px] font-bold leading-tight">
          {v.patient_name ?? '—'}
        </span>
      </span>
      {/* 2行目: 時刻 (高さがあるとき)。 */}
      {h >= TL_SHOW_SVC_PX && (
        <span className="tnum text-[9.5px] font-semibold opacity-75">
          {(v.start_time ?? '').slice(0, 5)}
        </span>
      )}
    </button>
  );
}

export function WeekTimelineBoard({
  selectedTemplateId,
  options,
  onSelectTemplate,
  visits,
  weekdayDates,
  onPatientClick,
  capacityByWeekday,
}: WeekTimelineBoardProps) {
  const H = height();
  const hours: number[] = [];
  for (let m = TL_DAY_START_MIN; m <= TL_DAY_END_MIN; m += 120) hours.push(m);

  // selectedTemplate の visits を曜日ごとに束ねる。
  const byWeekday = useMemo(() => {
    const map = new Map<number, WeekOverviewVisit[]>();
    for (let wd = 0; wd < 6; wd++) map.set(wd, []);
    for (const v of visits) {
      if (v.course_template_id !== selectedTemplateId) continue;
      if (v.weekday < 0 || v.weekday > 5) continue;
      map.get(v.weekday)!.push(v);
    }
    return map;
  }, [visits, selectedTemplateId]);

  const rows: number[] = [];
  for (let m = TL_DAY_START_MIN; m < TL_DAY_END_MIN; m += 60) rows.push(m);

  const selected = options.find((o) => o.templateId === selectedTemplateId);

  return (
    <div
      className="overflow-hidden rounded-lg border border-border-default bg-bg-base"
      data-testid="week-timeline-board"
    >
      {/* コース選択 (縦スペースを消費しない薄い1行) */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border-default bg-bg-muted px-3 py-1.5">
        <span className="text-[10.5px] font-bold text-text-muted">コース</span>
        <select
          value={selectedTemplateId}
          onChange={(ev) => onSelectTemplate(ev.target.value)}
          data-testid="week-timeline-course-select"
          className="rounded-md border border-border-default bg-bg-base px-2.5 py-1 text-xs font-bold text-brand-primary focus:border-brand-primary focus:outline-none focus:ring-2 focus:ring-brand-primary-light"
        >
          {options.map((o) => (
            <option key={o.templateId} value={o.templateId}>
              {o.label}
            </option>
          ))}
        </select>
        <span className="ml-auto text-[10.5px] text-text-muted">
          {selected?.label ?? ''} の一週間 — 曜日を横に、時間を縦に俯瞰
        </span>
      </div>

      <div className="overflow-auto">
        {/* 曜日ヘッダ */}
        <div className="sticky top-0 z-[6] flex min-w-fit border-b border-border-default bg-bg-muted">
          <div style={{ width: TIME_RAIL_W, flex: `0 0 ${TIME_RAIL_W}px` }} />
          {WEEKDAY_LABELS.map((d, wd) => {
            const n = byWeekday.get(wd)?.length ?? 0;
            const cap = capacityByWeekday?.(wd);
            return (
              <div
                key={d}
                className="border-l border-[var(--border-subtle)] px-2.5 py-1.5"
                style={{ flex: 1, minWidth: COL_MIN_W }}
              >
                <div className="text-[12px] font-bold text-text-primary">
                  {d}
                  {weekdayDates?.[wd] && (
                    <span className="tnum ml-1.5 text-[10px] font-medium text-text-muted">
                      {weekdayDates[wd]}
                    </span>
                  )}
                </div>
                <div className="tnum text-[9.5px] text-text-muted">
                  {n}
                  {cap != null ? `/${cap}` : ''}件
                </div>
              </div>
            );
          })}
        </div>

        {/* 本体 */}
        <div className="flex min-w-fit">
          <div style={{ width: TIME_RAIL_W, flex: `0 0 ${TIME_RAIL_W}px` }} className="relative">
            {hours.map((m) => (
              <span
                key={m}
                className="tnum absolute right-2 -translate-y-[7px] text-[10px] text-text-muted"
                style={{ top: minutesToYScaled(m, ROW_PX) }}
              >
                {String(Math.floor(m / 60)).padStart(2, '0')}:{String(m % 60).padStart(2, '0')}
              </span>
            ))}
            <div style={{ height: H }} />
          </div>

          {WEEKDAY_LABELS.map((d, wd) => (
            <div
              key={d}
              className="relative border-l border-[var(--border-subtle)]"
              style={{ flex: 1, minWidth: COL_MIN_W }}
              data-testid={`wtl-col-${wd}`}
            >
              {rows.map((m) => (
                <div
                  key={m}
                  className={cn('absolute left-0 right-0 border-t border-[var(--border-default)]')}
                  style={{ top: minutesToYScaled(m, ROW_PX), height: ROW_PX * 2 }}
                />
              ))}
              {(() => {
                const dayVisits = (byWeekday.get(wd) ?? [])
                  .slice()
                  .sort((a, b) => (parseHM(a.start_time) ?? 0) - (parseHM(b.start_time) ?? 0));
                // 重なり訪問を左右レーンに分割 (相互に隠さない・MED-2)。
                const lanes = assignLanes(
                  dayVisits
                    .map((v) => {
                      const s = parseHM(v.start_time);
                      const e = parseHM(v.end_time) ?? (s !== null ? s + 35 : null);
                      return s !== null && e !== null && e > s
                        ? { id: v.id, startMin: s, endMin: e }
                        : null;
                    })
                    .filter(
                      (b): b is { id: string; startMin: number; endMin: number } => b !== null,
                    ),
                );
                return dayVisits.map((v) => (
                  <WeekCard
                    key={v.id}
                    v={v}
                    laneInfo={lanes.get(v.id)}
                    onClick={onPatientClick ? () => onPatientClick(v.patient_id) : undefined}
                  />
                ));
              })()}
              <div style={{ height: H }} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
