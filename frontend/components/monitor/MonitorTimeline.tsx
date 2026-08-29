'use client';

/**
 * 訪問モニター タイムライン (ガント) — スタッフ×時刻 (8–19h)。
 *
 * M-4 (2026-07-08 PO要望): スケジュール画面のカード視覚言語へ統一。
 *   - 予定 = 灰ハッチバー → **性別ウォッシュのミニカード** (2行: 性別ドット+患者名 /
 *     時刻 tnum+📍住所)。縦の余白 (旧: 行高66pxの半分が空白) をカードに使う
 *   - 実績 = カード下辺の **状態色レール** (色の意味体系 --status-* は不変)
 *   - 行ヘッダの番号バッジ = 担当スタッフの性別色 / 今ライン = --sched-now /
 *     会議・イベント = 藤色帯 (カイポケ反映外・表示専用)
 * 行クリックでコース選択 (地図に順路)、カード/レールクリックで訪問詳細。
 * 性別・住所・スタッフ性別・イベントは optional props (未指定 = 中立色/帯なし)。
 */
import { useEffect, useRef, type PointerEvent as ReactPointerEvent } from 'react';

import { cn } from '@/lib/utils';
import type { MonitorStaffRow, MonitorVisit } from '@/lib/schemas/monitor';
import type { EventRead } from '@/lib/schemas/staff-events';
import { genderPalette } from '@/lib/scheduling/timeline';

import {
  MISSING_BAR_BG,
  STATUS_COLOR,
  TL_END_MIN,
  TL_START_MIN,
  assignVisitLanes,
  displayStatus,
  formatDistance,
  hmToMinutes,
  isUnplannedRow,
  isoToHm,
  minutesToPct,
  substituteTitle,
} from './constants';

/** M-4a: 予定カードの性別ウォッシュ・📍住所用メタ (患者マスタ FE join・未指定=中立色)。 */
export interface MonitorPatientMeta {
  sex?: string | null;
  address?: string | null;
}

/**
 * 行の安定キー。行=コース単位 (2026-07-10) なので course_id が第一キー。
 * コース無し行はスタッフ単位 (staff_id)、どちらも無ければ course_label で識別する。
 *
 * 予定外訪問の専用行 (§6) は **拠点ごとに 1 本**作られ、course_id は常に null・
 * course_label はどの行も同じ・staff_id は行内の担当が 1 名のときだけ入る。そのまま
 * だと (a) 複数拠点の予定外行同士 (b) 予定外行と同じスタッフのコース無し行 でキーが
 * 衝突し、React キー重複・2 行同時選択・地図の誤対象を招くため、拠点でキーを分ける。
 *
 * 予定外行の判定は配色・印と同じ {@link isUnplannedRow} (visit のフラグ) に揃える。
 * 以前はここだけラベル文字列と比較しており、文言を変えるとキーだけが別系統で
 * 変わる (= 行キーと表示の判定が食い違う) 危険があった。
 */
export function monitorRowKey(
  row: Pick<MonitorStaffRow, 'course_id' | 'staff_id' | 'course_label' | 'office_id' | 'visits'>,
): string {
  if (isUnplannedRow(row)) return `unplanned-${row.office_id ?? 'none'}`;
  return row.course_id ?? row.staff_id ?? `unassigned-${row.course_label ?? ''}`;
}

interface MonitorTimelineProps {
  rows: MonitorStaffRow[];
  /** 選択中の行キー (monitorRowKey)。担当未設定行も選択可能。 */
  selectedRowKey: string | null;
  selectedVisitId: string | null;
  /** 現在時刻 (分, JST)。「今」ライン用。 */
  nowMinutes: number;
  onSelectRow: (rowKey: string) => void;
  onSelectVisit: (visitId: string) => void;
  /** M-4a: 患者 ID → 性別/住所 (予定カードのウォッシュ・📍住所)。未指定=中立色。 */
  patientMetaById?: ReadonlyMap<string, MonitorPatientMeta>;
  /** M-4a: スタッフ ID → 性別 (行ヘッダの番号バッジ色)。未指定=中立色。 */
  staffSexById?: ReadonlyMap<string, string | null | undefined>;
  /** M-4b: スタッフ ID → 当日のイベント (藤色帯・表示専用・カイポケ反映外)。 */
  eventsByStaffId?: ReadonlyMap<string, EventRead[]>;
}

