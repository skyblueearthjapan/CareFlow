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
import { MovabilityMark } from './MovabilityMark';
import {
  isDivergedFromMaster,
  masterDivergenceCardStyle,
  masterDivergenceTitle,
  masterTimeSuffix,
} from './masterDivergence';
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

import type { AccompanimentBinding } from './accompaniment/types';
import type { StaffEventFrame } from './TimelineDayBoard';

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
  /**
   * イベントセクション (PO確定 2026-07-26): その日コースを持たないがイベントの
   * あるスタッフの枠 (weekday → frames)。先頭に「イベント」セクションとして描く。
   */
  eventFramesByWeekday?: Map<number, StaffEventFrame[]>;
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
  /**
   * 新人同行 (§7.1/§7.2)。指定時、active ならコース列ヘッダ/カードが選択トグルになり
   * (通常クリックは親が抑止)、inactive なら常時表示バッジを描く。
   */
  accompaniment?: AccompanimentBinding;
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
  accompaniment,
}: {
  v: WeekOverviewVisit;
  onClick?: () => void;
  laneInfo?: CardLane;
  accompaniment?: AccompanimentBinding;
}) {
  const accActive = accompaniment?.active === true;
  const accSelected = accActive && accompaniment!.isVisitSelected(v.id);
  const accInCourse = accActive && accompaniment!.isVisitInSelectedCourse(v.id);
  const accOverlap = accActive && accompaniment!.isVisitOverlapping(v.id);
  const accBadge =
    accompaniment && !accompaniment.active ? accompaniment.visitBadgeName(v.id) : null;
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
  // 同行モード中はカードクリック=選択トグル (選択済みコース内は個別トグル不可)。
  const effectiveOnClick = accActive
    ? accInCourse
      ? undefined
      : () => accompaniment!.toggleVisit(v.id)
    : onClick;
  return (
    <button
      type="button"
      onClick={effectiveOnClick}
      data-testid={`wtl-visit-${v.id}`}
      data-accompaniment-selected={accSelected ? 'true' : undefined}
      title={
        accInCourse
          ? 'コース丸ごとに含まれています（個別解除はコース選択を外してください）'
          : (v.patient_address ?? undefined)
      }
      className={cn(
        'absolute flex flex-col gap-px rounded-md border border-l-[3px] px-1.5 py-0.5 text-left shadow-[var(--shadow-xs)] transition-shadow hover:z-[4] hover:shadow-[var(--shadow-md)] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary',
        accOverlap && 'z-[3] ring-2 ring-error',
        accSelected && !accOverlap && 'z-[3] ring-2 ring-brand-primary',
      )}
      style={{
        top,
        height: h,
        ...laneStyle,
        background: pal.bg,
        borderColor: accOverlap ? 'var(--error)' : pal.ln,
        borderLeftColor: accOverlap ? 'var(--error)' : pal.bar,
        color: pal.ink,
        // 案 A (2026-08-08): 型とズレている訪問は左端の帯を破線＋警告色に (日ビューと同一)。
        ...(!accOverlap ? (masterDivergenceCardStyle(v) ?? {}) : {}),
      }}
      data-master-diverged={isDivergedFromMaster(v) ? 'true' : undefined}
    >
      {/* 同行モード: 選択チェック (実効選択集合に入っている訪問)。 */}
      {accActive && accSelected && (
        <span
          className="absolute left-0.5 top-0.5 z-[2] grid h-3.5 w-3.5 place-items-center rounded-full bg-brand-primary text-[8px] font-bold text-white"
          aria-hidden="true"
        >
          ✓
        </span>
      )}
      {/* 常時表示 (モード外): 個別同行バッジ 👥新人名 (§7.2)。
          右上へ配置 (患者名との被り解消・PO要望)。名は左寄せ truncate なので右上は空く。
          ピン留めカードは右上の画鋲 (CornerPushPin) を避けて少し左へ寄せる。 */}
      {accBadge && (
        <span
          className={cn(
            'absolute top-0.5 z-[2] max-w-[70%] truncate rounded-full bg-info-bg px-1 text-[8px] font-bold text-info',
            v.is_pinned ? 'right-[15px]' : 'right-0.5',
          )}
          data-testid={`wtl-accompaniment-badge-${v.id}`}
          title={`同行: ${accBadge}`}
        >
          👥{accBadge}
        </span>
      )}
      {/* ピン留め: 右上に打ち込んだ画鋲 (📍住所と誤認しない・PO要望 2026-07-08)。 */}
      {v.is_pinned && <CornerPushPin className="h-4 w-4" />}
      {/* 1行目: アイコン + 患者名 (フル表示)。 */}
      <span className="flex min-w-0 items-center gap-0.5">
        {isMulti && (
          <span className="inline-flex shrink-0 text-brand-primary" aria-label="2名体制">
            <PersonMark />
          </span>
        )}
        {/* 可動域 (2026-08-07 / PO 要望): 日タイムラインと同じ判定・同じ淡さ。 */}
        <MovabilityMark visit={v} visitId={v.id} testIdPrefix="wtl" />
        <span className="truncate text-[12px] font-bold leading-tight">
          {v.patient_name ?? '—'}
        </span>
      </span>
      {/* 2行目: 時刻・所要分 (日ビューと同じ構成)。 */}
      {h >= TL_SHOW_SVC_PX && (
        <span className="tnum flex min-w-0 items-center gap-1 text-[9.5px] font-semibold opacity-75">
          <span className="shrink-0">
            {(v.start_time ?? '').slice(0, 5)}・{durMin}分
          </span>
          {/* 案 B (2026-08-08): 型とズレているときだけ本来の時刻を併記 (日ビューと同一)。 */}
          {masterTimeSuffix(v) ? (
            <span
              className="shrink-0 text-warning"
              data-testid={`wtl-master-diverged-${v.id}`}
              title={masterDivergenceTitle(v) ?? undefined}
            >
              {masterTimeSuffix(v)}
            </span>
          ) : null}
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
  accompaniment,
}: {
  item: Extract<WeekRenderItem, { kind: 'pair' }>;
  laneInfo?: CardLane;
  onPatientClick?: (patientId: string) => void;
  accompaniment?: AccompanimentBinding;
}) {
  const accActive = accompaniment?.active === true;
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
          const accSelected = accActive && accompaniment!.isVisitSelected(v.id);
          const accInCourse = accActive && accompaniment!.isVisitInSelectedCourse(v.id);
          const accOverlap = accActive && accompaniment!.isVisitOverlapping(v.id);
          const accBadge =
            accompaniment && !accompaniment.active ? accompaniment.visitBadgeName(v.id) : null;
          const onCardClick = accActive
            ? accInCourse
              ? undefined
              : () => accompaniment!.toggleVisit(v.id)
            : onPatientClick
              ? () => onPatientClick(v.patient_id)
              : undefined;
          return (
            <button
              key={v.id}
              type="button"
              data-testid={`wtl-visit-${v.id}`}
              data-accompaniment-selected={accSelected ? 'true' : undefined}
              onClick={onCardClick}
              title={
                accInCourse
                  ? 'コース丸ごとに含まれています（個別解除はコース選択を外してください）'
                  : undefined
              }
              className={cn(
                'relative flex min-h-0 flex-1 flex-col justify-center gap-px rounded border border-l-[3px] px-1 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary',
                accOverlap && 'ring-2 ring-error',
                accSelected && !accOverlap && 'ring-2 ring-brand-primary',
              )}
              style={{
                background: pal.bg,
                borderColor: accOverlap ? 'var(--error)' : pal.ln,
                borderLeftColor: accOverlap ? 'var(--error)' : pal.bar,
                color: pal.ink,
              }}
            >
              {accActive && accSelected && (
                <span
                  className="absolute left-0.5 top-0.5 z-[2] grid h-3 w-3 place-items-center rounded-full bg-brand-primary text-[7px] font-bold text-white"
                  aria-hidden="true"
                >
                  ✓
                </span>
              )}
              {accBadge && (
                <span
                  className={cn(
                    'absolute top-0.5 z-[2] max-w-[46px] truncate rounded-full bg-info-bg px-1 text-[7.5px] font-bold text-info',
                    v.is_pinned ? 'right-[15px]' : 'right-0.5',
                  )}
                  data-testid={`wtl-accompaniment-badge-${v.id}`}
                  title={`同行: ${accBadge}`}
                >
                  👥{accBadge}
                </span>
              )}
              {v.is_pinned && <CornerPushPin className="h-4 w-4" />}
              {/* 1行目: 右端の時刻が右上バッジと重ならないよう、バッジ有り時は右パディングで逃がす。 */}
              <span
                className={cn(
                  'flex min-w-0 items-center gap-1',
                  accBadge && (v.is_pinned ? 'pr-[64px]' : 'pr-[50px]'),
                )}
              >
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
  eventFramesByWeekday,
  weekdayDates,
  onPatientClick,
  capacityByWeekday,
  staffByWeekday,
  accompaniment,
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
      {/* イベントセクション (PO確定 2026-07-26): コース無しスタッフの枠。
          曜日列×イベントの簡易グリッド (時間比例ではなくチップ一覧)。 */}
      {eventFramesByWeekday && eventFramesByWeekday.size > 0 && (
        <section
          className="overflow-hidden rounded-lg border border-border-subtle"
          data-testid="week-timeline-events-section"
        >
          <header
            className="px-3 py-1.5 text-[12px] font-bold"
            style={{ background: 'var(--sched-event-bg)', color: 'var(--sched-event-ink)' }}
          >
            イベント（コース外のスタッフ予定）
          </header>
          <div className="grid grid-cols-2 gap-px bg-border-subtle sm:grid-cols-3 lg:grid-cols-6">
            {WEEKDAY_LABELS.map((label, wd) => {
              const frames = eventFramesByWeekday.get(wd) ?? [];
              return (
                <div key={label} className="bg-bg-base px-2 py-1.5">
                  <div className="mb-1 text-[10px] font-bold text-text-secondary">
                    {weekdayDates?.[wd] ? `${weekdayDates[wd]}（${label}）` : label}
                  </div>
                  <div className="space-y-0.5">
                    {frames.flatMap((f) =>
                      f.events.map((ev) => {
                        const isMemo = ev.start_time === ev.end_time;
                        return (
                          <div
                            key={`${f.staff.id}-${ev.id}`}
                            className="rounded border border-l-[3px] px-1 py-0.5 text-[10px] leading-tight"
                            style={{
                              background: 'var(--sched-event-bg)',
                              borderColor: 'var(--sched-event-ln)',
                              borderLeftColor: 'var(--sched-event-bar)',
                              color: 'var(--sched-event-ink)',
                            }}
                            title={`${f.staff.name} ${ev.title || ev.type}${ev.note ? `\n備考: ${ev.note}` : ''}`}
                          >
                            <span className="font-bold">
                              {isMemo ? '📝 ' : ''}
                              {f.staff.name}
                            </span>{' '}
                            {isMemo ? (
                              <span>{ev.title || ev.type}</span>
                            ) : (
                              <span>
                                <span className="tnum opacity-80">
                                  {ev.start_time.slice(0, 5)}-{ev.end_time.slice(0, 5)}
                                </span>{' '}
                                {ev.title || ev.type}
                              </span>
                            )}
                          </div>
                        );
                      }),
                    )}
                    {frames.length === 0 && <span className="text-[10px] text-text-muted">—</span>}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}
      {options.map((o) => (
        <CourseWeekSection
          key={o.templateId}
          option={o}
          visits={visits}
          weekdayDates={weekdayDates}
          onPatientClick={onPatientClick}
          capacityByWeekday={capacityByWeekday}
          staffByWeekday={staffByWeekday}
          accompaniment={accompaniment}
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
  accompaniment,
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
  accompaniment?: AccompanimentBinding;
}) {
  const accActive = accompaniment?.active === true;
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
            // 同行モード: この曜日のコースインスタンスを解決し、ヘッダを選択トグルにする。
            const courseId = accompaniment?.resolveCourseId(option.templateId, wd) ?? null;
            const headerSelectable = accActive && !!courseId;
            const headerSelected = accActive && accompaniment!.isCourseSelected(courseId);
            const courseBadge =
              accompaniment && !accompaniment.active
                ? accompaniment.courseBadgeName(courseId)
                : null;
            return (
              <div
                key={d}
                role={headerSelectable ? 'button' : undefined}
                tabIndex={headerSelectable ? 0 : undefined}
                onClick={
                  headerSelectable
                    ? () => accompaniment!.toggleCourse(courseId, option.templateId, wd)
                    : undefined
                }
                onKeyDown={
                  headerSelectable
                    ? (ev) => {
                        if (ev.key === 'Enter' || ev.key === ' ') {
                          ev.preventDefault();
                          accompaniment!.toggleCourse(courseId, option.templateId, wd);
                        }
                      }
                    : undefined
                }
                data-testid={accActive ? `wtl-course-header-${option.templateId}-${wd}` : undefined}
                data-accompaniment-selected={headerSelected ? 'true' : undefined}
                className={cn(
                  'border-l border-[var(--border-subtle)] px-2 py-1.5',
                  headerSelectable &&
                    'cursor-pointer outline-none ring-inset hover:bg-brand-primary-light focus-visible:ring-2 focus-visible:ring-brand-primary',
                  headerSelected && 'bg-brand-primary-light ring-2 ring-inset ring-brand-primary',
                )}
                style={{ flex: 1, minWidth: COL_MIN_W }}
              >
                <div className="flex items-center text-[12px] font-bold text-text-primary">
                  {accActive && (
                    <span
                      className={cn(
                        'mr-1 grid h-4 w-4 shrink-0 place-items-center rounded border text-[9px] font-bold',
                        headerSelected
                          ? 'border-brand-primary bg-brand-primary text-white'
                          : 'border-border-default text-transparent',
                      )}
                      aria-hidden="true"
                    >
                      ✓
                    </span>
                  )}
                  {d}
                  {weekdayDates?.[wd] && (
                    <span className="tnum ml-1.5 text-[10px] font-medium text-text-muted">
                      {weekdayDates[wd]}
                    </span>
                  )}
                </div>
                {/* 曜日ごとの担当スタッフ: 日ビューヘッダと同じ性別色アバター + 太字名。
                    コース丸ごと同行 (§7.2) はスタッフ名の右隣に 👥チップで併記する
                    (別行に積むとこの列だけヘッダが縦に伸び、他コースと高さがズレるため・PO要望)。 */}
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
                  <span className="min-w-0 flex-1">
                    <span className="flex min-w-0 items-center gap-1">
                      <span className="min-w-0 flex-1 truncate text-[11.5px] font-bold text-text-primary">
                        {staff?.name ?? '（未割当）'}
                      </span>
                      {courseBadge && (
                        <span
                          className="inline-flex max-w-[55%] shrink-0 items-center truncate rounded-full bg-info-bg px-1 text-[9px] font-bold text-info"
                          data-testid={`wtl-course-accompaniment-${option.templateId}-${wd}`}
                          title={`同行: ${courseBadge}（新人）`}
                        >
                          👥{courseBadge}
                        </span>
                      )}
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
                      accompaniment={accompaniment}
                    />
                  ) : (
                    <WeekCard
                      key={it.id}
                      v={it.v}
                      laneInfo={lanes.get(it.id)}
                      onClick={onPatientClick ? () => onPatientClick(it.v.patient_id) : undefined}
                      accompaniment={accompaniment}
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
