'use client';

/**
 * CareFlow Mobile — 現場ボード (Warm & Human aligned, Phase2-3c 実データ接続)
 *
 * 電話/タブレット向けのフィールドボード本体。週切替 + 拠点タブ + 曜日ステッパーで
 * `GET /api/v1/schedule/v2/board` を再取得し、コース毎に実訪問を実時刻
 * (start_time〜end_time) で表示する。容量 filled/6・空き枠 (remaining)・同住所
 * (same_address_group_id) 連結を実データで描画する。
 *
 * 旧 Phase 1 のモック (CF_WEEK / CF_PATIENTS / CF_PENDING / RANK_*) は撤去済み。
 * 意匠 (Warm パレット・一覧レイアウト・実時刻表示・同住所/空き枠の見た目) は維持。
 */

import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import {
  Heart,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Plus,
  ClipboardCheck,
  MapPin,
  Check,
} from 'lucide-react';

import { useFieldBoard, toWeekStart, toIsoYearWeek } from '@/lib/queries/fieldBoard';
import { usePendingRequests } from '@/lib/queries/pending_requests';
import type { BoardCell, BoardCourse, BoardVisit } from '@/lib/schemas/v2/board';

import { CF_THEME, CF_DOWS, cc } from './theme';
import { KarteSheet, SuggestSheet, Toast } from './FieldSheets';
import { ApprovePanel } from './ApprovePanel';

const { TEAL, TEAL_DEEP, TERRA, TERRA_DEEP, INK, INK2, INK3, CREAM, LINE, PANEL } = CF_THEME;

const WEEKLEN = 7;

// ============================ 時間ユーティリティ ============================

/** 'HH:MM' → 0時起点の分。不正値は null。 */
function parseHM(s: string | null | undefined): number | null {
  if (!s) return null;
  const m = /^(\d{1,2}):(\d{2})$/.exec(s.trim());
  if (!m) return null;
  const h = Number(m[1]);
  const mm = Number(m[2]);
  if (h < 0 || h > 47 || mm < 0 || mm > 59) return null;
  return h * 60 + mm;
}

/** 'HH:MM〜HH:MM' の実時刻ラベル (start/end をそのまま使う)。 */
function visitTimeLabel(v: BoardVisit): string {
  return `${v.start_time}〜${v.end_time}`;
}

