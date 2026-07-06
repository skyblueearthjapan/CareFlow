'use client';

/**
 * CareFlow Mobile — 現場ボード (Warm & Human aligned, Phase2-3c 実データ接続)
 *
 * 電話/タブレット向けのフィールドボード本体。週切替 + 曜日ステッパーで
 * `GET /api/v1/schedule/v2/board` を再取得し、コース毎に実訪問を実時刻
 * (start_time〜end_time) で表示する。容量 filled/6・空き枠 (remaining)・同住所
 * (same_address_group_id) 連結を実データで描画する。
 *
 * 拠点 (稲毛/都賀) は分けず、親機 (デスクトップ週ビュー CourseWeekOverview) と同様に
 * **全拠点を 1 ボードに結合表示**する。選択日の全拠点コースを
 * 拠点順 (稲毛→都賀) → course_code (A,B,C,D,E,M) で並べる。
 *
 * 旧 Phase 1 のモック (CF_WEEK / CF_PATIENTS / CF_PENDING / RANK_*) は撤去済み。
 * 意匠 (Warm パレット・一覧レイアウト・実時刻表示・同住所/空き枠の見た目) は維持。
 */

import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import {
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Plus,
  ClipboardCheck,
  MapPin,
  ArrowLeft,
  UserPlus,
  Grid3x3,
} from 'lucide-react';

import { useFieldBoard, toWeekStart, toIsoYearWeek } from '@/lib/queries/fieldBoard';
import { usePendingRequests } from '@/lib/queries/pending_requests';
import type { BoardCell, BoardCourse, BoardOffice, BoardVisit } from '@/lib/schemas/v2/board';
import {
  computeFreeGaps,
  businessBlocksFromHours,
  buildSameAddressKey,
  BUSINESS_BLOCKS,
  parseHM,
  type FreeGap,
} from '@/lib/scheduling/freeGaps';
// Phase G-88: 営業時間設定を空き枠表示に反映 (取得前/失敗時は既定枠にフォールバック).
import { useSchedulingSettings } from '@/lib/queries/schedulingSettings';
import type { SlotPlacementContext } from '@/lib/field/patientCreate';

import { CF_THEME, CF_DOWS, cc } from './theme';
import { KarteSheet, SuggestSheet, PlacementSheet, PatientManageSheet, Toast } from './FieldSheets';
import { ApprovePanel } from './ApprovePanel';

const { TEAL, TEAL_DEEP, TERRA, TERRA_DEEP, INK, INK2, INK3, CREAM, LINE, PANEL } = CF_THEME;

const WEEKLEN = 7;

// ============================ 時間ユーティリティ ============================
//
// parseHM / fmtHM / computeFreeGaps / FreeGap / 営業枠定数 (BUSINESS_BLOCKS) /
// 閾値 (MIN_FREE_GAP_MIN) は親機 (CourseDayTable 等) と共有するため
// `@/lib/scheduling/freeGaps` に昇格済み (Phase G-55)。ここでは import して使う。
// 挙動は従来と不変 (同じ営業枠 / 60 分閾値 / start 昇順)。

/** 'HH:MM〜HH:MM' の実時刻ラベル (start/end をそのまま使う)。 */
function visitTimeLabel(v: BoardVisit): string {
  return `${v.start_time}〜${v.end_time}`;
}

/**
 * gap 直前 visit: end_time <= gap 開始 のうち最も遅く終わる visit (= 移動の起点)。
 * 空き枠への直接配置 (Phase G-84) で「誰の隣か」「移動時間の起点」を解決する。
 */
function lastVisitEndingAtOrBefore(
  visits: ReadonlyArray<BoardVisit>,
  gapStartMin: number,
): BoardVisit | null {
  let best: BoardVisit | null = null;
  let bestEnd = -1;
  for (const v of visits) {
    const e = parseHM(v.end_time);
    if (e === null || e > gapStartMin) continue;
    if (e > bestEnd) {
      bestEnd = e;
      best = v;
    }
  }
  return best;
}

/** gap 直後 visit: start_time >= gap 終了 のうち最も早く始まる visit。 */
function firstVisitStartingAtOrAfter(
  visits: ReadonlyArray<BoardVisit>,
  gapEndMin: number,
): BoardVisit | null {
  let best: BoardVisit | null = null;
  let bestStart = Number.MAX_SAFE_INTEGER;
  for (const v of visits) {
    const s = parseHM(v.start_time);
    if (s === null || s < gapEndMin) continue;
    if (s < bestStart) {
      bestStart = s;
      best = v;
    }
  }
  return best;
}

