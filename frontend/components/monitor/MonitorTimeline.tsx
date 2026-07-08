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
  isoToHm,
  minutesToPct,
} from './constants';

/** M-4a: 予定カードの性別ウォッシュ・📍住所用メタ (患者マスタ FE join・未指定=中立色)。 */
export interface MonitorPatientMeta {
  sex?: string | null;
  address?: string | null;
}

/**
 * 行の安定キー (PO 報告 2026-07-03: 担当未設定行も選択してマップ表示できるように)。
 * 担当あり = staff_id / 担当未設定 = コース別行なので course_label で識別する。
 */
export function monitorRowKey(row: Pick<MonitorStaffRow, 'staff_id' | 'course_label'>): string {
  return row.staff_id ?? `unassigned-${row.course_label ?? ''}`;
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
          #／スタッフ
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
        return (
          <div
            key={key}
            role="button"
            tabIndex={0}
            data-testid={`monitor-row-${idx}`}
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
                <span
                  className={cn(
                    'block truncate text-[13px] font-semibold',
                    isSel ? 'text-brand-primary-hover' : 'text-text-primary',
                  )}
                  title={row.staff_name ?? undefined}
                >
                  {row.staff_name ?? '（担当未設定）'}
                </span>
                <span className="text-[11px] text-text-muted">
                  {isSel
                    ? '● 選択中'
                    : [row.office_name, row.course_label].filter(Boolean).join(' ・ ') || '—'}
                </span>
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
                  空き時間の「なぜ空いているか」を説明する。カード (z-[2]) の下。 */}
              {row.staff_id
                ? eventsByStaffId?.get(row.staff_id)?.map((ev) => {
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
                  })
                : null}
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
        title={`予定 ${visit.start_time}–${visit.end_time} ${visit.patient_name ?? ''}${
          meta?.address ? `｜📍${meta.address}` : ''
        }`}
        className="absolute z-[2] flex flex-col justify-center gap-px overflow-hidden rounded-md border border-l-[3px] px-1.5 text-left shadow-[var(--shadow-xs)] transition-shadow hover:shadow-[var(--shadow-sm)]"
        style={{
          left: `${pL}%`,
          width: `${pW}%`,
          top: pos.cardTop,
          height: pos.cardH,
          background: pal.bg,
          borderColor: pal.ln,
          borderLeftColor: pal.bar,
          color: pal.ink,
        }}
      >
        <span className="flex min-w-0 items-center gap-1">
          <i
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ background: pal.bar }}
            aria-hidden="true"
          />
          <span className="min-w-0 truncate text-[11px] font-bold leading-tight">
            {visit.patient_name ?? '—'}
          </span>
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
            'absolute z-[2] flex items-center gap-0.5 overflow-hidden whitespace-nowrap rounded-[5px] px-1.5 text-[10px] font-semibold text-white',
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
          className="absolute flex items-center gap-0.5 overflow-hidden whitespace-nowrap rounded-[5px] border border-border-default bg-bg-muted px-1.5 text-[10px] font-semibold text-text-secondary"
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
