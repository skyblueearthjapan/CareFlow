'use client';

/**
 * WeekTimelineBoard — 週タイムライン (T-3・読み取り専用)。
 *
 * docs/plans/schedule-timeline-redesign-design.md / schedule-timeline-production-fit.md。
 * 原則「時間は下へ・列は比べたいもの」の週版: **列=曜日(月〜土)・縦=時間(9:00〜18:00)**。
 * 全コース (稲毛A/B/C…) を縦積みし、縦スクロールで一元閲覧する (PO要望 2026-07-08。
 * 旧: コース切替セレクタで1コース深掘り)。日タイムラインと同じ視覚言語・同じ算法
 * (行高は TL_WEEK_ROW_PX・曜日ヘッダ=性別色アバター+太字名・同住所同時刻2名=90分占有ペア)。
 *
 * 週の「全コース・全拠点の受入可能数俯瞰/開講判定」は既存の CourseWeekOverview(一覧)が
 * 担い続ける。表示専用: カードクリックで既存の患者詳細を開くのみ。API/ソルバは持たない。
 */

import { useMemo } from 'react';

import type { WeekOverviewVisit } from '@/components/schedule/v2/CourseWeekOverview';
import { CornerPushPin } from '@/components/ui/push-pin';
import { parseHM, SAME_ADDRESS_PAIR_MIN_OCCUPANCY } from '@/lib/scheduling/freeGaps';
import {
  assignLanes,
  durationToHeightScaled,
  genderPalette,
  minutesToYScaled,
  TL_DAY_END_MIN,
  TL_DAY_START_MIN,
  TL_MIN_CARD_PX,
  TL_SHOW_ADDR_PX,
  TL_SHOW_PILLS_PX,
  TL_SHOW_SVC_PX,
  TL_WEEK_ROW_PX,
} from '@/lib/scheduling/timeline';
import { cn } from '@/lib/utils';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;
const COL_MIN_W = 150;
const TIME_RAIL_W = 50;
const ROW_PX = TL_WEEK_ROW_PX; // 週も縦を圧縮せず余裕を持たせる
/** 終了時刻が無い訪問を描くときの既定所要分 (週ビューは終了を持たない訪問に寛容)。 */
const WTL_DEFAULT_SERVICE_MIN = 35;

export interface WeekTimelineOption {
  templateId: string;
  label: string; // 例: 稲毛A・田中 一郎
}

export interface WeekTimelineBoardProps {
  /** 表示する全コース (拠点順)。全コースを縦積みで一望する (PO要望・切替セレクタ廃止)。 */
  options: WeekTimelineOption[];
  /** 全曜日 × 全 template の visits (フラット)。各セクションが templateId で絞る。 */
  visits: WeekOverviewVisit[];
  /** 曜日ヘッダの日付ラベル (0=Mon..5=Sat)。省略時は曜日のみ。 */
  weekdayDates?: (string | null)[];
  /** カードクリック → 患者詳細 (既存ダイアログ)。 */
  onPatientClick?: (patientId: string) => void;
  /** 週の容量 (コース×曜日の受入可能数)。ヘッダの「n/N件」に使う。省略可。 */
  capacityByWeekday?: (templateId: string, weekday: number) => number;
  /**
   * コース×曜日の担当スタッフ (名前+性別)。曜日ごとに担当が異なり得るため関数で受け取る
   * (例: 稲毛B は 月=田中 / 火=佐藤)。性別は日ビューヘッダと同じ色付きアバターに使う。
   * null = 未割当。省略可。
   */
  staffByWeekday?: (
    templateId: string,
    weekday: number,
  ) => { name: string; sex?: string | null } | null;
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
  const e = parseHM(v.end_time) ?? (s !== null ? s + WTL_DEFAULT_SERVICE_MIN : null);
  if (s === null || e === null || e <= s) return null;
  const cs = Math.max(s, TL_DAY_START_MIN);
  const ce = Math.min(e, TL_DAY_END_MIN);
  if (ce <= cs) return null; // 9:00〜18:00 の範囲外 (幽霊カード防止・MED-1)。
  const pal = genderPalette(v.patient_sex);
  const top = minutesToYScaled(cs, ROW_PX) + 1;
  const h = Math.max(durationToHeightScaled(ce - cs, ROW_PX) - 2, TL_MIN_CARD_PX);
  const isMulti = v.patient_requires_multiple_staff === true;
  const durMin = e - s;
  // 日ビューカードと同じ条件ピル (性別制限 / 2名)。
  const pills: string[] = [];
  if (v.patient_sex_restriction === 'female_only') pills.push('女性のみ');
  if (v.patient_sex_restriction === 'male_only') pills.push('男性のみ');
  if (isMulti) pills.push('2名');
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
      title={v.patient_address ?? undefined}
      className="absolute flex flex-col gap-px rounded-md border border-l-[3px] px-1.5 py-0.5 text-left shadow-[var(--shadow-xs)] transition-shadow hover:z-[4] hover:shadow-[var(--shadow-md)] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
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
      {/* ピン留め: 右上に打ち込んだ画鋲 (📍住所と誤認しない・PO要望 2026-07-08)。 */}
      {v.is_pinned && <CornerPushPin className="h-4 w-4" />}
      {/* 1行目: アイコン + 患者名 (フル表示)。 */}
      <span className="flex min-w-0 items-center gap-0.5">
        {isMulti && (
          <span className="inline-flex shrink-0 text-brand-primary" aria-label="2名体制">
            <PersonMark />
          </span>
        )}
        <span className="truncate text-[12px] font-bold leading-tight">
          {v.patient_name ?? '—'}
        </span>
      </span>
      {/* 2行目: 時刻・所要分 (日ビューと同じ構成)。 */}
      {h >= TL_SHOW_SVC_PX && (
        <span className="tnum text-[9.5px] font-semibold opacity-75">
          {(v.start_time ?? '').slice(0, 5)}・{durMin}分
        </span>
      )}
      {/* 3行目: 📍住所 (日ビューと情報統一・30分カードから表示)。 */}
      {v.patient_address && h >= TL_SHOW_ADDR_PX && (
        <span className="flex min-w-0 items-center gap-0.5 text-[9px] opacity-75">
          <span className="shrink-0">📍</span>
          <span className="truncate">{v.patient_address}</span>
        </span>
      )}
      {/* 条件ピル (性別制限 / 2名・日ビューと同じ)。 */}
      {pills.length > 0 && h >= TL_SHOW_PILLS_PX && (
        <span className="mt-auto flex flex-wrap gap-[3px] pb-px">
          {pills.map((p) => (
            <span
              key={p}
              className="rounded-full px-1.5 py-px text-[8px] font-bold text-white"
              style={{ background: pal.bar }}
            >
              {p}
            </span>
          ))}
        </span>
      )}
    </button>
  );
}