// ============================ 日付フォーマット ============================

const MD_FORMAT = (iso: string): string => {
  // 'YYYY-MM-DD' → 'M/D'
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  return `${Number(m[2])}/${Number(m[3])}`;
};

// ============================ 拠点 / コース並び順 ============================
//
// 親機 (CourseWeekOverview) と同様に全拠点を 1 ボードに結合表示する。
// board API は cell に office_code を持たない (offices[].office_name のみ) ため、
// 拠点順は office_name から導く: 稲毛 → 都賀 → その他 (名前順)。
// その中で course_code を A,B,C,D,E,M の明示順で並べる (未知コードは末尾, code 昇順)。

/** office_name → 拠点並び順 (小さいほど先頭)。マップ外は名前順で末尾に回す。 */
const OFFICE_ORDER: Record<string, number> = { 稲毛: 0, 都賀: 1 };

// 拠点付きコーストークン (applier の parse_course_token 規約) 用の短縮名。
// backend `patient_excel/schema.py` OFFICE_SHORT_TO_CODE と対応: 稲毛→稲(INAGE)・都賀→津(TSUGA)。
// board API は office_code を持たず office_name のみ返すため、ここで名前→短縮名へ写す。
const OFFICE_NAME_TO_SHORT: Record<string, string> = { 稲毛: '稲', 都賀: '津' };

/**
 * 拠点付きコーストークン (例 "稲A") を組み立てる。applier はこのトークンを
 * `parse_course_token` で拠点 + コードに分解してコースを解決する (= 唯一の解決経路。
 * board は週次 Course.id しか返さず course_template_id は無いためトークンが頼り)。
 *
 * - board の course_code が既に短縮名始まり (稲/津) ならそのまま採用。
 * - 裸コード ("A") の場合は office_name から短縮名を前置する。
 * - 短縮名が解決できない拠点では拠点付きにできず、裸コードのままになる
 *   (この場合 applier の `parse_course_token` が拠点を特定できずコース解決に失敗する。
 *   現状の対象拠点は稲毛/都賀のみで必ず短縮名を引けるためトリガーしない)。
 *   呼び出し側 (CourseSlots) で拠点付きにできたかを判定し、できない枠は空き枠カードを
 *   出さない (= 配置不可) ことで、解決不能トークンの送出を防ぐ。
 */
function buildCourseToken(officeName: string, courseCode: string): string | null {
  const code = (courseCode || '').trim();
  const short = OFFICE_NAME_TO_SHORT[officeName];
  // 短縮名を引けない拠点は拠点付きトークン化できない → null (配置不可)。
  if (!short) return null;
  if (code.startsWith('稲') || code.startsWith('津')) return code;
  return `${short}${code}`;
}

function officeRank(officeName: string): number {
  const r = OFFICE_ORDER[officeName];
  return r === undefined ? 100 : r;
}

/** course_code → 並び順 (A,B,C,D,E,M)。マップ外は末尾 (code 昇順タイブレーク)。 */
const COURSE_ORDER: Record<string, number> = { A: 0, B: 1, C: 2, D: 3, E: 4, M: 5 };

function courseRank(courseCode: string): number {
  const head = (courseCode || '').trim().charAt(0).toUpperCase();
  const r = COURSE_ORDER[head];
  return r === undefined ? 100 : r;
}

/** 選択日の全拠点コースに office 情報を添えたフラットな表示単位。 */
export interface BoardCourseWithOffice {
  course: BoardCourse;
  officeId: string;
  officeName: string;
}

/**
 * 選択日 (weekday) の全拠点 cell を集約し、コースを
 * 拠点順 (稲毛→都賀) → course_code (A,B,C,D,E,M) で並べた配列を返す。
 */
