'use client';

/**
 * 訪問モニター タイムライン (ガント) — 職員×時刻 (8–19h)。
 *
 * 行 = 職員 (2026-10-01・docs/plans/monitor-staff-rows-design-2026-09-30.md)。
 *   - コースは行ではなく訪問ごとの**札** (「稲D」= 拠点名 1 文字目 + コード・色 = 拠点)。
 *     行ヘッダにその人のその日の札を並べる。予定外は「予定外」の札で本人の行に入る。
 *   - 同行・副担当はその人の行に薄いカードで出す (押すと担当の行の訪問を選ぶ)。
 *   - 所属の拠点が変わる所に「所属: 稲毛」の区切り行。
 *   - 行を押すと、その行の**すぐ下にパネルが開く** (中身は親が ``renderRowPanel`` で渡す:
 *     地図・順路・訪問の詳細)。開くのは 1 つだけ。横スクロールしても左に留まる。
 *     開け閉めで画面が飛ばないよう、押した行の画面上の位置を保つ。
 *
 * M-4 (2026-07-08 PO要望): スケジュール画面のカード視覚言語へ統一。
 *   - 予定 = **性別ウォッシュのミニカード** (2行: 札+性別ドット+患者名 / 時刻 tnum+📍住所)
 *   - 実績 = カード下辺の **状態色レール** (色の意味体系 --status-* は不変)
 *   - 行ヘッダの番号バッジ = 職員の性別色 / 今ライン = --sched-now /
 *     会議・イベント = 藤色帯・休み = ハッチ帯 (カイポケ反映外・表示専用)
 * 性別・住所・職員性別・イベントは optional props (未指定 = 中立色/帯なし)。
 */