const HOURS = Array.from({ length: (TL_END_MIN - TL_START_MIN) / 60 + 1 }, (_, i) => 8 + i);

/**
 * M-4c (PO決定 2026-07-08): 時間軸を固定スケールに広げて横スクロールにする。
 * 従来は画面幅に比例圧縮され、35分枠で患者名が苗字までしか見えなかった。
 * 216px/時 → 35分カード ≈ 126px = フルネーム+時刻/住所が常に読める。
 * スタッフ列は sticky で左に固定し、当日は「今」へ自動スクロールする。
 */
const PX_PER_HOUR = 216;
const TRACK_W = ((TL_END_MIN - TL_START_MIN) / 60) * PX_PER_HOUR;
const LABEL_COL_W = 156;
const GRID_COLS_STYLE = { gridTemplateColumns: `${LABEL_COL_W}px ${TRACK_W}px` } as const;

/**
 * 1 レーンあたりの高さ (px) = 従来の 1 人分の行高 66px をそのまま使う。
 * PO 指摘 (2026-07-04): 33px への圧縮はバー 7px・ラベル被りで見にくいため廃止。
 * 重なりのある行はレーン数 × 66px に行を伸ばし、各レーンは 1 人行と同一レイアウト
 * (= 文字サイズ・バー高とも縮小しない)。
 */
const LANE_H_PX = 66;

/**
 * レーン位置からカード/レールの top・height (px) を返す (全レーン共通レイアウト)。
 * M-4a: 旧「浮きラベル+予定バー14px+実績バー15px」→「予定カード40px (2行) +
 * 実績レール14px」。縦の余白をカードの情報量に使う (PO指摘 2026-07-08)。
 */
function lanePos(lane: number) {
  const off = lane * LANE_H_PX;
  return {
    cardTop: off + 4,
    cardH: 40,
    actTop: off + 47,
    actH: 14,
    distTop: off + 16,
  };
}