/** 0時起点の分 → 'HH:MM'。 */
function fmtHM(min: number): string {
  const h = Math.floor(min / 60);
  const m = min % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

// ============================ 営業枠 / 空き時間帯 ============================

/**
 * コースの営業枠 (フロント表示用の目安。バックエンド定数の複製)。
 * AM 09:30–12:00 / PM 13:00–18:00。昼休み 12:00–13:00 はブロック間で非営業。
 * 厳密な配置可否 (移動時間等) は提案フロー (propose-slots) が担保する。
 */
const BUSINESS_BLOCKS: ReadonlyArray<readonly [number, number]> = [
  [9 * 60 + 30, 12 * 60], // 09:30–12:00
  [13 * 60, 18 * 60], // 13:00–18:00
];

/**
 * これより短い gap は空き帯として表示しない (分)。
 * 60分 = 移動 + 約35分業務 + バッファーで概ね 1 時間確保が必要なため。
 */
const MIN_FREE_GAP_MIN = 60;

/** 営業枠から既存 visit の占有を除いた空き時間帯 (≥MIN_FREE_GAP_MIN)。 */
export interface FreeGap {
  /** gap 開始 (0時起点の分。interleave の並べ替えキー)。 */
  startMin: number;
  /** gap 終了 (0時起点の分)。 */
  endMin: number;
  /** 'HH:MM〜HH:MM' の表示ラベル。 */
  label: string;
}

/**
 * コースの営業枠から既存 visit の占有 [start, end) を除いた空き時間帯を算出する。
 * 戻り値は MIN_FREE_GAP_MIN 以上の gap のみ (短すぎる gap は省略)、start 昇順。
 */
function computeFreeGaps(visits: BoardVisit[]): FreeGap[] {
  const occupied: Array<[number, number]> = [];
  for (const v of visits) {
    const s = parseHM(v.start_time);
    const e = parseHM(v.end_time);
    if (s === null || e === null || e <= s) continue;
    occupied.push([s, e]);
  }
  occupied.sort((a, b) => a[0] - b[0]);

  const gaps: FreeGap[] = [];
  const push = (s: number, e: number) => {
    if (e - s >= MIN_FREE_GAP_MIN) {
      gaps.push({ startMin: s, endMin: e, label: `${fmtHM(s)}〜${fmtHM(e)}` });
    }
  };
  for (const [blockStart, blockEnd] of BUSINESS_BLOCKS) {
    let cursor = blockStart;
    for (const [s, e] of occupied) {
      if (e <= cursor || s >= blockEnd) continue; // ブロック外は無視
      const segStart = Math.max(s, blockStart);
      push(cursor, segStart);
      cursor = Math.max(cursor, Math.min(e, blockEnd));
    }
    push(cursor, blockEnd);
  }
  return gaps;
}

// ============================ 日付フォーマット ============================

const MD_FORMAT = (iso: string): string => {
  // 'YYYY-MM-DD' → 'M/D'
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  return `${Number(m[2])}/${Number(m[3])}`;
};

// ============================ App ============================

export function FieldBoard({ topPad = 16 }: { topPad?: number }) {
  // 週 state: 月曜起点の Date を持ち、ISO 年/週へ変換して API に渡す。
  const [weekStart, setWeekStart] = useState<Date>(() => toWeekStart(new Date()));
  const { isoYear, isoWeek } = useMemo(() => toIsoYearWeek(weekStart), [weekStart]);

  const [officeId, setOfficeId] = useState<string | null>(null);
  const [dayIdx, setDayIdx] = useState(0); // 0=月..6=日
  const [approve, setApprove] = useState(false);
  const [karte, setKarte] = useState<BoardVisit | null>(null);
  const [sheet, setSheet] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  // 全拠点ぶんを 1 度に取得 (officeId 指定なし)。拠点タブはレスポンスの offices[] から構築。
  const boardQuery = useFieldBoard({ isoYear, isoWeek, officeId: null });
  const board = boardQuery.data;

  const offices = board?.offices ?? [];

  // office 未選択 / 一覧変化時は先頭拠点を選ぶ。
  useEffect(() => {
    if (offices.length === 0) return;
    if (!officeId || !offices.some((o) => o.office_id === officeId)) {
      setOfficeId(offices[0]?.office_id ?? null);
    }
    // offices 配列の中身が変わったときのみ評価。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offices.map((o) => o.office_id).join(',')]);

  // 選択拠点の曜日 → セル を引く索引。
  const cellsByDay = useMemo(() => {
    const map = new Map<number, BoardCell>();
    if (!board || !officeId) return map;
    for (const cell of board.board) {
      if (cell.office_id === officeId) map.set(cell.weekday, cell);
    }
    return map;
  }, [board, officeId]);

  const cell = cellsByDay.get(dayIdx) ?? null;
  // 参照安定化 (空配列の再生成で useMemo 依存が毎回変わるのを防ぐ)。
  const courses = useMemo(() => cell?.courses ?? [], [cell]);
  const closed = cell?.closed ?? false;

  // 当日にコースが無い拠点なら、最初に開いている曜日へジャンプ。
  useEffect(() => {
    if (!board || !officeId) return;
    const current = cellsByDay.get(dayIdx);
    const hasCourses = (current?.courses.length ?? 0) > 0;
    if (hasCourses || current?.closed) return;
    for (let d = 0; d < WEEKLEN; d++) {
      const c = cellsByDay.get(d);
      if ((c?.courses.length ?? 0) > 0) {
        setDayIdx(d);
        return;
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [officeId, board]);

  const showToast = (msg: string) => {
    setToast(msg);
    window.clearTimeout((window as unknown as { __cfT?: number }).__cfT);
    (window as unknown as { __cfT?: number }).__cfT = window.setTimeout(() => setToast(null), 2200);
  };

  const goWeek = (delta: number) => {
    setWeekStart((w) => {
      const x = new Date(w);
      x.setDate(x.getDate() + delta * 7);
      return x;
    });
    showToast(delta < 0 ? '◀ 前の週' : '▶ 次の週');
  };

  // 当日のヘッダー集計 (実訪問件数 + 残り空き枠合計)。
  const visitsCount = courses.reduce((n, co) => n + co.capacity.filled, 0);
  const roomCount = courses.reduce((n, co) => n + co.capacity.remaining, 0);

  // 同住所相手解決用: 当日 (選択拠点) の全 visit を group_id でまとめる。
  const sameAddressGroups = useMemo(() => buildSameAddressGroups(courses), [courses]);

  // 承認待ち件数バッジ (実 pending-requests, pending 状態のみ)。
  const pendingQuery = usePendingRequests({ status: 'pending', limit: 100 });
  const pendingCount = pendingQuery.data?.items.length ?? 0;

  const weekLabel = board ? `${isoYear}年 第${isoWeek}週` : `第${isoWeek}週`;
  const weekRange = useMemo(() => {
    const start = MD_FORMAT(weekStart.toISOString().slice(0, 10));
    const end = new Date(weekStart);
    end.setDate(end.getDate() + 6);
    return `${start} - ${MD_FORMAT(end.toISOString().slice(0, 10))}`;
  }, [weekStart]);

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: CREAM,
        position: 'relative',
        overflow: 'hidden',
        fontFamily: 'var(--font-sans)',
      }}
    >
      <Header
        offices={offices}
        officeId={officeId}
        setOfficeId={setOfficeId}
        approve={approve}
        setApprove={setApprove}
        pendingCount={pendingCount}
        onNew={() => setSheet(true)}
        topPad={topPad}
      />

      <DayStepper
        dayIdx={dayIdx}
        setDayIdx={setDayIdx}
        dateLabel={board?.weekdays[dayIdx]?.date ? MD_FORMAT(board.weekdays[dayIdx]!.date) : ''}
        visitsCount={visitsCount}
        roomCount={roomCount}
        closed={closed}
        weekLabel={weekLabel}
        weekRange={weekRange}
        goWeek={goWeek}
      />

      <div
        className="cf-scroll"
        style={{ flex: 1, overflow: 'auto', WebkitOverflowScrolling: 'touch' }}
      >
        {approve ? (
          <ApprovePanel onToast={showToast} />
        ) : boardQuery.isLoading ? (
          <LoadingState />
        ) : boardQuery.isError ? (
          <ErrorState onRetry={() => void boardQuery.refetch()} />
        ) : closed ? (
          <ClosedState dayIdx={dayIdx} />
        ) : courses.length === 0 ? (
          <EmptyState dayIdx={dayIdx} />
        ) : (
          <AgendaBoard
            courses={courses}
            sameAddressGroups={sameAddressGroups}
            onKarte={setKarte}
            onEmpty={() => setSheet(true)}
          />
        )}
        <div style={{ height: 28 }} />
      </div>

      {/* scrim */}
      {(karte || sheet) && (
        <div
          onClick={() => {
            setKarte(null);
            setSheet(false);
          }}
          style={{
            position: 'absolute',
            inset: 0,
            background: 'rgba(28,25,23,0.42)',
            zIndex: 40,
            backdropFilter: 'blur(2px)',
          }}
        />
      )}

      {karte && (
        <KarteSheet
          visit={karte}
          officeName={offices.find((o) => o.office_id === officeId)?.office_name ?? ''}
          sameAddressGroups={sameAddressGroups}
          onClose={() => setKarte(null)}
          onOpenVisit={setKarte}
        />
      )}
      {sheet && (
        <SuggestSheet
          isoYear={isoYear}
          isoWeek={isoWeek}
          officeId={officeId}
          onClose={() => setSheet(false)}
          onToast={showToast}
        />
      )}

      {toast && <Toast msg={toast} />}
    </div>
  );
}

// ============================ 同住所グループ ============================

export interface SameAddressGroups {
  /** group_id → その group に属する visit 一覧 (同コース内, start 昇順想定) */
  byGroup: Map<string, BoardVisit[]>;
  /** visit_id → group_id */
  groupOf: Map<string, string>;
}

function buildSameAddressGroups(courses: BoardCourse[]): SameAddressGroups {
  const byGroup = new Map<string, BoardVisit[]>();
  const groupOf = new Map<string, string>();
  for (const co of courses) {
    for (const v of co.visits) {
      if (!v.same_address_group_id) continue;
      groupOf.set(v.visit_id, v.same_address_group_id);
      const arr = byGroup.get(v.same_address_group_id) ?? [];
      arr.push(v);
      byGroup.set(v.same_address_group_id, arr);
    }
  }
  return { byGroup, groupOf };
}

// ============================ Header ============================

interface OfficeOpt {
  office_id: string;
  office_name: string;
}

// ============================ 拠点プルダウン ============================

/**
 * ヘッダー内の拠点セレクタ。`📍 {現在の拠点名} ▾` ボタン + 直下ポップオーバー一覧。
 * 旧・横並びピルを置換。外側クリック / 再タップ / 項目選択でクローズし、選択は setOfficeId。
 * offices は API 由来 (board.offices)。将来拠点が増えてもそのまま列挙する。
 */
function OfficePicker({
  offices,
  officeId,
  setOfficeId,
  accentInk,
}: {
  offices: OfficeOpt[];
  officeId: string | null;
  setOfficeId: (id: string) => void;
  accentInk: string;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  const current = offices.find((o) => o.office_id === officeId);
  const currentName = current?.office_name || '拠点';
  const loading = offices.length === 0;

  // 外側クリック / タップで閉じる (ポップオーバー外を pointerdown したら close)。
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent | TouchEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('pointerdown', onDoc);
    return () => document.removeEventListener('pointerdown', onDoc);
  }, [open]);

  return (
    <div
      ref={wrapRef}
      style={{ position: 'relative', flex: '1 1 auto', minWidth: 0, display: 'flex' }}
    >
      <button
        type="button"
        onClick={() => !loading && setOpen((v) => !v)}
        disabled={loading}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`拠点を選択 (現在: ${currentName})`}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 5,
          minHeight: 34,
          maxWidth: '100%',
          padding: '5px 10px',
          borderRadius: 999,
          background: open ? '#fff' : 'rgba(255,255,255,0.16)',
          color: open ? accentInk : '#fff',
          fontFamily: 'var(--font-serif)',
          fontSize: 13,
          fontWeight: 600,
          boxShadow: open ? '0 2px 6px rgba(0,0,0,0.12)' : 'none',
          minWidth: 0,
        }}
      >
        <MapPin size={14} strokeWidth={2.2} style={{ flex: '0 0 auto' }} />
        <span
          style={{
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            minWidth: 0,
          }}
        >
          {loading ? '拠点を読込中…' : currentName}
        </span>
        <ChevronDown
          size={15}
          strokeWidth={2.4}
          style={{
            flex: '0 0 auto',
            transition: 'transform .18s',
            transform: open ? 'rotate(180deg)' : 'none',
          }}
        />
      </button>

      {open && !loading && (
        <div
          role="menu"
          aria-label="拠点一覧"
          style={{
            position: 'absolute',
            top: 'calc(100% + 6px)',
            left: 0,
            minWidth: 168,
            maxWidth: 240,
            background: '#fff',
            borderRadius: 14,
            boxShadow: '0 10px 28px rgba(28,25,23,0.22)',
            border: `1px solid ${LINE}`,
            padding: 5,
            zIndex: 30,
            display: 'flex',
            flexDirection: 'column',
            gap: 2,
          }}
        >
          {offices.map((o) => {
            const on = o.office_id === officeId;
            return (
              <button
                key={o.office_id}
                type="button"
                role="menuitemradio"
                aria-checked={on}
                onClick={() => {
                  setOfficeId(o.office_id);
                  setOpen(false);
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  width: '100%',
                  textAlign: 'left',
                  minHeight: 38,
                  padding: '7px 10px',
                  borderRadius: 10,
                  background: on ? '#EBF9F7' : 'transparent',
                  color: on ? accentInk : INK,
                  fontFamily: 'var(--font-serif)',
                  fontSize: 14,
                  fontWeight: on ? 700 : 600,
                }}
              >
                <span
                  style={{ width: 16, flex: '0 0 auto', display: 'grid', placeItems: 'center' }}
                >
                  {on && <Check size={15} strokeWidth={3} color={accentInk} />}
                </span>
                <span
                  style={{
                    flex: '1 1 0',
                    minWidth: 0,
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {o.office_name || '拠点'}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

interface HeaderProps {
  offices: OfficeOpt[];
  officeId: string | null;
  setOfficeId: (id: string) => void;
  approve: boolean;
  setApprove: (fn: (a: boolean) => boolean) => void;
  pendingCount: number;
  onNew: () => void;
  topPad?: number;
}

function Header({
  offices,
  officeId,
  setOfficeId,
  approve,
  setApprove,
  pendingCount,
  onNew,
  topPad = 16,
}: HeaderProps) {
  const bg = approve
    ? 'linear-gradient(135deg, #E0A21A 0%, #B5790A 100%)'
    : `linear-gradient(135deg, ${TEAL} 0%, ${TEAL_DEEP} 100%)`;
  const accentInk = approve ? '#9A6700' : TEAL_DEEP;
  return (
    <div
      style={{
        flex: '0 0 auto',
        // 浮きカード: 上・左右に小さめ余白を取り、周囲のクリーム背景を覗かせる。
        // 上余白は safe-area の下に 6〜8px、左右 8px。縦増分は最小限 (上に約 7px のみ)。
        padding: `${Math.max(topPad - 13, 6)}px 8px 0`,
        position: 'relative',
        zIndex: 20,
      }}
    >
      <div
        style={{
          background: bg,
          color: '#fff',
          padding: '9px 14px',
          // 四隅すべて角丸の浮きカード。
          borderRadius: 16,
          boxShadow: '0 5px 14px rgba(13,148,136,0.18)',
          position: 'relative',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: '0 0 auto' }}>
            <div
              style={{
                width: 26,
                height: 26,
                borderRadius: 8,
                background: '#fff',
                color: accentInk,
                display: 'grid',
                placeItems: 'center',
                boxShadow: '0 2px 6px rgba(0,0,0,0.14)',
              }}
            >
              <Heart size={14} strokeWidth={2.4} />
            </div>
            <div
              style={{
                fontFamily: 'var(--font-serif)',
                fontSize: 15,
                fontWeight: 700,
                lineHeight: 1,
                letterSpacing: '-0.01em',
              }}
            >
              CareFlow
              <span style={{ fontSize: 9, opacity: 0.85, fontWeight: 500, marginLeft: 4 }}>
                現場ボード
              </span>
            </div>
          </div>
          <OfficePicker
            offices={offices}
            officeId={officeId}
            setOfficeId={setOfficeId}
            accentInk={accentInk}
          />
          <div style={{ display: 'flex', gap: 7, flex: '0 0 auto' }}>
            <button onClick={onNew} style={{ ...hdrAct, background: '#fff', color: accentInk }}>
              <Plus size={15} /> 提案
            </button>
            <button
              onClick={() => setApprove((a) => !a)}
              style={{
                ...hdrAct,
                background: approve ? '#fff' : 'rgba(255,255,255,0.16)',
                color: approve ? accentInk : '#fff',
                position: 'relative',
              }}
            >
              <ClipboardCheck size={15} /> 承認
              {pendingCount > 0 && (
                <span
                  style={{
                    position: 'absolute',
                    top: -6,
                    right: -6,
                    minWidth: 18,
                    height: 18,
                    padding: '0 5px',
                    background: '#E1657F',
                    color: '#fff',
                    fontSize: 11,
                    fontWeight: 700,
                    borderRadius: 999,
                    display: 'grid',
                    placeItems: 'center',
                    border: '2px solid #fff',
                  }}
                >
                  {pendingCount}
                </span>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

const hdrAct: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 5,
  padding: '7px 13px',
  minHeight: 35,
  borderRadius: 12,
  fontSize: 13,
  fontWeight: 600,
  fontFamily: 'var(--font-serif)',
  boxShadow: '0 2px 6px rgba(0,0,0,0.10)',
};

// ============================ Day stepper (週送り + 曜日送り 統合) ============================

function DayStepper({
  dayIdx,
  setDayIdx,
  dateLabel,
  visitsCount,
  roomCount,
  closed,
  weekLabel,
  weekRange,
  goWeek,
}: {
  dayIdx: number;
  setDayIdx: (d: number) => void;
  dateLabel: string;
  visitsCount: number;
  roomCount: number;
  closed: boolean;
  weekLabel: string;
  weekRange: string;
  goWeek: (delta: number) => void;
}) {
  const go = (dir: number) => setDayIdx((dayIdx + dir + WEEKLEN) % WEEKLEN);
  const day = CF_DOWS[dayIdx] ?? '月';
  const sat = dayIdx === 5;
  const sun = dayIdx === 6;
  // 日送りは押しやすい 36px 級、週送りは小さめ 28px。
  const arrow: CSSProperties = {
    width: 36,
    height: 36,
    flex: '0 0 auto',
    borderRadius: 12,
    background: '#fff',
    border: `1px solid ${LINE}`,
    boxShadow: '0 2px 6px rgba(28,25,23,0.06)',
    display: 'grid',
    placeItems: 'center',
    color: TEAL_DEEP,
  };
  const wkArrow: CSSProperties = {
    width: 28,
    height: 28,
    flex: '0 0 auto',
    borderRadius: 9,
    background: '#fff',
    border: `1px solid ${LINE}`,
    display: 'grid',
    placeItems: 'center',
    color: TEAL_DEEP,
  };
  return (
    <div
      style={{
        flex: '0 0 auto',
        padding: '6px 12px 4px',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
      }}
    >
      {/* 週送り (小さめ) */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 3, flex: '0 0 auto' }}>
        <button onClick={() => goWeek(-1)} style={wkArrow} aria-label="前の週">
          <ChevronLeft size={16} />
        </button>
        <div style={{ textAlign: 'center', lineHeight: 1.1, minWidth: 52 }}>
          <div
            style={{
              fontFamily: 'var(--font-serif)',
              fontSize: 12.5,
              fontWeight: 700,
              color: INK,
              whiteSpace: 'nowrap',
            }}
          >
            {weekLabel.replace(/^\d+年\s*/, '')}
          </div>
          <div style={{ fontSize: 9, color: INK3, fontWeight: 600, whiteSpace: 'nowrap' }}>
            {weekRange}
          </div>
        </div>
        <button onClick={() => goWeek(1)} style={wkArrow} aria-label="次の週">
          <ChevronRight size={16} />
        </button>
      </div>

      {/* 区切り */}
      <span
        style={{
          width: 1,
          alignSelf: 'stretch',
          background: LINE,
          flex: '0 0 auto',
          margin: '2px 0',
        }}
      />

      {/* 日送り (押しやすい 36px) */}
      <button onClick={() => go(-1)} style={arrow} aria-label="前の曜日">
        <ChevronLeft size={20} />
      </button>
      <div
        style={{
          flex: 1,
          minWidth: 0,
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'baseline',
          justifyContent: 'center',
          gap: '0 6px',
          lineHeight: 1.1,
        }}
      >
        <span
          style={{
            fontFamily: 'var(--font-serif)',
            fontSize: 16,
            fontWeight: 700,
          }}
        >
          <span style={{ color: sat ? '#2F6FB0' : sun ? '#C75C77' : TEAL_DEEP }}>{day}曜</span>
          {dateLabel && (
            <span style={{ fontSize: 12, color: INK3, fontWeight: 600, marginLeft: 5 }}>
              {dateLabel}
            </span>
          )}
        </span>
        <span style={{ fontSize: 10.5, color: INK2 }}>
          {closed ? (
            '休講日'
          ) : (
            <>
              訪問 <b style={{ color: INK }}>{visitsCount}</b> 件 ・{' '}
              <span style={{ color: roomCount > 0 ? '#0E8472' : INK3, fontWeight: 700 }}>
                {roomCount > 0 ? `空き ${roomCount}つ` : '満員'}
              </span>
            </>
          )}
        </span>
      </div>
      <button onClick={() => go(1)} style={arrow} aria-label="次の曜日">
        <ChevronRight size={20} />
      </button>
    </div>
  );
}

// ============================ Patient card (shared) ============================

function PatientCard({
  visit,
  courseCode,
  inPair,
  startOverride,
  onKarte,
}: {
  visit: BoardVisit;
  /** 所属コードの course_code (色解決用。BoardCourse から明示的に渡す)。 */
  courseCode: string;
  inPair?: boolean;
  /** ペア連結で開始ラベルを上書きする場合のラベル */
  startOverride?: string;
  onKarte: (v: BoardVisit) => void;
}) {
  const k = cc(courseCode);
  const timeLabel = startOverride ?? visitTimeLabel(visit);
  return (
    <button
      onClick={() => onKarte(visit)}
      style={{
        width: '100%',
        textAlign: 'left',
        background: inPair ? '#fff' : k.soft,
        borderRadius: 12,
        borderLeft: `5px solid ${k.c}`,
        padding: '9px 12px',
        display: 'flex',
        flexDirection: 'column',
        gap: 2,
        position: 'relative',
        boxShadow: '0 1px 2px rgba(28,25,23,0.05)',
      }}
    >
      <span
        style={{
          position: 'absolute',
          top: 7,
          right: 9,
          fontSize: 9.5,
          color: INK3,
          fontWeight: 600,
        }}
      >
        {visit.slot_index + 1}枠目
      </span>
      <span
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 11,
          color: TEAL_DEEP,
          background: '#fff',
          border: `1.5px solid ${k.bg}`,
          borderRadius: 7,
          padding: '1px 7px',
          alignSelf: 'flex-start',
          fontWeight: 600,
          whiteSpace: 'nowrap',
          letterSpacing: '-0.01em',
        }}
      >
        {timeLabel}
      </span>
      <div
        style={{
          fontFamily: 'var(--font-serif)',
          fontSize: 14.5,
          fontWeight: 700,
          lineHeight: 1.15,
        }}
      >
        {visit.patient_name}
      </div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 10.5,
          color: INK2,
          flexWrap: 'wrap',
        }}
      >
        {visit.insurance && (
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              padding: '1px 7px',
              borderRadius: 999,
              background: visit.insurance === 'med' ? '#E2EDF8' : '#D7F2EE',
              color: visit.insurance === 'med' ? '#2F6FB0' : '#0E8472',
            }}
          >
            {visit.insurance === 'med' ? '医療' : '介護'}
          </span>
        )}
        <span>{visit.service_minutes}分</span>
        {inPair && (
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              padding: '1px 7px',
              borderRadius: 999,
              background: k.bg,
              color: k.c,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 2,
            }}
          >
            <MapPin size={9} />
            同住所
          </span>
        )}
      </div>
    </button>
  );
}

/**
 * ≥60分 の空き帯 1 つを表すカード。その帯の開始時刻位置 (時間順) に挿入される。
 * 文言は「この時間空いてますよ：HH:MM〜HH:MM」。タップで提案シート (onEmpty) へ。
 */
function EmptySlot({ gap, onEmpty }: { gap: FreeGap; onEmpty: () => void }) {
  return (
    <button
      onClick={onEmpty}
      style={{
        width: '100%',
        minHeight: 50,
        borderRadius: 12,
        border: `2.5px dashed ${TERRA}`,
        background:
          'repeating-linear-gradient(45deg, #FFFBF4, #FFFBF4 9px, #FDF1DF 9px, #FDF1DF 18px)',
        color: TERRA_DEEP,
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        flexWrap: 'wrap',
        padding: '8px 12px',
        fontFamily: 'var(--font-serif)',
        fontSize: 13,
        fontWeight: 700,
        textAlign: 'left',
      }}
    >
      <span
        style={{
          width: 24,
          height: 24,
          borderRadius: '50%',
          background: TERRA,
          color: '#fff',
          display: 'grid',
          placeItems: 'center',
          fontSize: 16,
          lineHeight: 1,
          flex: '0 0 auto',
        }}
      >
        ＋
      </span>
      <span style={{ flex: '1 1 0', minWidth: 0 }}>
        <span style={{ display: 'block', whiteSpace: 'normal', lineHeight: 1.25 }}>
          この時間空いてますよ：
          <span
            style={{
              fontFamily: 'var(--font-mono)',
              letterSpacing: '-0.01em',
              whiteSpace: 'nowrap',
            }}
          >
            {gap.label}
          </span>
        </span>
      </span>
    </button>
  );
}

function PairWrap({
  visits,
  courseCode,
  onKarte,
}: {
  visits: BoardVisit[];
  /** 連結バンド内カードの色解決用 course_code (BoardCourse から明示的に渡す)。 */
  courseCode: string;
  onKarte: (v: BoardVisit) => void;
}) {
  // 同住所連結バンドの所要 = 先頭 start 〜 末尾 end。
  const first = visits[0]!;
  const last = visits[visits.length - 1]!;
  const startMin = parseHM(first.start_time);
  const endMin = parseHM(last.end_time);
  const totalMin = startMin !== null && endMin !== null ? endMin - startMin : null;
  // 同住所ペアの色は固定の PLUM ではなく、そのコース色に統一する。
  const k = cc(courseCode);
  const bandLabel =
    totalMin !== null
      ? `${first.start_time}〜${last.end_time} 同時刻・連続訪問（約${totalMin}分）`
      : `${first.start_time}〜${last.end_time} 同時刻・連続訪問`;
  return (
    <div
      style={{
        border: `2.5px solid ${k.c}`,
        background: k.soft,
        borderRadius: 15,
        padding: '6px 6px 7px',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
      }}
    >
      <div
        style={{
          fontFamily: 'var(--font-serif)',
          fontSize: 11,
          color: '#fff',
          background: k.c,
          borderRadius: 8,
          padding: '5px 8px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 5,
          fontWeight: 700,
          flexWrap: 'wrap',
          textAlign: 'center',
          lineHeight: 1.3,
        }}
      >
        <span
          style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap' }}
        >
          <MapPin size={11} /> 同住所・同時
        </span>
      </div>
      {visits.map((v, i) => (
        <div key={v.visit_id}>
          <PatientCard visit={v} courseCode={courseCode} inPair onKarte={onKarte} />
          {i < visits.length - 1 && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 6,
                fontSize: 10,
                fontWeight: 700,
                color: k.c,
                marginTop: 6,
              }}
            >
              <span style={{ flex: 1, height: 2, background: k.bg, borderRadius: 2 }} />
              <span
                style={{
                  fontFamily: 'var(--font-mono)',
                  whiteSpace: 'nowrap',
                  letterSpacing: '-0.01em',
                }}
              >
                {bandLabel}
              </span>
              <span style={{ flex: 1, height: 2, background: k.bg, borderRadius: 2 }} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// コース内のスロット一覧 (実訪問 + 同住所連結 + 空き枠)。
function CourseSlots({
  co,
  sameAddressGroups,
  onKarte,
  onEmpty,
}: {
  co: BoardCourse;
  sameAddressGroups: SameAddressGroups;
  onKarte: (v: BoardVisit) => void;
  onEmpty: () => void;
}) {
  // 訪問カード / 同住所連結 / 空き帯を start_time 昇順に interleave して描画する。
  // 各要素は { sortKey=開始分, seq=安定タイブレーク, node } を持ち、最後にまとめて並べ替える。
  // 色解決用の course_code は BoardCourse から明示的に prop で渡す
  // (react-query キャッシュ物体への書込はしない)。
  const items: Array<{ sortKey: number; seq: number; node: React.ReactNode }> = [];
  let seq = 0;
  // 連結済みの (group_id, start_time) ペアキー。同住所でも別時刻なら別グループ。
  const renderedKeys = new Set<string>();
  for (const v of co.visits) {
    const startKey = parseHM(v.start_time) ?? Number.MAX_SAFE_INTEGER;
    const gid = v.same_address_group_id;
    if (gid && sameAddressGroups.byGroup.has(gid)) {
      // 同住所連続(ペア)扱いの条件: same_address_group_id が同じ + 同コース + start_time が同一。
      // 同住所でも開始時刻が違えば連結しない (各々通常カードで表示)。
      const members = sameAddressGroups.byGroup
        .get(gid)!
        .filter(
          (m) =>
            co.visits.some((cv) => cv.visit_id === m.visit_id) && m.start_time === v.start_time,
        );
      if (members.length >= 2) {
        const pairKey = `${gid}@${v.start_time}`;
        if (renderedKeys.has(pairKey)) continue;
        renderedKeys.add(pairKey);
        items.push({
          sortKey: startKey,
          seq: seq++,
          node: (
            <PairWrap
              key={'pair' + pairKey}
              visits={members}
              courseCode={co.course_code}
              onKarte={onKarte}
            />
          ),
        });
        continue;
      }
    }
    items.push({
      sortKey: startKey,
      seq: seq++,
      node: (
        <PatientCard key={v.visit_id} visit={v} courseCode={co.course_code} onKarte={onKarte} />
      ),
    });
  }

  // 空き帯: ≥60分 の各 gap を、その開始時刻位置に「この時間空いてますよ：HH:MM〜HH:MM」で挿入。
  // ただし頭数(capacity)でゲートする: remaining<=0 (filled>=6 満員) なら、時間的に空き帯が
  // あっても「この時間空いてますよ」カードを一切出さない (空きなし)。remaining>0 のときのみ表示。
  if (co.capacity.remaining > 0) {
    for (const gap of computeFreeGaps(co.visits)) {
      items.push({
        sortKey: gap.startMin,
        seq: seq++,
        node: <EmptySlot key={`gap@${gap.startMin}`} gap={gap} onEmpty={onEmpty} />,
      });
    }
  }

  // start_time 昇順。同一開始時刻は元の出現順 (seq) で安定ソート。
  items.sort((a, b) => a.sortKey - b.sortKey || a.seq - b.seq);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 7, padding: 8 }}>
      {items.map((it) => it.node)}
    </div>
  );
}

// ============================ Layout: AGENDA ============================

function AgendaBoard({
  courses,
  sameAddressGroups,
  onKarte,
  onEmpty,
}: {
  courses: BoardCourse[];
  sameAddressGroups: SameAddressGroups;
  onKarte: (v: BoardVisit) => void;
  onEmpty: () => void;
}) {
  const [closedCourses, setClosedCourses] = useState<Set<string>>(() => new Set());
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '4px 14px 0' }}>
      {courses.map((co) => {
        const k = cc(co.course_code);
        const filled = co.capacity.filled;
        const room = co.capacity.remaining;
        const cid = co.course_id ?? co.course_label;
        const isOpen = !closedCourses.has(cid);
        return (
          <section
            key={cid}
            style={{
              background: PANEL,
              borderRadius: 16,
              boxShadow: '0 2px 8px rgba(28,25,23,0.06)',
              overflow: 'hidden',
              borderLeft: `5px solid ${k.c}`,
            }}
          >
            <button
              onClick={() =>
                setClosedCourses((s) => {
                  const n = new Set(s);
                  if (n.has(cid)) n.delete(cid);
                  else n.add(cid);
                  return n;
                })
              }
              style={{
                width: '100%',
                padding: '11px 14px',
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                textAlign: 'left',
              }}
              aria-expanded={isOpen}
            >
              <span
                style={{
                  fontFamily: 'var(--font-serif)',
                  fontSize: 15,
                  fontWeight: 700,
                  color: k.c,
                }}
              >
                {co.course_label}
              </span>
              {co.staff_name && <span style={{ fontSize: 11, color: INK2 }}>{co.staff_name}</span>}
              <span style={{ flex: 1 }} />
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: room > 0 ? TEAL_DEEP : INK3,
                  background: room > 0 ? '#D7F2EE' : '#F0ECE5',
                  padding: '2px 8px',
                  borderRadius: 999,
                }}
              >
                {filled}/{co.capacity.max} {room > 0 ? `空き${room}つ` : '空きなし'}
              </span>
              <span
                style={{
                  color: INK3,
                  transform: isOpen ? 'rotate(180deg)' : 'none',
                  transition: 'transform .2s',
                  display: 'inline-flex',
                }}
              >
                <ChevronDown size={16} />
              </span>
            </button>
            {isOpen && (
              <CourseSlots
                co={co}
                sameAddressGroups={sameAddressGroups}
                onKarte={onKarte}
                onEmpty={onEmpty}
              />
            )}
          </section>
        );
      })}
    </div>
  );
}