import {
  Fragment,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react';

import { cn } from '@/lib/utils';
import type { MonitorStaffRow, MonitorVisit } from '@/lib/schemas/monitor';
import type { EventRead } from '@/lib/schemas/staff-events';
import { actualTimeParts } from '@/lib/format/actualTime';
import { genderPalette } from '@/lib/scheduling/timeline';
import { InactiveVisitBadge } from '@/components/schedule/InactiveVisitBadge';
import { classifyVisitDisplay, type VisitDisplayKind } from '@/lib/schedule/visitVisibility';

import {
  DEPARTURE_MISSING_LABEL,
  MISSING_BAR_BG,
  STATUS_COLOR,
  TL_END_MIN,
  TL_START_MIN,
  UNASSIGNED_ROW_KEY,
  actualArrivalIso,
  actualDepartureIso,
  adjustmentNotes,
  assignVisitLanes,
  displayStatus,
  formatDistance,
  hmToMinutes,
  isDepartureMissing,
  isoToHm,
  minutesToPct,
  officeTagTone,
  substituteTitle,
} from './constants';

/** M-4a: 予定カードの性別ウォッシュ・📍住所用メタ (患者マスタ FE join・未指定=中立色)。 */
export interface MonitorPatientMeta {
  sex?: string | null;
  address?: string | null;
}

/**
 * 行の安定キー。行 = 職員単位 (2026-10-01) なので職員 id が第一キー。
 * 「担当なし」行 (staff_id=null) は BE が 1 本しか作らないので固定キー。
 */
export function monitorRowKey(row: Pick<MonitorStaffRow, 'staff_id'>): string {
  return row.staff_id ?? UNASSIGNED_ROW_KEY;
}

interface MonitorTimelineProps {
  rows: MonitorStaffRow[];
  /** 選択中 (= 下にパネルを開いている) 行キー (monitorRowKey)。 */
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
  /**
   * 表示中の日付 (YYYY-MM-DD)。`MonitorVisit` は日付を持たない (モニターは 1 日単位)
   * ので、「入院中」バッジの日付条件 (ステータス変更日以降のみ) に使うため受け取る。
   * 未指定なら日付条件を課さない (従来どおり)。
   */
  dateIso?: string | null;
  /** 拠点 id の並び (モニター応答の office_order = 拠点マスタ順)。札の色を拠点ごとに揃える。 */
  officeIds?: readonly string[];
  /**
   * 訪問 id → 訪問 (表示中の全行の主担当の訪問)。同行・副担当の薄いカード
   * (`companion_visit_ids`) を描くのに使う。未指定なら薄いカードは描かない。
   */
  visitById?: ReadonlyMap<string, MonitorVisit>;
  /** 選んだ行のすぐ下に開くパネルの中身 (地図・順路・訪問の詳細)。 */
  renderRowPanel?: (row: MonitorStaffRow) => ReactNode;
}

const HOURS = Array.from({ length: (TL_END_MIN - TL_START_MIN) / 60 + 1 }, (_, i) => 8 + i);

/**
 * M-4c (PO決定 2026-07-08): 時間軸を固定スケールに広げて横スクロールにする。
 * 従来は画面幅に比例圧縮され、35分枠で患者名が苗字までしか見えなかった。
 * 216px/時 → 35分カード ≈ 126px = フルネーム+時刻/住所が常に読める。
 * 職員列は sticky で左に固定し、当日は「今」へ自動スクロールする。
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

/** 休み・時間変更の外側 (勤務外) のハッチ帯 (スケジュールの勤務外と同じトークン)。 */
const OFFDUTY_BG =
  'repeating-linear-gradient(45deg,var(--sched-offduty-bg),var(--sched-offduty-bg) 6px,var(--sched-offduty-hatch) 6px,var(--sched-offduty-hatch) 12px)';

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

/** 縦 / 横にスクロールする最も近い祖先 (無ければ null = ページ全体)。 */
function findScrollParent(el: HTMLElement | null, axis: 'x' | 'y'): HTMLElement | null {
  let cur: HTMLElement | null = el?.parentElement ?? null;
  while (cur) {
    const st = typeof window !== 'undefined' ? window.getComputedStyle(cur) : null;
    // 縦横別の値が取れない環境 (jsdom) では shorthand の overflow を見る。
    const ov = (axis === 'x' ? st?.overflowX : st?.overflowY) || st?.overflow;
    if (ov === 'auto' || ov === 'scroll') return cur;
    cur = cur.parentElement;
  }
  return null;
}

/** 縦方向に delta px スクロールする (スクロール領域が無ければページ)。 */
function scrollByY(scroller: HTMLElement | null, delta: number) {
  if (delta === 0) return;
  const el = scroller ?? document.scrollingElement ?? document.documentElement;
  el.scrollTop += delta;
}

/** コースの札 (訪問カード・行ヘッダ・順路一覧で共通)。 */
export function CourseTagChip({
  label,
  officeId,
  officeIds,
  mismatch = false,
  className,
  testId,
}: {
  label: string;
  officeId: string | null | undefined;
  officeIds: readonly string[];
  mismatch?: boolean;
  className?: string;
  testId?: string;
}) {
  return (
    <span
      data-testid={testId}
      className={cn(
        'inline-flex shrink-0 items-center rounded px-1 text-[10px] font-bold leading-[1.55]',
        className,
      )}
      style={officeTagTone(officeId, officeIds)}
      title={mismatch ? 'コースの担当とこの訪問の担当が違います' : undefined}
    >
      {label}
      {mismatch ? '⚠' : ''}
    </span>
  );
}

/** 「予定外」の札 (予定に無い訪問・QR 打刻の実績)。 */
export function UnplannedChip({ className, testId }: { className?: string; testId?: string }) {
  return (
    <span
      data-testid={testId}
      className={cn(
        'inline-flex shrink-0 items-center rounded border border-unplanned bg-unplanned-bg px-1 text-[10px] font-bold leading-[1.45] text-unplanned',
        className,
      )}
    >
      予定外
    </span>
  );
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
  dateIso = null,
  officeIds = [],
  visitById,
  renderRowPanel,
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
  const headerRef = useRef<HTMLDivElement | null>(null);
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

  // 行の下に開くパネルの幅 = 横スクロール領域の見えている幅 (sticky left:0 で左に留める)。
  const [viewportW, setViewportW] = useState(0);
  useLayoutEffect(() => {
    const scroller = findScrollParent(rootRef.current, 'x');
    if (!scroller) return;
    // ルートの左右 padding (px-1) ぶんを引く。
    const measure = () => setViewportW(Math.max(0, scroller.clientWidth - 8));
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(scroller);
    return () => ro.disconnect();
  }, []);

  // ─── 開け閉めで画面が飛ばないようにする (設計 §4) ───
  // 行 / カードを押した時点の「押した行の画面上の位置」を覚えておき、描画後に
  // 同じ位置へ戻す (上の行のパネルが閉じて下の行が開くと、閉じたぶん行が上へ動くため)。
  // タイムライン外 (要対応トレイ) から選んだときは覚えが無いので、その行へスクロールする。
  const rowEls = useRef(new Map<string, HTMLDivElement>());
  const panelRef = useRef<HTMLDivElement | null>(null);
  const anchorRef = useRef<{ key: string; top: number } | null>(null);
  const rememberAnchor = (key: string) => {
    const el = rowEls.current.get(key);
    if (el) anchorRef.current = { key, top: el.getBoundingClientRect().top };
  };
  const prevSelectedKey = useRef<string | null>(null);
  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    anchorRef.current = null;
    const prevKey = prevSelectedKey.current;
    const keyChanged = prevKey !== selectedRowKey;
    prevSelectedKey.current = selectedRowKey;
    if (selectedRowKey == null) {
      // パネルを閉じた (✕・Esc): 閉じるボタンと一緒にフォーカスが消えるので、開いていた
      // 行へ戻す。ほかの所 (日付の矢印など) にフォーカスがあるときは奪わない。
      const active = typeof document !== 'undefined' ? document.activeElement : null;
      if (prevKey != null && (active == null || active === document.body)) {
        rowEls.current.get(prevKey)?.focus({ preventScroll: true });
      }
      return;
    }
    const rowEl = rowEls.current.get(selectedRowKey);
    if (!rowEl) return;
    const scroller = findScrollParent(rootRef.current, 'y');
    const viewTop = scroller ? scroller.getBoundingClientRect().top : 0;
    const viewBottom = scroller ? scroller.getBoundingClientRect().bottom : window.innerHeight;
    // 時間軸の見出し (縦 sticky) の下を「見える上端」とする。
    const headerH = headerRef.current?.getBoundingClientRect().height ?? 0;
    const rowTop = rowEl.getBoundingClientRect().top;
    if (anchor && anchor.key === selectedRowKey) {
      // タイムラインで押した: 押した行を押した時の位置に留める。
      scrollByY(scroller, rowTop - anchor.top);
    } else if (keyChanged || rowTop < viewTop + headerH || rowTop >= viewBottom) {
      // トレイなど外から選んだ: その行へスクロール (同じ行で見えているなら動かさない
      // = パネル内の順路から訪問を選んでも画面は飛ばない)。
      scrollByY(scroller, rowTop - (viewTop + headerH));
    }
    // 行を開いた直後だけ: パネルが下にはみ出すなら、行が見えている範囲でパネルを見せる。
    const panelEl = panelRef.current;
    if (keyChanged && panelEl) {
      const over = panelEl.getBoundingClientRect().bottom - viewBottom;
      const room = rowEl.getBoundingClientRect().top - (viewTop + headerH);
      if (over > 0 && room > 0) scrollByY(scroller, Math.min(over, room));
    }
  }, [selectedRowKey, selectedVisitId]);

  // 所属の拠点が変わる所に区切り行を出す (担当なし行の前は「担当なし」)。
  let prevGroup: string | null = null;

  return (
    <div ref={rootRef} className="w-max select-none px-1 pb-4" data-testid="monitor-timeline">
      {/* 時間軸ヘッダ (縦 sticky。#／職員 セルは横にも sticky) */}
      <div
        ref={headerRef}
        className="sticky top-0 z-[5] grid border-b border-border-default bg-bg-base"
        style={GRID_COLS_STYLE}
      >
        <div className="sticky left-0 z-[6] border-r border-border-default bg-bg-base p-2 text-[11px] text-text-muted">
          #／職員
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
        <div
          className="sticky left-0 min-w-[320px] px-4 py-10 text-center text-sm text-text-muted"
          style={viewportW ? { width: viewportW } : undefined}
        >
          この日の訪問はありません。
        </div>
      )}

      {rows.map((row, idx) => {
        const rowKey = monitorRowKey(row);
        const isSel = rowKey === selectedRowKey;
        const panelId = `monitor-row-panel-${rowKey}`;
        const unassigned = row.staff_id == null;
        const group = unassigned ? UNASSIGNED_ROW_KEY : (row.office_id ?? 'none');
        const groupLabel = unassigned ? '担当なし' : `所属: ${row.office_name ?? '所属なし'}`;
        const showDivider = group !== prevGroup;
        prevGroup = group;

        // 同行・副担当の訪問 (主担当の行にある訪問を薄く描く)。見つからない (拠点・異常のみの
        // 絞り込みで主担当の行が隠れている) ものは描かない。
        const companions = (row.companion_visit_ids ?? [])
          .map((vid) => visitById?.get(vid))
          .filter((v): v is MonitorVisit => v != null);
        const laneMap = assignVisitLanes([...row.visits, ...companions]);
        // laneCount は全 visit で共通 (assignVisitLanes が統一値を返す)。
        const rowLaneCount = laneMap.size > 0 ? laneMap.values().next().value!.laneCount : 1;
        const trackH = rowLaneCount * LANE_H_PX;

        // 同行 (§7.3): 行内の訪問から同行スタッフ名を重複無しで収集。
        // 1 訪問に複数名ありうる (確定#5) ため配列を優先し、無ければ単数へ落とす。
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
        // 行ヘッダの札: その人がこの日持つコース (BE が重複なし・初出順で返す) +
        // 予定外があれば「予定外」。コースの担当と違う訪問を含む札には ⚠。
        const mismatchCourseIds = new Set(
          row.visits.filter((v) => v.course_staff_mismatch).map((v) => v.course_id),
        );
        const hasUnplanned = row.visits.some((v) => v.is_unplanned);
        const override = row.day_override ?? null;
        const overrideText =
          override?.kind === 'off'
            ? '休み'
            : override?.kind === 'custom_time'
              ? `時間変更 ${override.start_time ?? ''}–${override.end_time ?? ''}`
              : null;
        const subText = [
          unassigned ? '担当が決まっていない訪問' : (row.office_name ?? '所属なし'),
          row.visits.length > 0 ? `${row.visits.length} 件` : '訪問なし',
          overrideText,
        ]
          .filter(Boolean)
          .join(' ・ ');
        const selectRow = () => {
          rememberAnchor(rowKey);
          onSelectRow(rowKey);
        };
        // 行の中のカードを押したとき: 同じ行が開く/開いたままなので、行の位置を保つ。
        const selectOwnVisit = (visitId: string) => {
          rememberAnchor(rowKey);
          onSelectVisit(visitId);
        };

        return (
          <Fragment key={rowKey}>
            {showDivider && (
              <div
                className="border-b border-border-default bg-bg-muted"
                data-testid={`monitor-office-divider-${group}`}
              >
                <span
                  className="sticky left-0 block border-r border-border-default bg-bg-muted px-2.5 py-0.5 text-[11px] font-bold text-text-secondary"
                  style={{ width: LABEL_COL_W }}
                >
                  {groupLabel}
                </span>
              </div>
            )}
            <div
              ref={(el) => {
                if (el) rowEls.current.set(rowKey, el);
                else rowEls.current.delete(rowKey);
              }}
              role="button"
              tabIndex={0}
              aria-expanded={isSel}
              aria-controls={isSel && renderRowPanel ? panelId : undefined}
              data-testid={`monitor-row-${idx}`}
              data-row-key={rowKey}
              onClick={selectRow}
              onKeyDown={(e) => {
                // 行の中のボタン (訪問カード・同行カード) で押したキーはそのボタンに任せる。
                if (e.target !== e.currentTarget) return;
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  selectRow();
                }
              }}
              className={cn(
                'group grid min-h-[66px] cursor-pointer border-b border-border-default/60 transition-[opacity,background] duration-150',
                isSel ? 'bg-brand-primary-light' : 'hover:bg-bg-muted',
                hasSelection && !isSel ? 'opacity-40' : '',
              )}
              style={{
                ...GRID_COLS_STYLE,
                ...(rowLaneCount > 1 ? { minHeight: trackH } : {}),
              }}
            >
              {/* 左: 行番号 (M-4a: 非選択時は職員の性別色のバッジ) + 職員。
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
                  {/* 1行目=職員 / 2行目=所属・件数 (・休み) / 3行目=コースの札。 */}
                  <span
                    className={cn(
                      'block truncate text-[13px] font-semibold',
                      isSel ? 'text-brand-primary-hover' : 'text-text-primary',
                    )}
                    title={row.staff_name ?? '担当が決まっていない訪問です'}
                  >
                    {row.staff_name ?? '担当なし'}
                    {unassigned && (
                      <span
                        className="ml-1 font-bold text-warning"
                        data-testid="monitor-row-unassigned-warning"
                        title="担当が決まっていない訪問です（訪問にもコースにも担当がいません）"
                      >
                        ⚠
                      </span>
                    )}
                  </span>
                  <span
                    className="block truncate text-[11px] text-text-muted"
                    data-testid={`monitor-row-sub-${rowKey}`}
                  >
                    {subText}
                  </span>
                  {(row.course_tags?.length ?? 0) > 0 || hasUnplanned ? (
                    <span
                      className="mt-0.5 flex flex-wrap gap-[3px]"
                      data-testid={`monitor-row-tags-${rowKey}`}
                    >
                      {(row.course_tags ?? []).map((t) => (
                        <CourseTagChip
                          key={t.course_id}
                          label={t.label}
                          officeId={t.office_id}
                          officeIds={officeIds}
                          mismatch={mismatchCourseIds.has(t.course_id)}
                        />
                      ))}
                      {hasUnplanned && <UnplannedChip />}
                    </span>
                  ) : null}
                  {/* 新人同行 (§7.3): 情報ラベル。選択/非選択どちらでも表示。 */}
                  {accompanimentNames.length > 0 && (
                    <span
                      className="block truncate text-[11px] font-medium text-info"
                      data-testid={`monitor-row-accompaniment-${rowKey}`}
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
                {/* 休み・時間変更 (staff_weekly_overrides): 勤務外をハッチ帯で示す。 */}
                {override && <OffDutyBands override={override} trackH={trackH} rowKey={rowKey} />}
                {/* M-4b: 会議・イベント帯 (藤色・表示専用・カイポケ反映外)。
                    空き時間の「なぜ空いているか」を説明する。カード (z-[2]) の下。 */}
                {(row.staff_ids ?? [])
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
                          height: trackH - 9,
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
                {row.visits.map((v, i) => {
                  const li = laneMap.get(v.visit_id) ?? { lane: 0, laneCount: 1 };
                  // 拠点をまたぐ次の訪問の前に「→ 都賀へ」(その人の 1 日の時刻順)。
                  const next = row.visits[i + 1];
                  const hopTo =
                    next &&
                    v.course_office_id &&
                    next.course_office_id &&
                    next.course_office_id !== v.course_office_id
                      ? (next.course_office_name ?? null)
                      : null;
                  return (
                    <VisitBars
                      key={v.visit_id}
                      visit={v}
                      lane={li.lane}
                      nowMinutes={nowMinutes}
                      isSelected={v.visit_id === selectedVisitId}
                      onSelect={selectOwnVisit}
                      meta={patientMetaById?.get(v.patient_id)}
                      dateIso={dateIso}
                      hopToOfficeName={hopTo}
                    />
                  );
                })}
                {companions.map((v) => {
                  const li = laneMap.get(v.visit_id) ?? { lane: 0, laneCount: 1 };
                  return (
                    <CompanionBar
                      key={`companion-${v.visit_id}`}
                      visit={v}
                      lane={li.lane}
                      onSelect={onSelectVisit}
                    />
                  );
                })}
              </div>
            </div>
            {/* 行の下に開くパネル (設計 §4 決定 #2)。横スクロールしても左に留まり、
                幅はスクロール領域の見えている幅。行のクリック扱いにはしない。 */}
            {/* select-text: タイムライン全体の select-none の中でも、パネルの文字は選べる。 */}
            {isSel && renderRowPanel && (
              <div
                ref={panelRef}
                id={panelId}
                role="region"
                aria-label={`${row.staff_name ?? '担当なし'}の地図と訪問`}
                data-testid="monitor-row-panel"
                className="sticky left-0 z-[4] select-text border-b-2 border-t border-b-brand-primary border-t-brand-primary-light bg-bg-muted"
                style={viewportW ? { width: viewportW } : undefined}
              >
                {renderRowPanel(row)}
              </div>
            )}
          </Fragment>
        );
      })}
    </div>
  );
}