export function MonitorTimeline({
  rows,
  selectedRowKey,
  selectedVisitId,
  nowMinutes,
  onSelectRow,
  onSelectVisit,
  patientMetaById,
  staffSexById,
  eventsByStaffId,
}: MonitorTimelineProps) {
  const hasSelection = selectedRowKey !== null;
  // M-4c: 初回表示時に「今」を画面中央へ (横スクロール化に伴う迷子防止・当日のみ)。
  const nowMarkerRef = useRef<HTMLSpanElement | null>(null);
  const didAutoScroll = useRef(false);
  useEffect(() => {
    if (didAutoScroll.current) return;
    if (nowMinutes < TL_START_MIN || nowMinutes > TL_END_MIN) return;
    didAutoScroll.current = true;
    // jsdom 未実装のため optional call。
    nowMarkerRef.current?.scrollIntoView?.({ inline: 'center', block: 'nearest' });
  }, [nowMinutes]);

  // M-4c改: 時刻バー (目盛り帯) をつかんで左右にドラッグでパン (PO要望 2026-07-08:
  // 下端のスクロールバーより直感的な横移動手段)。行側はクリック/選択があるため
  // ハンドラは時刻バーだけに付ける。Shift+ホイールの横スクロールはブラウザ標準。
  const rootRef = useRef<HTMLDivElement | null>(null);
  const panState = useRef<{ startX: number; startLeft: number; el: HTMLElement } | null>(null);
  const findHScrollParent = (): HTMLElement | null => {
    let el: HTMLElement | null = rootRef.current?.parentElement ?? null;
    while (el) {
      if (el.scrollWidth > el.clientWidth + 1) return el;
      el = el.parentElement;
    }
    return null;
  };
  const onAxisPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    const el = findHScrollParent();
    if (!el) return;
    panState.current = { startX: e.clientX, startLeft: el.scrollLeft, el };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const onAxisPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const p = panState.current;
    if (!p) return;
    p.el.scrollLeft = p.startLeft - (e.clientX - p.startX);
  };
  const onAxisPointerEnd = () => {
    panState.current = null;
  };

  return (
    <div ref={rootRef} className="w-max select-none px-1 pb-4" data-testid="monitor-timeline">
      {/* 時間軸ヘッダ (縦 sticky。#／スタッフ セルは横にも sticky) */}
      <div
        className="sticky top-0 z-[5] grid border-b border-border-default bg-bg-base"
        style={GRID_COLS_STYLE}
      >
        <div className="sticky left-0 z-[6] border-r border-border-default bg-bg-base p-2 text-[11px] text-text-muted">
          #／コース
        </div>
        <div
          className="relative flex cursor-grab touch-none active:cursor-grabbing"
          data-testid="monitor-time-axis"
          title="ドラッグで横スクロール（Shift+ホイールでも動かせます）"
          onPointerDown={onAxisPointerDown}
          onPointerMove={onAxisPointerMove}
          onPointerUp={onAxisPointerEnd}
          onPointerCancel={onAxisPointerEnd}
        >
          {/* 8時ラベルは左端に絶対配置し、残り11時間を目盛線 (行側と同じ11分割) に揃える。
              旧: 12分割 flex-1 で目盛線から最大180pxドリフトしていた (レビューLOW対応)。 */}
          <span className="absolute left-0 top-0 py-2 pl-0.5 text-[11px] text-text-muted">8</span>
          {HOURS.slice(1).map((h) => (
            <span
              key={h}
              className="flex-1 border-l border-border-default py-2 pl-0.5 text-[11px] text-text-muted"
            >
              {h}
            </span>
          ))}
          {/* 「今」への自動スクロール用マーカー (不可視・当日のみ意味を持つ) */}
          {nowMinutes >= TL_START_MIN && nowMinutes <= TL_END_MIN && (
            <span
              ref={nowMarkerRef}
              aria-hidden
              className="pointer-events-none absolute top-0 h-px w-px"
              style={{ left: `${minutesToPct(nowMinutes)}%` }}
            />
          )}
        </div>
      </div>

      {rows.length === 0 && (
        <div className="sticky left-0 w-[calc(100vw-460px)] min-w-[320px] px-4 py-10 text-center text-sm text-text-muted">
          この日の訪問はありません。
        </div>
      )}

      {rows.map((row, idx) => {
        const rowKey = monitorRowKey(row);
        const isSel = rowKey === selectedRowKey;
        const key = rowKey;
        const laneMap = assignVisitLanes(row.visits);
        // laneCount は全 visit で共通 (assignVisitLanes が統一値を返す)。
        const rowLaneCount = laneMap.size > 0 ? laneMap.values().next().value!.laneCount : 1;
        // スケジュール側のコース担当 (courses.assigned) と実訪問の担当 (visits.primary) の突合。
        // 食い違いは隠さず ⚠ を出す (設計原則③)。
        // 予定外訪問の専用行 (§6): 予定が無い実績だけの行なので、通常のコース行とは
        // 別配色 (--unplanned 系) にして「予定の列」と読み違えないようにする。
        // 並び順: BE の sort キーは (拠点名 → コースコード → スタッフ名) で、予定外行の
        // ラベル「📌予定外訪問」は先頭が U+1F4CC (> コース無し行の番兵 U+FFFF) になるため
        // **各拠点ブロックの末尾**に並ぶ。全行の最後ではない (拠点が複数なら拠点ごとに 1 本)。
        // 絵文字のコードポイント順に依存した並びなので、ラベル変更時は BE 側の sort も
        // 併せて見直すこと (FE では並べ替えロジックを持たない)。
        const unplannedRow = isUnplannedRow(row);
        const scheduleStaff = row.course_staff_name ?? null;
        const visitStaffIds = row.staff_ids ?? [];
        const staffMismatch =
          row.course_staff_id != null &&
          (visitStaffIds.length === 0 || visitStaffIds.some((sid) => sid !== row.course_staff_id));
        // 同行 (§7.3): 行内の訪問から同行スタッフ名を重複無しで収集。
        // 1 訪問に複数名ありうる (確定#5) ため配列を優先し、無ければ単数へ落とす。
        // ⚠ 担当乖離とは別の情報系ラベルなので混同しないよう独立して描画する。
        const accompanimentNames = Array.from(
          new Set(
            row.visits
              .flatMap((v) =>
                v.accompaniment_staff_names && v.accompaniment_staff_names.length > 0
                  ? v.accompaniment_staff_names
                  : [v.accompaniment_staff_name],
              )
              .filter((n): n is string => !!n),
          ),
        );
        return (
          <div
            key={key}
            role="button"
            tabIndex={0}
            data-testid={`monitor-row-${idx}`}
            data-unplanned={unplannedRow ? 'true' : undefined}
            onClick={() => onSelectRow(rowKey)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') onSelectRow(rowKey);
            }}
            className={cn(
              'group grid min-h-[66px] cursor-pointer border-b border-border-default/60 transition-[opacity,background] duration-150',
              isSel ? 'bg-brand-primary-light' : 'hover:bg-bg-muted',
              hasSelection && !isSel ? 'opacity-40' : '',
            )}
            style={{
              ...GRID_COLS_STYLE,
              ...(rowLaneCount > 1 ? { minHeight: rowLaneCount * LANE_H_PX } : {}),
            }}
          >
            {/* 左: 行番号 (M-4a: 非選択時はスタッフ性別色のバッジ) + スタッフ。
                M-4c: 横スクロールしても見失わないよう左に sticky (不透明背景必須)。 */}
            <div
              className={cn(
                'sticky left-0 z-[3] flex items-center gap-2 border-r border-border-default/60 px-2 py-1.5',
                // 選択アクセント線はセル側に置く (行側だと不透明な sticky セルに隠れる)。
                isSel
                  ? 'bg-brand-primary-light shadow-[inset_5px_0_0_var(--brand-primary)]'
                  : unplannedRow
                    ? 'bg-unplanned-bg shadow-[inset_5px_0_0_var(--unplanned)]'
                    : 'bg-bg-base group-hover:bg-bg-muted',
              )}
            >
              <span
                className={cn(
                  'flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-[11px] font-bold tabular-nums',
                  isSel ? 'bg-brand-primary text-white' : 'border-[1.5px]',
                )}
                style={
                  isSel
                    ? undefined
                    : unplannedRow
                      ? {
                          background: 'var(--unplanned-bg)',
                          borderColor: 'var(--unplanned)',
                          color: 'var(--unplanned)',
                        }
                      : (() => {
                          const sp = genderPalette(
                            row.staff_id ? (staffSexById?.get(row.staff_id) ?? null) : null,
                          );
                          return { background: sp.bg, borderColor: sp.bar, color: sp.ink };
                        })()
                }
              >
                {idx + 1}
              </span>
              <span className="min-w-0 overflow-hidden">
                {/* 行=コース単位: 1行目=コース (無ければ担当名) / 2行目=拠点・担当。 */}
                <span
                  className={cn(
                    'block truncate text-[13px] font-semibold',
                    isSel
                      ? 'text-brand-primary-hover'
                      : unplannedRow
                        ? 'text-unplanned'
                        : 'text-text-primary',
                  )}
                  data-testid={unplannedRow ? `monitor-row-unplanned-${key}` : undefined}
                  title={
                    unplannedRow
                      ? '予定に無い訪問の実績（QR打刻）です。コース行には混ぜていません。'
                      : [row.course_label, row.staff_name].filter(Boolean).join(' ・ ') || undefined
                  }
                >
                  {row.course_label ?? row.staff_name ?? '（担当未設定）'}
                </span>
                <span className="block truncate text-[11px] text-text-muted">
                  {isSel ? (
                    '● 選択中'
                  ) : unplannedRow ? (
                    // 予定外行は「予定の担当」が存在しないので、拠点と実績スタッフを出す。
                    [row.office_name, row.staff_name ?? '実績のみ'].filter(Boolean).join(' ・ ')
                  ) : (
                    <>
                      {/* 担当 = スケジュールのコース担当を優先表示 (無ければ実訪問の担当)。 */}
                      {[
                        row.office_name,
                        row.course_label ? (scheduleStaff ?? row.staff_name ?? '担当未設定') : null,
                      ]
                        .filter(Boolean)
                        .join(' ・ ') || '—'}
                      {staffMismatch && (
                        <span
                          className="ml-1 font-bold text-warning"
                          title={`スケジュールの担当（${scheduleStaff ?? '未設定'}）と実訪問の担当（${row.staff_name ?? '未設定'}）が一致していません`}
                        >
                          ⚠
                        </span>
                      )}
                    </>
                  )}
                </span>
                {/* 新人同行 (§7.3): ⚠ 担当乖離とは別枠の情報ラベル。選択/非選択どちらでも表示。 */}
                {accompanimentNames.length > 0 && (
                  <span
                    className="block truncate text-[11px] font-medium text-info"
                    data-testid={`monitor-row-accompaniment-${key}`}
                    title="この行に同行があります"
                  >
                    ＋{accompanimentNames.join('・')}（同行）
                  </span>
                )}
              </span>
            </div>

            {/* 右: トラック */}
            <div className="relative border-l border-border-default">
              {/* グリッド線 */}
              <div className="absolute inset-0 flex">
                {HOURS.slice(1).map((h) => (
                  <i key={h} className="flex-1 border-l border-border-default/40" />
                ))}
              </div>
              {/* 今ライン (M-4a: スケジュールと同じ --sched-now に統一) */}
              {nowMinutes >= TL_START_MIN && nowMinutes <= TL_END_MIN && (
                <div
                  className="absolute bottom-0 top-0 z-[4] w-0.5"
                  style={{ left: `${minutesToPct(nowMinutes)}%`, background: 'var(--sched-now)' }}
                  aria-hidden
                >
                  <span
                    className="absolute -top-px left-1 text-[10px] font-bold"
                    style={{ color: 'var(--sched-now)' }}
                  >
                    今
                  </span>
                </div>
              )}
              {/* M-4b: 会議・イベント帯 (藤色・表示専用・カイポケ反映外)。
                  空き時間の「なぜ空いているか」を説明する。カード (z-[2]) の下。
                  行=コース単位: 行内の全担当 (staff_ids) のイベントを重ねる。 */}
              {/* 予定外行は「予定なし・実績のみ」の行なので、予定側の情報である
                  イベント帯は描画しない (行の意味論と矛盾するため)。 */}
              {(unplannedRow
                ? []
                : row.staff_ids?.length
                  ? row.staff_ids
                  : row.staff_id
                    ? [row.staff_id]
                    : []
              )
                .flatMap((sid) => eventsByStaffId?.get(sid) ?? [])
                .map((ev) => {
                  const es = hmToMinutes(ev.start_time.slice(0, 5));
                  const ee = hmToMinutes(ev.end_time.slice(0, 5));
                  if (ee <= TL_START_MIN || es >= TL_END_MIN || ee <= es) return null;
                  const eL = minutesToPct(es);
                  const eW = Math.max(minutesToPct(ee) - eL, 1.5);
                  return (
                    <div
                      key={`ev-${ev.id}`}
                      data-testid={`monitor-event-${ev.id}`}
                      className="pointer-events-none absolute z-[1] flex items-center gap-1 overflow-hidden rounded-md border border-l-[3px] px-1.5"
                      style={{
                        left: `${eL}%`,
                        width: `${eW}%`,
                        top: 4,
                        height: rowLaneCount * LANE_H_PX - 9,
                        background: 'var(--sched-event-bg)',
                        borderColor: 'var(--sched-event-ln)',
                        borderLeftColor: 'var(--sched-event-bar)',
                      }}
                      title={`${ev.type}${ev.title ? `: ${ev.title}` : ''}（${ev.start_time.slice(0, 5)}〜${ev.end_time.slice(0, 5)}・カイポケ反映外）`}
                    >
                      <span
                        className="shrink-0 text-[11px]"
                        style={{ color: 'var(--sched-event-bar)' }}
                      >
                        👥
                      </span>
                      <span
                        className="min-w-0 truncate text-[10px] font-bold"
                        style={{ color: 'var(--sched-event-ink)' }}
                      >
                        {ev.title && ev.title.trim() !== '' ? ev.title : ev.type}
                      </span>
                      <span
                        className="tnum shrink-0 text-[9px] opacity-75"
                        style={{ color: 'var(--sched-event-ink)' }}
                      >
                        {ev.start_time.slice(0, 5)}〜
                      </span>
                    </div>
                  );
                })}
              {row.visits.map((v) => {
                const li = laneMap.get(v.visit_id) ?? { lane: 0, laneCount: 1 };
                return (
                  <VisitBars
                    key={v.visit_id}
                    visit={v}
                    lane={li.lane}
                    nowMinutes={nowMinutes}
                    isSelected={v.visit_id === selectedVisitId}
                    onSelect={onSelectVisit}
                    meta={patientMetaById?.get(v.patient_id)}
                  />
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function VisitBars({
  visit,
  lane,
  nowMinutes,
  isSelected,
  onSelect,
  meta,
}: {
  visit: MonitorVisit;
  lane: number;
  nowMinutes: number;
  isSelected: boolean;
  onSelect: (visitId: string) => void;
  /** M-4a: 性別ウォッシュ・📍住所 (未指定=中立色)。 */
  meta?: MonitorPatientMeta;
}) {
  const pos = lanePos(lane);
  const ps = hmToMinutes(visit.start_time);
  const pe = hmToMinutes(visit.end_time);
  const pL = minutesToPct(ps);
  // クランプ済み座標。範囲外バーが消えないよう最小幅を確保する。
  const pW = Math.max(minutesToPct(pe) - pL, 1.5);
  const status = displayStatus(visit);
  const color = STATUS_COLOR[status];
  const isPair = visit.visit_group_id != null;
  // 時間軸 (8–19h) からのはみ出し印。
  const overflowLeft = ps < TL_START_MIN;
  const overflowRight = pe > TL_END_MIN;

  const arrived = visit.arrival != null;
  const hasActual = arrived || status === 'missing';

  // 実績バーの開始/幅。到着あり=到着〜(退出 or now)、未訪問=予定区間に赤ハッチ。
  let actLeft = pL;
  let actWidth = Math.max(pW, 2.5);
  let actLabel = '';
  if (arrived) {
    const arrMin = visit.arrival?.scanned_at ? isoToMinutesJst(visit.arrival.scanned_at) : ps;
    const endMin = visit.departure?.scanned_at
      ? isoToMinutesJst(visit.departure.scanned_at)
      : nowMinutes;
    actLeft = minutesToPct(arrMin);
    actWidth = Math.max(minutesToPct(endMin) - actLeft, 2.5);
    if (status === 'mismatch' && visit.arrival?.distance_m != null) {
      actLabel = `${Math.round(visit.arrival.distance_m)}m`;
    } else if (status === 'review' && visit.arrival_delay_min != null) {
      actLabel = `+${visit.arrival_delay_min}分`;
    }
  }

  const dn = visit.distance_to_next_m;
  // M-4a: 予定カードの性別ウォッシュ (患者マスタ FE join。未指定/未登録=中立色)。
  const pal = genderPalette(meta?.sex ?? null);
  // §6: 予定外訪問のカードは性別ウォッシュではなく専用配色 (c-coupled 系)。
  // 「予定の列」に見えないよう、行の配色と揃える。
  const cardStyle = visit.is_unplanned
    ? {
        background: 'var(--unplanned-bg)',
        borderColor: 'var(--unplanned)',
        borderLeftColor: 'var(--unplanned)',
        color: 'var(--unplanned)',
      }
    : { background: pal.bg, borderColor: pal.ln, borderLeftColor: pal.bar, color: pal.ink };
  // カード 2 行目に併記する「行った人」。予定担当 (staff_name) は書き換えない (§6)。
  // 代行は substitute_staff_name (代行した人) — actual_staff_name は最新打刻者なので
  // 代行後に担当本人が打ち直すと担当本人名になり、バッジと矛盾するため使わない。
  // 予定外は実績スタッフ (= 打刻者本人) をそのまま出す。名前が無ければ併記しない。
  const trailingName = visit.is_substitute
    ? (visit.substitute_staff_name ?? null)
    : visit.is_unplanned
      ? (visit.actual_staff_name ?? null)
      : null;

  return (
    <>
      {/* はみ出し印 (時間軸外の訪問) */}
      {overflowLeft && (
        <span
          className="pointer-events-none absolute left-0 z-[3] text-[10px] font-bold text-text-muted"
          style={{ top: pos.cardTop }}
          title={`${visit.start_time} 開始（表示範囲外）`}
        >
          ‹
        </span>
      )}
      {overflowRight && (
        <span
          className="pointer-events-none absolute right-0 z-[3] text-[10px] font-bold text-text-muted"
          style={{ top: pos.cardTop }}
          title={`${visit.end_time} 終了（表示範囲外）`}
        >
          ›
        </span>
      )}
      {/* 次までの距離 (カード右横) */}
      {dn != null && (
        <div
          className="pointer-events-none absolute whitespace-nowrap text-[10px] text-text-muted [text-shadow:0_0_3px_#fff,0_0_3px_#fff]"
          style={{ left: `${minutesToPct(pe)}%`, top: pos.distTop }}
        >
          →{formatDistance(dn)}
        </div>
      )}
      {/* 予定カード (M-4a): スケジュールと同じ性別ウォッシュ+左帯+角丸の2行カード。
          1行目=性別ドット+患者名+2名ピル / 2行目=時刻 tnum+📍住所。 */}
      <button
        type="button"
        data-testid={`monitor-bar-plan-${visit.visit_id}`}
        data-lane={lane}
        onClick={(e) => {
          e.stopPropagation();
          onSelect(visit.visit_id);
        }}
        title={`${visit.is_unplanned ? '予定外訪問 ' : '予定 '}${visit.start_time}–${visit.end_time} ${
          visit.patient_name ?? ''
        }${visit.staff_name ? `｜担当: ${visit.staff_name}` : ''}${
          visit.is_substitute ? `｜${substituteTitle(visit)}` : ''
        }${
          visit.is_unplanned && visit.actual_staff_name ? `｜実績: ${visit.actual_staff_name}` : ''
        }${meta?.address ? `｜📍${meta.address}` : ''}`}
        className="absolute z-[2] flex flex-col justify-center gap-px overflow-hidden rounded-md border border-l-[3px] px-1.5 text-left shadow-[var(--shadow-xs)] transition-shadow hover:shadow-[var(--shadow-sm)]"
        style={{
          left: `${pL}%`,
          width: `${pW}%`,
          top: pos.cardTop,
          height: pos.cardH,
          ...cardStyle,
        }}
      >
        <span className="flex min-w-0 items-center gap-1">
          <i
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ background: visit.is_unplanned ? 'var(--unplanned)' : pal.bar }}
            aria-hidden="true"
          />
          <span className="min-w-0 truncate text-[11px] font-bold leading-tight">
            {visit.patient_name ?? '—'}
          </span>
          {/* 代行バッジ (§6 #7): 行レベルの ⚠ (スケジュール担当≠visit 予定担当・amber) とは
              別物なので、色 (teal) とラベルの両方で区別する。意匠は「2名」バッジと同じ
              淡色地×濃色文字 (--info-bg × --info-strong = 6.7:1・WCAG AA)。 */}
          {visit.is_substitute && (
            <span
              data-testid={`monitor-bar-substitute-${visit.visit_id}`}
              title={substituteTitle(visit)}
              className="shrink-0 rounded-full bg-info-bg px-1 py-px text-[9px] font-bold text-info-strong"
            >
              代行
            </span>
          )}
          {isPair && (
            <span className="shrink-0 rounded-full bg-c-coupled-bg px-1 py-px text-[9px] font-bold text-c-coupled">
              2名
            </span>
          )}
        </span>
        <span className="flex min-w-0 items-center gap-1 text-[9px] leading-tight opacity-80">
          <span className="tnum shrink-0 font-semibold">
            {visit.start_time}–{visit.end_time}
          </span>
          {/* 行った人 (代行者 / 予定外の実績スタッフ)。予定担当は書き換えず並記する。
              名前が無い応答ではバッジのみとし、誤った名前を出さない。 */}
          {trailingName ? (
            <span
              data-testid={`monitor-bar-actual-staff-${visit.visit_id}`}
              className="min-w-0 truncate font-semibold"
            >
              →{trailingName}
            </span>
          ) : null}
          {meta?.address ? <span className="min-w-0 truncate">📍{meta.address}</span> : null}
        </span>
      </button>
      {/* 実績レール (M-4a: カード下辺・状態色の意味体系 --status-* は不変) */}
      {/* rounded-[5px]: 極小レールのためトークン(sm=8px)未満の例外 */}
      {hasActual && (
        <button
          type="button"
          data-testid={`monitor-bar-actual-${visit.visit_id}`}
          data-status={status}
          data-lane={lane}
          onClick={(e) => {
            e.stopPropagation();
            onSelect(visit.visit_id);
          }}
          className={cn(
            'absolute z-[2] flex items-center gap-0.5 overflow-hidden whitespace-nowrap rounded-[5px] px-1.5 text-[10px] font-semibold leading-none text-white',
            isSelected ? 'outline outline-2 outline-offset-1 outline-text-primary' : '',
            status === 'inprogress'
              ? '[background-image:repeating-linear-gradient(45deg,rgba(255,255,255,.25),rgba(255,255,255,.25)_4px,transparent_4px,transparent_8px)]'
              : '',
            // 確認済みは淡色化 (要対応の消化が一目で分かる)。未訪問は赤ハッチ+トレイで十分なため点滅しない。
            visit.reviewed ? 'opacity-50' : '',
          )}
          style={
            status === 'missing'
              ? {
                  left: `${pL}%`,
                  width: `${pW}%`,
                  backgroundImage: MISSING_BAR_BG,
                  top: pos.actTop,
                  height: pos.actH,
                }
              : {
                  left: `${actLeft}%`,
                  width: `${actWidth}%`,
                  backgroundColor: color,
                  top: pos.actTop,
                  height: pos.actH,
                }
          }
          title={`${visit.patient_name ?? ''} ${actLabel}${visit.reviewed ? ' ✓確認済' : ''}`}
        >
          {visit.reviewed && (
            <span data-testid={`monitor-bar-reviewed-${visit.visit_id}`} aria-label="確認済">
              ✓
            </span>
          )}
          {status === 'missing' ? '未訪問' : actLabel}
        </button>
      )}
      {/* ペア待ち: 同住所・同時刻の相方を対応中で未訪問扱いを保留している間。 */}
      {/* 警告色ではなく muted/info トーン (未訪問と紛らわしくしない)。 */}
      {/* rounded-[5px]: 極小バーのためトークン(sm=8px)未満の例外 */}
      {visit.pair_waiting && (
        <button
          type="button"
          data-testid={`monitor-pair-waiting-${visit.visit_id}`}
          data-lane={lane}
          onClick={(e) => {
            e.stopPropagation();
            onSelect(visit.visit_id);
          }}
          title={`ペア待ち（同住所の相方を対応中）${visit.patient_name ?? ''}`}
          className="absolute flex items-center gap-0.5 overflow-hidden whitespace-nowrap rounded-[5px] border border-border-default bg-bg-muted px-1.5 text-[10px] font-semibold leading-none text-text-secondary"
          style={{ left: `${pL}%`, width: `${pW}%`, top: pos.actTop, height: pos.actH }}
        >
          ペア待ち
        </button>
      )}
    </>
  );
}

/** ISO (UTC) → JST の「その日の分」。タイムライン座標用。 */
function isoToMinutesJst(iso: string): number {
  const hm = isoToHm(iso); // "HH:MM" (JST)
  return hmToMinutes(hm);
}