// ============================ 状態表示 (loading / error / empty / closed) ============================

function LoadingState() {
  return (
    <div
      style={{
        textAlign: 'center',
        color: INK3,
        padding: '60px 20px',
        fontFamily: 'var(--font-serif)',
      }}
    >
      <div
        style={{
          width: 34,
          height: 34,
          borderRadius: '50%',
          border: `3px solid ${LINE}`,
          borderTopColor: TEAL,
          margin: '0 auto',
          animation: 'cfSpin 0.8s linear infinite',
        }}
      />
      <div style={{ marginTop: 14, fontSize: 14, fontWeight: 700 }}>ボードを読み込んでいます…</div>
    </div>
  );
}

function ErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div
      style={{
        textAlign: 'center',
        color: INK2,
        padding: '54px 20px',
        fontFamily: 'var(--font-serif)',
      }}
    >
      <div style={{ fontSize: 40 }}>⚠️</div>
      <div style={{ marginTop: 10, fontSize: 16, fontWeight: 700, color: '#C75C77' }}>
        読み込みに失敗しました
      </div>
      <div style={{ fontSize: 12.5, marginTop: 4, fontFamily: 'var(--font-sans)' }}>
        通信状況をご確認ください
      </div>
      <button
        onClick={onRetry}
        style={{
          marginTop: 16,
          padding: '10px 22px',
          borderRadius: 12,
          background: TEAL,
          color: '#fff',
          fontFamily: 'var(--font-serif)',
          fontWeight: 700,
          fontSize: 13,
        }}
      >
        再読み込み
      </button>
    </div>
  );
}