/** 休み (終日) / 時間変更 (勤務時間の外側) のハッチ帯。 */
function OffDutyBands({
  override,
  trackH,
  rowKey,
}: {
  override: NonNullable<MonitorStaffRow['day_override']>;
  trackH: number;
  rowKey: string;
}) {
  const bands: { from: number; to: number; label: string | null }[] = [];
  if (override.kind === 'off') {
    bands.push({ from: TL_START_MIN, to: TL_END_MIN, label: '休み（終日）' });
  } else if (override.kind === 'custom_time' && override.start_time && override.end_time) {
    const s = hmToMinutes(override.start_time);
    const e = hmToMinutes(override.end_time);
    if (s > TL_START_MIN) bands.push({ from: TL_START_MIN, to: s, label: null });
    if (e < TL_END_MIN) bands.push({ from: e, to: TL_END_MIN, label: null });
  }
  const title =
    override.kind === 'off'
      ? `休み${override.reason ? `（${override.reason}）` : ''}`
      : `時間変更 ${override.start_time ?? ''}–${override.end_time ?? ''}${
          override.reason ? `（${override.reason}）` : ''
        }`;
  return (
    <>
      {bands.map((b, i) => {
        const l = minutesToPct(b.from);
        const w = Math.max(minutesToPct(b.to) - l, 0);
        if (w <= 0) return null;
        return (
          <div
            key={i}
            data-testid={`monitor-offduty-${rowKey}`}
            className="pointer-events-none absolute z-[1] flex items-center overflow-hidden rounded-md border border-l-[3px] px-1.5 text-[12px] font-bold"
            style={{
              left: `${l}%`,
              width: `${w}%`,
              top: 4,
              height: trackH - 9,
              backgroundImage: OFFDUTY_BG,
              borderColor: 'var(--border-strong)',
              borderLeftColor: 'var(--sched-offduty-ink)',
              color: 'var(--sched-neutral-ink)',
            }}
            title={title}
          >
            {b.label}
          </div>
        );
      })}
    </>
  );
}