/** 描画単位: 単独訪問 or 同住所・同時刻2名の90分占有ペア (日ビューと同じ規則)。 */
type WeekRenderItem =
  | { kind: 'single'; id: string; v: WeekOverviewVisit; startMin: number; endMin: number }
  | { kind: 'pair'; id: string; visits: WeekOverviewVisit[]; startMin: number; endMin: number };

/**
 * 同住所・同時刻の 2 名 (別患者・同 same_address_key・同 start) を 1 つの
 * 「90分占有ペア」にまとめる (日ビュー buildRenderItems と同じ規則の週版)。
 */
function buildWeekRenderItems(visits: ReadonlyArray<WeekOverviewVisit>): WeekRenderItem[] {
  const sorted = [...visits].sort(
    (a, b) => (parseHM(a.start_time) ?? 0) - (parseHM(b.start_time) ?? 0),
  );
  const used = new Set<string>();
  const items: WeekRenderItem[] = [];
  for (const v of sorted) {
    if (used.has(v.id)) continue;
    const s = parseHM(v.start_time);
    const e = parseHM(v.end_time) ?? (s !== null ? s + WTL_DEFAULT_SERVICE_MIN : null);
    if (s === null || e === null || e <= s) continue;
    const gid = v.same_address_key ?? null;
    if (gid) {
      const mate = sorted.find(
        (o) =>
          o.id !== v.id &&
          !used.has(o.id) &&
          (o.same_address_key ?? null) === gid &&
          o.patient_id !== v.patient_id &&
          parseHM(o.start_time) === s,
      );
      if (mate) {
        used.add(v.id);
        used.add(mate.id);
        const meEnd = parseHM(mate.end_time) ?? s + WTL_DEFAULT_SERVICE_MIN;
        const endMin = Math.max(s + SAME_ADDRESS_PAIR_MIN_OCCUPANCY, e, meEnd);
        items.push({
          kind: 'pair',
          id: `pair:${v.id}:${mate.id}`,
          visits: [v, mate],
          startMin: s,
          endMin,
        });
        continue;
      }
    }
    used.add(v.id);
    items.push({ kind: 'single', id: v.id, v, startMin: s, endMin: e });
  }
  return items;
}