function collectDayCourses(
  cells: BoardCell[],
  weekday: number,
  officeNameById: Map<string, string>,
): BoardCourseWithOffice[] {
  const out: BoardCourseWithOffice[] = [];
  for (const cell of cells) {
    if (cell.weekday !== weekday) continue;
    const officeName = officeNameById.get(cell.office_id) ?? '';
    for (const course of cell.courses) {
      out.push({ course, officeId: cell.office_id, officeName });
    }
  }
  out.sort((a, b) => {
    const or = officeRank(a.officeName) - officeRank(b.officeName);
    if (or !== 0) return or;
    const on = a.officeName.localeCompare(b.officeName, 'ja');
    if (on !== 0) return on;
    const cr = courseRank(a.course.course_code) - courseRank(b.course.course_code);
    if (cr !== 0) return cr;
    return (a.course.course_code || '').localeCompare(b.course.course_code || '', 'ja');
  });
  return out;
}

// ============================ App ============================

export function FieldBoard() {
  // 週 state: 月曜起点の Date を持ち、ISO 年/週へ変換して API に渡す。
  const [weekStart, setWeekStart] = useState<Date>(() => toWeekStart(new Date()));
  const { isoYear, isoWeek } = useMemo(() => toIsoYearWeek(weekStart), [weekStart]);

  // 編集系の認可: manager / admin のみ編集 UI (提案・承認・患者管理・直接配置・
  // カルテ編集) を出す。staff は閲覧専用でボード自体は見られる。
  const { data: session } = useSession();
  const canEditKarte = session?.user?.role === 'admin' || session?.user?.role === 'manager';

  const [dayIdx, setDayIdx] = useState(0); // 0=月..6=日
  const [approve, setApprove] = useState(false);
  const [karte, setKarte] = useState<BoardVisit | null>(null);
  const [sheet, setSheet] = useState(false);
  // 患者管理シート (Phase G-87): 提案を介さない新規登録 + 検索編集ハブ。
  // manager/admin (canEditKarte) のみ開ける。
  const [manage, setManage] = useState(false);
  // 空き枠タップ → 直接配置シート (Phase G-84)。枠コンテキストを保持して開く。
  const [placement, setPlacement] = useState<SlotPlacementContext | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // 全拠点ぶんを 1 度に取得 (officeId 指定なし)。拠点は分けず 1 ボードに結合表示する。
  const boardQuery = useFieldBoard({ isoYear, isoWeek, officeId: null });
  const board = boardQuery.data;

  const offices: BoardOffice[] = useMemo(() => board?.offices ?? [], [board]);

  // office_id → office_name の索引 (拠点並び順の解決に使う)。
  const officeNameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const o of offices) m.set(o.office_id, o.office_name);
    return m;
  }, [offices]);

  // 選択日の全拠点コースを 拠点順 (稲毛→都賀) → course_code (A..M) で並べた配列。
  const dayCourses = useMemo<BoardCourseWithOffice[]>(() => {
    if (!board) return [];
    return collectDayCourses(board.board, dayIdx, officeNameById);
  }, [board, dayIdx, officeNameById]);

  // 表示用のコース配列 (参照安定化のため dayCourses から導出)。
  const courses = useMemo(() => dayCourses.map((d) => d.course), [dayCourses]);

  // 休講判定: 選択日に全拠点いずれの cell も closed で、かつコースが 1 件も無ければ休講。
  const closed = useMemo(() => {
    if (!board) return false;
    const dayCells = board.board.filter((c) => c.weekday === dayIdx);
    if (dayCells.length === 0) return false;
    if (dayCourses.length > 0) return false;
    return dayCells.every((c) => c.closed);
  }, [board, dayIdx, dayCourses]);

  // 当日に全拠点コースが無いなら、最初にコースのある曜日へジャンプ。
  useEffect(() => {
    if (!board) return;
    if (dayCourses.length > 0 || closed) return;
    for (let d = 0; d < WEEKLEN; d++) {
      const has = board.board.some((c) => c.weekday === d && c.courses.length > 0);
      if (has) {
        setDayIdx(d);
        return;
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [board]);

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

  // 同住所相手解決用: 当日 (全拠点) の全 visit を group_id でまとめる。
  const sameAddressGroups = useMemo(() => buildSameAddressGroups(courses), [courses]);

  // カルテの「担当拠点」表示用: visit_id → office_name (全拠点結合のため)。
  const officeNameByVisitId = useMemo(() => {
    const m = new Map<string, string>();
    for (const dc of dayCourses) {
      for (const v of dc.course.visits) m.set(v.visit_id, dc.officeName);
    }
    return m;
  }, [dayCourses]);

  // 承認待ち件数バッジ (実 pending-requests, pending 状態のみ)。
  const pendingQuery = usePendingRequests({ status: 'pending', limit: 100 });
  const pendingCount = pendingQuery.data?.items.length ?? 0;

  // Phase G-88: 営業時間設定 → 空き枠 (取得前/失敗時は既定枠にフォールバック)。
  const schedulingSettingsQuery = useSchedulingSettings();
  const businessBlocks = useMemo(() => {
    const v = schedulingSettingsQuery.data?.values;
    return businessBlocksFromHours(v?.business_start, v?.business_end) ?? BUSINESS_BLOCKS;
  }, [schedulingSettingsQuery.data]);

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
      {/* 旧ブランドヘッダー (CareFlow 現場ボード カード) は撤去し、操作ボタンは
          最上部バーへ集約 — スケジュール表示の縦スペースを優先する (2026-07-05 PO要望)。 */}
      <BackToAppBar
        canEdit={canEditKarte}
        approve={approve}
        pendingCount={pendingCount}
        onToggleApprove={() => setApprove((a) => !a)}
        onNew={() => setSheet(true)}
        onManage={() => setManage(true)}
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
        ) : dayCourses.length === 0 ? (
          <EmptyState dayIdx={dayIdx} />
        ) : (
          <AgendaBoard
            dayCourses={dayCourses}
            weekday={dayIdx}
            sameAddressGroups={sameAddressGroups}
            businessBlocks={businessBlocks}
            onKarte={setKarte}
            // 直接配置 (Phase G-84) は manager/admin のみ。staff には閲覧専用の旨を伝える。
            onEmpty={
              canEditKarte ? setPlacement : () => showToast('閲覧専用です (配置は管理者のみ)')
            }
          />
        )}
        <div style={{ height: 28 }} />
      </div>

      {/* scrim */}
      {(karte || sheet || placement || manage) && (
        <div
          onClick={() => {
            setKarte(null);
            setSheet(false);
            setPlacement(null);
            setManage(false);
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
          officeName={officeNameByVisitId.get(karte.visit_id) ?? ''}
          offices={offices}
          canEdit={canEditKarte}
          sameAddressGroups={sameAddressGroups}
          onClose={() => setKarte(null)}
          onOpenVisit={setKarte}
          onToast={showToast}
        />
      )}
      {sheet && (
        <SuggestSheet
          isoYear={isoYear}
          isoWeek={isoWeek}
          onClose={() => setSheet(false)}
          onToast={showToast}
        />
      )}
      {placement && (
        <PlacementSheet
          ctx={placement}
          isoYear={isoYear}
          isoWeek={isoWeek}
          onClose={() => setPlacement(null)}
          onToast={showToast}
        />
      )}
      {manage && canEditKarte && (
        <PatientManageSheet onClose={() => setManage(false)} onToast={showToast} />
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

// ============================ 最上部: モバイルアプリへ戻るバー ============================
//
// モバイルアプリ (MobileShell) 最上部の「現場ボードを開く →」全幅バーと対になる導線。
// 現場ボードでは最上部に「← モバイルアプリへ」の細い全幅バーを置く。
//
// - 遷移先は "/" (同タブ)。UA で振り分け (モバイル→/m/home・PC→/dashboard) されるため、
//   モバイルから戻ると正しくモバイル版へ戻る。
// - 様式: 現場ボードの Warm 意匠に合わせ、白/クリーム系の細い帯 + 下境界線 +
//   TEAL_DEEP 系テキスト。控えめ・薄め (縦を圧迫しない)。
// - safe-area: 最上部がこのバーになるため、ノッチぶんの余白 (env(safe-area-inset-top))
//   はこのバーの上 padding で吸収する (Teal ヘッダ側では二重に空けない)。

interface BackToAppBarProps {
  /** 編集系 (患者管理/提案/承認) を出すか。manager/admin のみ true (staff は閲覧専用)。 */
  canEdit: boolean;
  approve: boolean;
  pendingCount: number;
  onToggleApprove: () => void;
  onNew: () => void;
  onManage: () => void;
}

/** トップバー右側の丸ピル (受け入れ枠と同じ意匠のボタン版)。 */
const barPill: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 4,
  padding: '5px 10px',
  borderRadius: 999,
  border: `1px solid ${LINE}`,
  background: '#fff',
  color: TEAL_DEEP,
  fontFamily: 'var(--font-serif)',
  fontSize: 12,
  fontWeight: 700,
  lineHeight: 1,
  whiteSpace: 'nowrap',
};

function BackToAppBar({
  canEdit,
  approve,
  pendingCount,
  onToggleApprove,
  onNew,
  onManage,
}: BackToAppBarProps) {
  return (
    <div
      style={{
        flex: '0 0 auto',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 8,
        // ノッチぶんの安全余白はこのバーの上 padding で吸収する。
        paddingTop: 'calc(env(safe-area-inset-top) + 6px)',
        paddingBottom: 6,
        paddingLeft: 14,
        paddingRight: 10,
        background: CREAM,
        borderBottom: `1px solid ${LINE}`,
        position: 'relative',
        zIndex: 21,
      }}
    >
      <Link
        href="/"
        aria-label="モバイルアプリに戻る"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 5,
          color: TEAL_DEEP,
          fontFamily: 'var(--font-serif)',
          fontSize: 12,
          fontWeight: 600,
          lineHeight: 1,
          whiteSpace: 'nowrap',
        }}
      >
        <ArrowLeft size={13} strokeWidth={2.4} />
        戻る
      </Link>

      {/* 右クラスタ: 受け入れ枠 + (manager/admin) 患者管理/提案/承認。
          旧ブランドヘッダーの操作をここへ集約し縦スペースを節約。 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <Link href="/m/acceptance" aria-label="受け入れ枠マトリックスを開く" style={barPill}>
          <Grid3x3 size={12} strokeWidth={2.2} />
          受け入れ枠
        </Link>
        {canEdit && (
          <>
            <button
              onClick={onManage}
              aria-label="患者の登録・編集"
              title="患者の登録・編集"
              style={barPill}
            >
              <UserPlus size={13} strokeWidth={2.2} />
            </button>
            <button onClick={onNew} aria-label="配置の提案" title="配置の提案" style={barPill}>
              <Plus size={13} strokeWidth={2.4} />
              提案
            </button>
            <button
              onClick={onToggleApprove}
              aria-label="申請の承認"
              title="申請の承認"
              style={{
                ...barPill,
                position: 'relative',
                ...(approve
                  ? { background: '#B5790A', borderColor: '#B5790A', color: '#fff' }
                  : {}),
              }}
            >
              <ClipboardCheck size={13} strokeWidth={2.2} />
              承認
              {pendingCount > 0 && (
                <span
                  style={{
                    position: 'absolute',
                    top: -6,
                    right: -6,
                    minWidth: 16,
                    height: 16,
                    padding: '0 4px',
                    background: '#E1657F',
                    color: '#fff',
                    fontSize: 10,
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
          </>
        )}
      </div>
    </div>
  );
}

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
  officeId,
  officeName,
  weekday,
  sameAddressGroups,
  businessBlocks,
  onKarte,
  onEmpty,
}: {
  co: BoardCourse;
  /** 直接配置の枠コンテキスト構築用 (拠点情報 + 曜日)。 */
  officeId: string;
  officeName: string;
  weekday: number;
  sameAddressGroups: SameAddressGroups;
  /** Phase G-88: 営業設定から算出した営業枠 (空き帯算出用)。 */
  businessBlocks: ReadonlyArray<readonly [number, number]>;
  onKarte: (v: BoardVisit) => void;
  onEmpty: (ctx: SlotPlacementContext) => void;
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
    // そのコースの既存 visit 占有 (時刻重複の赤判定用)。start 昇順。
    const occupied = co.visits
      .map((v) => ({ startTime: v.start_time, endTime: v.end_time }))
      .filter((v) => parseHM(v.startTime) !== null && parseHM(v.endTime) !== null)
      .sort((a, b) => (parseHM(a.startTime) ?? 0) - (parseHM(b.startTime) ?? 0));
    const courseToken = buildCourseToken(officeName, co.course_code);
    // 拠点付きトークンにできない (= applier がコース解決できない) 場合は空き枠カードを
    // 出さない。解決不能トークンを承認に送ると PFV の course が NULL になるため配置不可とする。
    // 現状の対象拠点 (稲毛/都賀) では必ず解決できるため通常はトリガーしない。
    // 同住所 2 名 (同 lat/lng・同 start) の 90 分占有を空き時間に反映するため、各 visit に
    // same_address_key (lat/lng 由来) を付与して computeFreeGaps へ渡す (PC 盤と同規約)。
    // BoardVisit は lat/lng を持つので buildSameAddressKey でキー化できる。
    const visitsForGaps = co.visits.map((v) => ({
      ...v,
      same_address_key: buildSameAddressKey(v.lat, v.lng),
    }));
    const gaps = courseToken === null ? [] : computeFreeGaps(visitsForGaps, businessBlocks);
    for (const gap of gaps) {
      // gap 直前 / 直後の visit を実時刻位置から解決する (移動時間 / 表示用)。
      const prev = lastVisitEndingAtOrBefore(co.visits, gap.startMin);
      const next = firstVisitStartingAtOrAfter(co.visits, gap.endMin);
      const ctx: SlotPlacementContext = {
        officeId,
        officeName,
        courseId: co.course_id ?? null,
        // gaps は courseToken!==null のときのみ非空なので、ここでは必ず文字列。
        courseCode: courseToken!,
        courseLabel: co.course_label,
        weekday,
        gapStartMin: gap.startMin,
        gapEndMin: gap.endMin,
        prevVisit: prev
          ? { patientId: prev.patient_id, patientName: prev.patient_name, endTime: prev.end_time }
          : null,
        nextVisit: next ? { patientName: next.patient_name, startTime: next.start_time } : null,
        capacityRemaining: co.capacity.remaining,
        existingVisits: occupied,
      };
      items.push({
        sortKey: gap.startMin,
        seq: seq++,
        node: <EmptySlot key={`gap@${gap.startMin}`} gap={gap} onEmpty={() => onEmpty(ctx)} />,
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
  dayCourses,
  weekday,
  sameAddressGroups,
  businessBlocks,
  onKarte,
  onEmpty,
}: {
  /** 拠点順 (稲毛→都賀) → course_code (A..M) で並んだ全拠点コース。 */
  dayCourses: BoardCourseWithOffice[];
  /** 選択日の曜日 (0=Mon..6=Sun)。直接配置の枠コンテキスト用。 */
  weekday: number;
  sameAddressGroups: SameAddressGroups;
  /** Phase G-88: 営業設定から算出した営業枠 (空き帯算出用)。 */
  businessBlocks: ReadonlyArray<readonly [number, number]>;
  onKarte: (v: BoardVisit) => void;
  onEmpty: (ctx: SlotPlacementContext) => void;
}) {
  const [closedCourses, setClosedCourses] = useState<Set<string>>(() => new Set());
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '4px 14px 0' }}>
      {dayCourses.map((dc, idx) => {
        const co = dc.course;
        const k = cc(co.course_code);
        const filled = co.capacity.filled;
        const room = co.capacity.remaining;
        // 拠点跨ぎでも安定する key: office_id + (course_id || code + label)。
        const cid = `${dc.officeId}:${co.course_id ?? `${co.course_code}:${co.course_label}`}`;
        const isOpen = !closedCourses.has(cid);
        // 拠点ブロックの先頭にだけ薄い拠点小見出しを挿入 (コンパクト優先・視認性のため)。
        const showOfficeHeading = idx === 0 || dayCourses[idx - 1]!.officeId !== dc.officeId;
        return (
          <div key={cid} style={{ display: 'contents' }}>
            {showOfficeHeading && dc.officeName && (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 7,
                  padding: idx === 0 ? '0 2px 0' : '4px 2px 0',
                  marginTop: idx === 0 ? 0 : 2,
                }}
              >
                <MapPin size={12} strokeWidth={2.4} color={INK3} style={{ flex: '0 0 auto' }} />
                <span
                  style={{
                    fontFamily: 'var(--font-serif)',
                    fontSize: 12,
                    fontWeight: 700,
                    color: INK2,
                    letterSpacing: '0.02em',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {dc.officeName}
                </span>
                <span style={{ flex: 1, height: 1, background: LINE, borderRadius: 1 }} />
              </div>
            )}
            <section
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
                {co.staff_name && (
                  <span style={{ fontSize: 11, color: INK2 }}>{co.staff_name}</span>
                )}
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
                  officeId={dc.officeId}
                  officeName={dc.officeName}
                  weekday={weekday}
                  sameAddressGroups={sameAddressGroups}
                  businessBlocks={businessBlocks}
                  onKarte={onKarte}
                  onEmpty={onEmpty}
                />
              )}
            </section>
          </div>
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