/**
 * 同行・副担当として関わる訪問の薄いカード (設計 §1 仮置き「同行者の行にも薄く出す」)。
 * 実績レールは描かない (状態は主担当の行が正)。押すと主担当側の訪問を選ぶ。
 */
function CompanionBar({
  visit,
  lane,
  onSelect,
}: {
  visit: MonitorVisit;
  lane: number;
  onSelect: (visitId: string) => void;
}) {
  const pos = lanePos(lane);
  const ps = hmToMinutes(visit.start_time);
  const pe = hmToMinutes(visit.end_time);
  const pL = minutesToPct(ps);
  const pW = Math.max(minutesToPct(pe) - pL, 1.5);
  return (
    <button
      type="button"
      data-testid={`monitor-bar-companion-${visit.visit_id}`}
      data-lane={lane}
      onClick={(e) => {
        e.stopPropagation();
        onSelect(visit.visit_id);
      }}
      title={`同行・副担当 ${visit.start_time}–${visit.end_time} ${visit.patient_name ?? ''}${
        visit.staff_name ? `｜担当: ${visit.staff_name}` : ''
      }（押すと担当の行で開きます）`}
      className="absolute z-[2] flex flex-col justify-center gap-px overflow-hidden rounded-md border border-dashed border-border-strong bg-bg-base/70 px-1.5 text-left text-text-secondary opacity-70 hover:opacity-100"
      style={{ left: `${pL}%`, width: `${pW}%`, top: pos.cardTop, height: pos.cardH }}
    >
      <span className="flex min-w-0 items-center gap-1">
        <span className="shrink-0 rounded-full bg-info-bg px-1 py-px text-[9px] font-bold text-info-strong">
          同行
        </span>
        <span className="min-w-0 truncate text-[11px] font-bold leading-tight">
          {visit.patient_name ?? '—'}
        </span>
      </span>
      <span className="flex min-w-0 items-center gap-1 text-[9px] leading-tight">
        <span className="tnum shrink-0 font-semibold">
          {visit.start_time}–{visit.end_time}
        </span>
        {visit.staff_name ? (
          <span className="min-w-0 truncate">担当 {visit.staff_name}</span>
        ) : null}
      </span>
    </button>
  );
}