/** 同住所ペアの90分占有ボックス (上下2段・日ビュー PairBox の週版)。 */
function WeekPairBox({
  item,
  laneInfo,
  onPatientClick,
}: {
  item: Extract<WeekRenderItem, { kind: 'pair' }>;
  laneInfo?: CardLane;
  onPatientClick?: (patientId: string) => void;
}) {
  const cs = Math.max(item.startMin, TL_DAY_START_MIN);
  const ce = Math.min(item.endMin, TL_DAY_END_MIN);
  if (ce <= cs) return null;
  const top = minutesToYScaled(cs, ROW_PX) + 1;
  const boxH = Math.max(durationToHeightScaled(ce - cs, ROW_PX) - 2, TL_MIN_CARD_PX * 2);
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
  const durMin = item.endMin - item.startMin;
  return (
    <div
      data-testid={`wtl-pair-${item.id}`}
      className="absolute z-[2] flex flex-col rounded-md border-2 border-amber-400 bg-amber-50/40 shadow-[var(--shadow-xs)]"
      style={{ top, height: boxH, ...laneStyle }}
    >
      {/* 見出し: 占有時間のみ。住所は各カード行に出す (日ビューと同じ・PO要望)。 */}
      <div className="flex min-w-0 items-center gap-1 px-1 pt-0.5 text-[8.5px] font-bold text-amber-700">
        <span className="shrink-0">📍</span>
        <span className="truncate">同住所 {durMin}分占有</span>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-0.5 px-1 pb-1">
        {item.visits.map((v) => {
          const pal = genderPalette(v.patient_sex);
          const s = parseHM(v.start_time);
          const e = parseHM(v.end_time) ?? (s !== null ? s + WTL_DEFAULT_SERVICE_MIN : null);
          const dm = s !== null && e !== null && e > s ? e - s : null;
          return (
            <button
              key={v.id}
              type="button"
              data-testid={`wtl-visit-${v.id}`}
              onClick={onPatientClick ? () => onPatientClick(v.patient_id) : undefined}
              className="relative flex min-h-0 flex-1 flex-col justify-center gap-px rounded border border-l-[3px] px-1 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
              style={{
                background: pal.bg,
                borderColor: pal.ln,
                borderLeftColor: pal.bar,
                color: pal.ink,
              }}
            >
              {v.is_pinned && <CornerPushPin className="h-3.5 w-3.5" />}
              <span className="flex min-w-0 items-center gap-1">
                <span className="truncate text-[11px] font-bold leading-tight">
                  {v.patient_name ?? '—'}
                </span>
                <span className="tnum ml-auto shrink-0 text-[8.5px] opacity-75">
                  {(v.start_time ?? '').slice(0, 5)}
                  {dm !== null ? `・${dm}分` : ''}
                </span>
              </span>
              {/* 2行目: 📍住所 (通常カードと同じ配置。同住所なので2行とも同じ住所・PO要望)。 */}
              {v.patient_address && (
                <span className="flex min-w-0 items-center gap-0.5 text-[8.5px] opacity-75">
                  <span className="shrink-0">📍</span>
                  <span className="truncate">{v.patient_address}</span>
                </span>
              )}
              {/* 3行目: 条件 (単独カードと同じ情報)。 */}
              {(v.patient_sex_restriction === 'female_only' ||
                v.patient_sex_restriction === 'male_only') && (
                <span
                  className="w-fit shrink-0 rounded-full px-1 py-px text-[7.5px] font-bold text-white"
                  style={{ background: pal.bar }}
                >
                  {v.patient_sex_restriction === 'female_only' ? '女性のみ' : '男性のみ'}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * 全コースを縦積みで一望する (PO要望 2026-07-08)。旧: コース切替セレクタで 1 コース
 * 深掘り → 新: 稲毛A/B/C… を縦に並べ、縦スクロールで一元閲覧する。
 * 各コース 1 セクション (= 旧 1 コース盤そのまま)。
 */
export function WeekTimelineBoard({
  options,
  visits,
  weekdayDates,
  onPatientClick,
  capacityByWeekday,
  staffByWeekday,
}: WeekTimelineBoardProps) {
  if (options.length === 0) {
    return (
      <div
        className="rounded-lg border border-border-default bg-bg-muted p-4 text-sm text-text-muted"
        data-testid="week-timeline-board"
      >
        表示対象コースがありません。
      </div>
    );
  }
  return (
    // pb-6: 最後のコースの 18:00 端がスクロール端と密着して「切れて見える」のを防ぐ余白。
    <div className="space-y-4 pb-6" data-testid="week-timeline-board">
      {options.map((o) => (
        <CourseWeekSection
          key={o.templateId}
          option={o}
          visits={visits}
          weekdayDates={weekdayDates}
          onPatientClick={onPatientClick}
          capacityByWeekday={capacityByWeekday}
          staffByWeekday={staffByWeekday}
        />
      ))}
    </div>
  );
}

/** 1 コースぶんの週タイムライン (曜日列 × 時間)。旧 WeekTimelineBoard の盤面そのまま。 */
function CourseWeekSection({
  option,
  visits,
  weekdayDates,
  onPatientClick,
  capacityByWeekday,
  staffByWeekday,
}: {
  option: WeekTimelineOption;
  visits: WeekOverviewVisit[];
  weekdayDates?: (string | null)[];
  onPatientClick?: (patientId: string) => void;
  capacityByWeekday?: (templateId: string, weekday: number) => number;
  staffByWeekday?: (
    templateId: string,
    weekday: number,
  ) => { name: string; sex?: string | null } | null;
}) {
  const H = height();
  const hours: number[] = [];
  for (let m = TL_DAY_START_MIN; m <= TL_DAY_END_MIN; m += 120) hours.push(m);

  // このコースの visits を曜日ごとに束ねる。
  const byWeekday = useMemo(() => {
    const map = new Map<number, WeekOverviewVisit[]>();
    for (let wd = 0; wd < 6; wd++) map.set(wd, []);
    for (const v of visits) {
      if (v.course_template_id !== option.templateId) continue;
      if (v.weekday < 0 || v.weekday > 5) continue;
      map.get(v.weekday)!.push(v);
    }
    return map;
  }, [visits, option.templateId]);

  const rows: number[] = [];
  for (let m = TL_DAY_START_MIN; m < TL_DAY_END_MIN; m += 60) rows.push(m);

  return (
    <div
      className="overflow-hidden rounded-lg border border-border-default bg-bg-base"
      data-testid={`wtl-section-${option.templateId}`}
    >
      {/* コース見出し (縦積みの区切り) */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border-default bg-bg-muted px-3 py-1.5">
        <span className="text-[12px] font-bold text-brand-primary">{option.label}</span>
        <span className="ml-auto text-[10.5px] text-text-muted">
          一週間 — 曜日を横に、時間を縦に俯瞰
        </span>
      </div>

      <div className="overflow-auto">
        {/* 曜日ヘッダ */}
        <div className="sticky top-0 z-[6] flex min-w-fit border-b border-border-default bg-bg-muted">
          <div style={{ width: TIME_RAIL_W, flex: `0 0 ${TIME_RAIL_W}px` }} />
          {WEEKDAY_LABELS.map((d, wd) => {
            const n = byWeekday.get(wd)?.length ?? 0;
            const cap = capacityByWeekday?.(option.templateId, wd);
            const staff = staffByWeekday?.(option.templateId, wd) ?? null;
            const pal = genderPalette(staff?.sex);
            return (
              <div
                key={d}
                className="border-l border-[var(--border-subtle)] px-2 py-1.5"
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
                {/* 曜日ごとの担当スタッフ: 日ビューヘッダと同じ性別色アバター + 太字名。 */}
                <div className="mt-0.5 flex items-center gap-1.5">
                  <span
                    className="grid h-6 w-6 shrink-0 place-items-center rounded-full border-[1.5px] text-[10px] font-bold"
                    style={{
                      background: pal.bg,
                      borderColor: pal.bar,
                      color: pal.ink,
                    }}
                    data-testid={`wtl-staff-avatar-${option.templateId}-${wd}`}
                  >
                    {staff?.name?.[0] ?? '—'}
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate text-[11.5px] font-bold text-text-primary">
                      {staff?.name ?? '（未割当）'}
                    </span>
                    <span className="tnum block text-[9.5px] leading-tight text-text-muted">
                      {n}
                      {cap != null ? `/${cap}` : ''}件
                    </span>
                  </span>
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
              data-testid={`wtl-col-${option.templateId}-${wd}`}
            >
              {rows.map((m) => (
                <div
                  key={m}
                  className={cn('absolute left-0 right-0 border-t border-[var(--border-default)]')}
                  style={{ top: minutesToYScaled(m, ROW_PX), height: ROW_PX * 2 }}
                />
              ))}
              {(() => {
                // 同住所・同時刻2名は90分占有ペアに束ね、描画単位 (単独/ペア) 同士で
                // 重なるときのみ左右レーンに分割 (日ビューと同じ規則)。
                const items = buildWeekRenderItems(byWeekday.get(wd) ?? []);
                const lanes = assignLanes(
                  items.map((it) => ({ id: it.id, startMin: it.startMin, endMin: it.endMin })),
                );
                return items.map((it) =>
                  it.kind === 'pair' ? (
                    <WeekPairBox
                      key={it.id}
                      item={it}
                      laneInfo={lanes.get(it.id)}
                      onPatientClick={onPatientClick}
                    />
                  ) : (
                    <WeekCard
                      key={it.id}
                      v={it.v}
                      laneInfo={lanes.get(it.id)}
                      onClick={onPatientClick ? () => onPatientClick(it.v.patient_id) : undefined}
                    />
                  ),
                );
              })()}
              <div style={{ height: H }} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