function ClosedState({ dayIdx }: { dayIdx: number }) {
  const day = CF_DOWS[dayIdx] ?? '月';
  return (
    <div
      style={{
        textAlign: 'center',
        color: INK3,
        padding: '60px 20px',
        fontFamily: 'var(--font-serif)',
      }}
    >
      <div style={{ fontSize: 44 }}>🌙</div>
      <div style={{ marginTop: 10, fontSize: 16, fontWeight: 700 }}>{day}曜は休講日です</div>
      <div style={{ fontSize: 12.5, marginTop: 4, fontFamily: 'var(--font-sans)' }}>
        出勤スタッフがいません
      </div>
    </div>
  );
}

function EmptyState({ dayIdx }: { dayIdx: number }) {
  const day = CF_DOWS[dayIdx] ?? '月';
  return (
    <div
      style={{
        textAlign: 'center',
        color: INK3,
        padding: '60px 20px',
        fontFamily: 'var(--font-serif)',
      }}
    >
      <div style={{ fontSize: 44 }}>🗓️</div>
      <div style={{ marginTop: 10, fontSize: 16, fontWeight: 700 }}>
        {day}曜の訪問はまだありません
      </div>
      <div style={{ fontSize: 12.5, marginTop: 4, fontFamily: 'var(--font-sans)' }}>
        「提案」から新しい訪問枠を探せます
      </div>
    </div>
  );
}