/**
 * 実績が確定した phase (訪問済み / 不在 / 取消)。ここには「入院中」バッジを出さない
 * — 過去の実績に「稼働中でない」と書くのは嘘になる (design 2026-09-09 §3-4)。
 * MonitorVisit は `visits.status` を持たない (phase が時間進捗の唯一の情報源)。
 */
const MONITOR_SETTLED_PHASES = new Set(['done', 'no_show', 'cancelled']);

/**
 * モニターのバーに出す表示区分。まだ訪問前 / 訪問中 (future / awaiting /
 * inprogress / missing) の予定にだけ非稼働バッジを出す。
 */
function inactiveKind(visit: MonitorVisit, dateIso: string | null): VisitDisplayKind {
  if (MONITOR_SETTLED_PHASES.has(visit.phase)) return 'normal';
  // 日付条件 (2026-09-10): ステータスを変えた日より前の予定にはバッジを出さない。
  // MonitorVisit は日付を持たない (モニターは 1 日単位) ので親から流す。
  return classifyVisitDisplay({ ...visit, visit_date: dateIso }, { showInactive: true });
}

function VisitBars({
  visit,
  lane,
  nowMinutes,
  isSelected,
  onSelect,
  meta,
  dateIso,
  hopToOfficeName,
}: {
  visit: MonitorVisit;
  lane: number;
  nowMinutes: number;
  isSelected: boolean;
  onSelect: (visitId: string) => void;
  /** M-4a: 性別ウォッシュ・📍住所 (未指定=中立色)。 */
  meta?: MonitorPatientMeta;
  /** 表示中の日付 (YYYY-MM-DD)。「入院中」バッジの日付条件に使う。 */
  dateIso: string | null;
  /** 次の訪問で拠点が変わるなら、その拠点名 (「→ 都賀へ」)。 */
  hopToOfficeName: string | null;
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

  // 実績時刻 (調整後。無ければ読取時刻・設計 2026-09-30 §8-1)。バーの位置と幅・併記・
  // ツールチップはすべてこの 2 つから描く (打刻の scanned_at を直接読まない)。
  const arrivalIso = actualArrivalIso(visit);
  const departureIso = actualDepartureIso(visit);
  const arrived = arrivalIso != null;
  const hasActual = arrived || status === 'missing';

  // 実績バーの開始/幅。到着あり=到着〜(退出 or now)、未訪問=予定区間に赤ハッチ。
  let actLeft = pL;
  let actWidth = Math.max(pW, 2.5);
  let actLabel = '';
  // 前日以前で退出が無い =「退出未記録」。今の時刻まで伸ばさず、予定の長さぶんで止める
  // (pc-actual-time-edit-design Q4)。
  const departureMissing = isDepartureMissing(visit);
  if (arrived) {
    const arrMin = arrivalIso ? isoToMinutesJst(arrivalIso) : ps;
    const endMin = departureIso
      ? isoToMinutesJst(departureIso)
      : departureMissing
        ? arrMin + Math.max(pe - ps, 30)
        : nowMinutes;
    actLeft = minutesToPct(arrMin);
    actWidth = Math.max(minutesToPct(endMin) - actLeft, 2.5);
    if (departureMissing) {
      actLabel = DEPARTURE_MISSING_LABEL;
    } else if (status === 'mismatch' && visit.arrival?.distance_m != null) {
      actLabel = `${Math.round(visit.arrival.distance_m)}m`;
    } else if (status === 'review' && visit.arrival_delay_min != null) {
      actLabel = `+${visit.arrival_delay_min}分`;
    }
  }

  const dn = visit.distance_to_next_m;
  // M-4a: 予定カードの性別ウォッシュ (患者マスタ FE join。未指定/未登録=中立色)。
  const pal = genderPalette(meta?.sex ?? null);
  // §6: 予定外訪問のカードは性別ウォッシュではなく --unplanned 系の配色。
  // 同じ職員の行に予定の訪問と並ぶので、「予定外」の札と合わせて見分けられるようにする。
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
  // 打刻の実時刻 (お客様要望 2026-09-18)。予定カードのバー位置・幅は**変えず**、
  // 2 行目とツールチップに「✓12:56–13:40 / ▶12:56〜」を併記する。時刻は shrink-0 で
  // 途中切れさせない (狭いカードで詰まるのは氏名・住所側だけ)。
  // 未訪問 (no_show) は抑止する: モバイルと同じ理由 — 「未訪問」と到着時刻を
  // 並べない (BE レビュー申し送り 2026-09-18)。詳細パネルには従来どおり出る。
  // ただし未訪問の記録の後に管理者が到着を手で入れた訪問は、訪問した扱いなので出す
  // (未訪問の記録は詳細パネルに履歴として残る・PO 決定 2026-10-07)。
  const actual =
    visit.phase === 'no_show' || (visit.no_show != null && !visit.arrival_manual)
      ? null
      : actualTimeParts(arrivalIso, departureIso);
  // 「調整」「手入力」の印 (合わせる操作は詳細パネルの枠・打刻履歴・スマホから)。
  // 実績を併記しない訪問 (未訪問) には出さない。
  const adjustNotes = actual ? adjustmentNotes(visit) : [];
  const adjustTitle = adjustNotes.map((n) => n.text).join(' / ');

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
      {/* 次までの距離 (カード右横)。拠点をまたぐ次の訪問の前は「→ 都賀へ」を目立たせる。 */}
      {hopToOfficeName ? (
        <div
          data-testid={`monitor-hop-${visit.visit_id}`}
          className="pointer-events-none absolute whitespace-nowrap pl-1 text-[10px] font-bold text-warning-strong [text-shadow:0_0_3px_#fff,0_0_3px_#fff]"
          style={{ left: `${minutesToPct(pe)}%`, top: pos.distTop }}
          title={dn != null ? `次まで ${formatDistance(dn)}` : undefined}
        >
          → {hopToOfficeName}へ
        </div>
      ) : dn != null ? (
        <div
          className="pointer-events-none absolute whitespace-nowrap text-[10px] text-text-muted [text-shadow:0_0_3px_#fff,0_0_3px_#fff]"
          style={{ left: `${minutesToPct(pe)}%`, top: pos.distTop }}
        >
          →{formatDistance(dn)}
        </div>
      ) : null}
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
          visit.course_tag
            ? `｜コース: ${visit.course_tag}${visit.course_staff_mismatch ? '（コースの担当と違う）' : ''}`
            : ''
        }${visit.is_substitute ? `｜${substituteTitle(visit)}` : ''}${
          visit.is_unplanned && visit.actual_staff_name ? `｜実績: ${visit.actual_staff_name}` : ''
        }${
          // 時刻側は「打刻」の 1 本に統一する (既存の「実績: 打刻者名」と混ざらない)。
          actual ? `｜打刻: ${actual.compactRange}` : ''
        }${
          // 調整のある訪問: 読取時刻・理由・誰がいつ合わせたか。
          adjustTitle ? `｜調整: ${adjustTitle}` : ''
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
          {/* コースの札はカードに出さない (行ヘッダだけ・PO 2026-10-01)。
              予定外は行ヘッダで分からないので「予定外」の札を残す (設計 §4)。 */}
          {visit.is_unplanned ? (
            <UnplannedChip testId={`monitor-bar-tag-${visit.visit_id}`} />
          ) : null}
          <i
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ background: visit.is_unplanned ? 'var(--unplanned)' : pal.bar }}
            aria-hidden="true"
          />
          <span className="min-w-0 truncate text-[11px] font-bold leading-tight">
            {visit.patient_name ?? '—'}
          </span>
          {/* 非稼働患者のバッジ (「入院中」等・design 2026-09-09 §3-4)。
              連動取消はモニターに来ない (BE + クエリ層で除外済み)。 */}
          <InactiveVisitBadge
            visit={visit}
            kind={inactiveKind(visit, dateIso)}
            className="rounded-full py-px text-[9px]"
            testId={`monitor-bar-inactive-${visit.visit_id}`}
          />
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
          {/* 実績の時刻を合わせた訪問の印。2 行目の**先頭**に置く: 35 分枠のカードは
              108〜126px しかなく、時刻の後ろに置くとカードの外へはみ出して見えない
              (結合検証 2026-10-01)。詳細は title に出す。注意色にはしない
              (遅れて記録されるのは看護師の誤りではない)。 */}
          {adjustNotes.length > 0 ? (
            <span
              data-testid={`monitor-bar-adjusted-${visit.visit_id}`}
              title={adjustTitle}
              className="shrink-0 rounded-full bg-brand-primary-50 px-1 py-px font-bold text-brand-primary-hover"
            >
              {adjustNotes.every((n) => n.manual) ? '手入力' : '調整'}
            </span>
          ) : null}
          {/* サインで記録した退出 (signature-checkin-design §5-1)。注意色にはしない。 */}
          {visit.departure?.checkin_source === 'signature' ? (
            <span
              data-testid={`monitor-bar-signature-${visit.visit_id}`}
              title="退出は利用者さんのサインで記録しました"
              className="shrink-0 rounded-full bg-info-bg px-1 py-px font-bold text-info-strong"
            >
              サイン
            </span>
          ) : null}
          <span className="tnum shrink-0 font-semibold">
            {visit.start_time}–{visit.end_time}
          </span>
          {/* 打刻の実時刻。予定の後ろに併記する (予定は書き換えない)。
              ✓=退出まで確定 / ▶=到着のみ (訪問中)。 */}
          {actual ? (
            <span
              data-testid={`monitor-bar-actual-time-${visit.visit_id}`}
              className="tnum shrink-0 font-semibold"
            >
              {actual.done ? '✓' : '▶'}
              {actual.compactRange}
            </span>
          ) : null}
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
          data-departure-missing={departureMissing || undefined}
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
          title={`${visit.patient_name ?? ''} ${actLabel}${visit.reviewed ? ' ✓確認済' : ''}${
            adjustTitle ? ` ｜調整: ${adjustTitle}` : ''
          }`}
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
