'use client';

/**
 * WeekdayScheduleCard — 共通コンポーネント (2026-W20, 改修):
 * 曜日 × コース の visit 一覧を Before/After 方式で描画する共通コンポーネント。
 *
 * 用途:
 *   - FullOptimizeDialog の `CourseListColumn` (Before / After 2 ペイン)
 *   - /schedule の 月-土タブの「リスト表示モード」 (Before/After 形式統一)
 *
 * 統一する情報 (ユーザー要件 / 2026-W20 後期):
 *   (1) 患者名 (クリック可能)
 *   (2) 開始時刻 (HH:MM, 全モード共通)
 *   (3) 実動時間 (formatDuration. weekView=true のときは省略)
 *   (4) 住所詳細 (ペア囲み内でも各 visit に表示 — Phase E-1)
 *   (5) 個別条件: time_type + sex_restriction
 *   (6) 次の目的地までの距離 (VisitArrow)
 *
 * 2026-W20 後期改修ポイント:
 *   - 同 `same_address_group_id` の連続 visit を **1 つの大きな囲み** に統合する
 *     (旧: 1 人ずつ枠線で個別に囲まれ「2 ペア 4 人」が 4 個の枠に見えていた問題)
 *   - 3 名以上の同 group_id は最初の 2 名のみペア表示 + 残りは単独表示
 *     (BE H2 enforce 漏れケースの視覚化)
 *   - 週ビュー (weekView=true) は最小情報のみ (氏名 + 時刻 + ペアリング囲み)
 */
import * as React from 'react';

import { cn } from '@/lib/utils';
import { formatDuration } from '@/lib/format/duration';
import { buildSameAddressKey, parseHM, type FreeGap } from '@/lib/scheduling/freeGaps';
import { genderPalette } from '@/lib/scheduling/timeline';
import { CornerPushPin, PushPin, PushPinOff } from '@/components/ui/push-pin';
import { VisitArrow } from './v2/VisitArrow';
import { trimSeconds } from './v2/_autoScheduleUtils';
import type { PartnerLocation } from './v2/courseGrid';
import { PinScopeMenu, type PinScope } from './v2/PinScopeMenu';

// ─────────────────────────────────────────────────────────────────────────
// Types — 共通 visit / course データ型 (V2VisitForUI と互換)
// ─────────────────────────────────────────────────────────────────────────

/**
 * 1 visit の表示用データ. V2VisitForUI のサブセット + /schedule 側で
 * 構築する patient/visit ハイブリッド型と互換になるよう缓和.
 */
export interface VisitListItem {
  /** ユニーク key (visit_id or patient_id+slot 等). */
  key: string;
  /**
   * 患者 ID. 指定があり、かつカード側に `onPatientClick` が渡されている場合のみ
   * 患者名がクリック可能になり、患者詳細ダイアログを開ける。
   */
  patient_id?: string | null;
  /** "HH:MM" or "HH:MM:SS". 表示時は trimSeconds 適用. */
  start_time: string;
  patient_name: string;
  /** 例: "稲毛区小仲台 7-12-2-407" / null. */
  address?: string | null;
  /** 町レベルのエリアラベル. 任意. */
  area_label?: string | null;
  /** "固定" / "時間帯" / "午前" / "午後" / "終日". */
  time_type?: string | null;
  preferred_start?: string | null;
  preferred_end?: string | null;
  /** "female_only" / "male_only" / null. */
  sex_restriction?: string | null;
  /** 同住所グループの id (同 id を持つ visit を黄色枠でまとめる). */
  same_address_group_id?: string | null;
  /** 次の visit までの直線距離 (km). null = 最後尾. */
  distance_to_next_km?: number | null;
  /**
   * 実動時間 (分). end_time - start_time, または PFV.duration_min.
   * 指定があれば formatDuration で日本語化して表示する.
   */
  duration_min?: number | null;
  /**
   * CareFlow #101 FE: 当該 visit に紐づく警告 (主に travel_time_shortage).
   * 非空のとき visit セルを赤枠ハイライト + tooltip (message) を表示する.
   */
  warnings?: Array<{ type?: string | null; message?: string | null } | null> | null;
  /**
   * Phase G-21: 当該 visit のソース PFV id.
   * 指定があれば 🔒 toggle が enable になる. 未指定 (weekly_pattern 由来) は disabled.
   */
  fixed_visit_id?: string | null;
  /** Phase G-21: PFV.is_pinned のミラー値. true で 🔒 active 表示. */
  is_pinned?: boolean | null;
  /**
   * 2026-08-08: 型 (固定訪問スケジュール) とズレているときの型の開始時刻 'HH:MM'.
   * 一致 / 固定枠なし は null. ピン留めできない理由の説明に使う.
   */
  master_start_time?: string | null;
  /** 週のピン (青ピン / 2026-08-09)。source と独立のフラグ。 */
  week_pinned?: boolean | null;
  /**
   * T-1L: タイムライン兄弟リスト (TimelineDayList) 用の患者性別 (patient.sex:
   * male/female/unknown)。行頭の性別ドット・左色帯に使う。既存 WeekdayScheduleCard は
   * 未使用 (加算のみ・無影響)。
   */
  patient_sex?: string | null;
  /**
   * G2: 実 visit の id。TimelineDayList の × (訪問削除) が使う。`key` は提案系では
   * patient_id+slot 等になり得るため、削除対象は必ずこのフィールドで受け取る。
   * 未指定の行には × を出さない。既存 WeekdayScheduleCard は未使用 (加算のみ)。
   */
  visit_id?: string | null;
  /**
   * G3: visit のソース (Wave U-2)。'manual_week' = この週だけの配置 →
   * TimelineDayList に「今週のみ」チップを出す。既存 WeekdayScheduleCard は未使用。
   */
  source?: string | null;
  /**
   * T-6撤去: 2 名体制の slot (①/②) と相方の現在地。旧 CourseDayTable がセル下部に
   * 出していた運用情報で、テーブル撤去に伴い日リスト/日タイムラインへ移設した。
   * 提案系 (WeekdayScheduleCard) は未使用 (加算のみ)。
   */
  group_slot_label?: 1 | 2;
  partner_location?: PartnerLocation | null;
  partner_label?: string | null;
}

/** 1 コース分のサマリ. */
export interface CourseListItem {
  /** ユニーク key (office_id+code+staff_id 等). */
  key: string;
  /** "本店 A コース" のような表示ラベル. */
  title: string;
  /** "{visits_count}件 / {distance}km" の右端 summary. 任意. */
  summary?: string | null;
  /** 描画する visit 群. start_time 昇順で並べておくこと. */
  visits: VisitListItem[];
  /**
   * Phase G-55: 営業枠から既存 visit を除いた空き時間帯 (≥60分, start 昇順)。
   * 共有 util `@/lib/scheduling/freeGaps` の computeFreeGaps で親が算出して渡す。
   * 指定があり、かつ頭数の空き (capacity.remaining>0) のときのみ、visit 行に
   * 時刻順 interleave で「HH:MM〜HH:MM 空き時間」マーカーが挿入される。
   */
  freeGaps?: FreeGap[];
  /**
   * Phase G-55: 実効定員 / 配置済み。頭数ゲートに使う。
   * remaining = max - filled <= 0 (満員) のときは時間 gap があっても空き時間帯を
   * 表示しない (モバイル AgendaBoard と同 semantics)。未指定時は空き時間帯を出さない
   * (= freeGaps だけ渡しても capacity 無しでは非表示 = 後方互換)。
   */
  capacity?: { filled: number; max: number };
  /**
   * T-1L: TimelineDayList のグループ見出し用 (拠点名+コード のチップ / 担当スタッフ名)。
   * 既存 WeekdayScheduleCard は title/summary を使うため未使用 (加算のみ・無影響)。
   */
  office_name?: string | null;
  course_code?: string | null;
  staff_name?: string | null;
  /**
   * PO 2026-07-09: 担当 (course.assigned_staff_id) が削除済み staff を指す (stale) 場合 true。
   * コース見出しで staff_name の代わりに（担当不在）を琥珀色で出す（担当 null の
   * 「（未割当）」とは区別）。
   */
  staff_missing?: boolean | null;
  /**
   * 新人同行 (§7.2): TimelineDayList のコース見出し 👥 バッジ解決用
   * (course_template_id, weekday) → resolveCourseId → courseBadgeName。
   * 提案系 (WeekdayScheduleCard) は未使用 (加算のみ・無影響)。省略時は出さない。
   */
  course_template_id?: string | null;
  weekday?: number | null;
}

export interface WeekdayScheduleCardProps {
  /** ヘッダーラベル (例: "Before" / "After" / "曜日コース一覧"). */
  title: string;
  /** ヘッダー右端の合計サマリ (任意). 例: "23件 / 145.2km". */
  totalSummary?: string | null;
  /** ヘッダーの強調色 (primary=After 用 / muted=Before 用). */
  tone?: 'primary' | 'muted';
  /** 各コース行. 並び順を制御したい場合は呼び出し側で sort 済みで渡す. */
  courses: CourseListItem[];
  /** 表示する最大 visit 数 (overflow は "…他 N 件"). undefined / 0 = 全件表示. */
  maxVisitsPerCourse?: number;
  /** カードに付与する testid prefix. */
  testIdPrefix?: string;
  /**
   * 患者名クリック時のハンドラ. 指定された行のみ患者名が button になり、
   * クリックで親側 (例: /schedule の PatientScheduleDetailDialog) を開く。
   * 指定なし or visit に `patient_id` が無い場合は通常の text 表示.
   */
  onPatientClick?: (patientId: string) => void;
  /**
   * 2026-W20 後期: 週ビュー用 (= 情報を最小限に絞る) フラグ.
   * true:
   *   - 実動時間 / 個別条件 / 距離 / 住所詳細 を非表示
   *   - 氏名 + 開始時刻 + ペアリング囲みのみ
   * false (default): フル表示 (Before/After と同等).
   */
  weekView?: boolean;
  /**
   * Phase G-21: 🔒 完全固定 toggle ハンドラ.
   * 指定時のみ各 visit 行に 🔒 / 🔓 ボタンが描画される.
   * - visit.fixed_visit_id が無い場合は disabled (tooltip で説明).
   * - canEdit による表示制御は呼び出し側 (ハンドラ未指定で完全に非表示) で行う.
   */
  onTogglePin?: (pfvId: string, nextPinned: boolean, scope: PinScope, patientId: string) => void;
  /**
   * 距離 (distance_to_next_km) の意味.
   * - 'to_next' (default): その値は「次の患者までの距離」。最後尾は空欄、同住所ペアは
   *   最後の 1 名のみ表示 (従来挙動 / 提案ダイアログ用)。
   * - 'to_reach': その値は「ここに来るまでの移動距離 (前の患者 or 拠点から)」。全員に
   *   表示し、同住所ペアも各メンバーの値をそのまま出す (スケジュール日リスト用)。
   */
  distanceMode?: 'to_next' | 'to_reach';
}

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

/**
 * time_type バッジを preferred_start/end と合わせて整形.
 * FullOptimizeDialog 内の同名関数と完全互換 (移植元).
 *
 * CareFlow #UX-2026W21: リストとテーブル双方で語彙統一するため、
 * primitive 引数 ({time_type, preferred_start, preferred_end}) を受け取る
 * 汎用シグネチャに変更した. CourseDayTable.tsx もこの関数を import する.
 *
 * 戻り値: 🕐 prefix 付き文字列 (例: '🕐 時間帯 (09:30-10:00)') / null
 */
export function formatTimeCondition(args: {
  time_type?: string | null;
  preferred_start?: string | null;
  preferred_end?: string | null;
}): string | null {
  const { time_type, preferred_start, preferred_end } = args;
  if (time_type === '時間帯' && preferred_start && preferred_end) {
    return `🕐 時間帯 (${trimSeconds(preferred_start)}-${trimSeconds(preferred_end)})`;
  }
  if (time_type === '固定' && preferred_start) {
    return `🕐 固定 (${trimSeconds(preferred_start)})`;
  }
  if (time_type === '午前') return '🕐 午前 (~12:00)';
  if (time_type === '午後') return '🕐 午後 (13:00~)';
  if (time_type === '終日') return '🕐 終日';
  return time_type ? `🕐 ${time_type}` : null;
}

// ─────────────────────────────────────────────────────────────────────────
// Cluster helper — 2026-W20 後期: ペア囲みを 1 つに統合
// ─────────────────────────────────────────────────────────────────────────

/**
 * `clusterVisits` 出力の各要素.
 *
 * - `pair`: 連続する同 `same_address_group_id` の 2 件 (= 同住所ペア).
 * - `single`: それ以外の単独 visit (group_id なし / 1 件しかない group_id /
 *   3 名以上のうち 3 番目以降).
 *
 * 並び順は入力 visits の順序を維持する (連続性が前提なので呼び出し側で
 * start_time 等にソート済み).
 */
export type ClusterItem =
  | { kind: 'pair'; visits: VisitListItem[]; groupId: string }
  | { kind: 'single'; visit: VisitListItem };

/**
 * 連続する同 `same_address_group_id` を「2 名ペア」として 1 つの cluster に
 * 集約する. 3 名以上の同 group_id は最初の 2 名のみペア化し、残りは single
 * として後続に並べる (BE H2 enforce 漏れケースの視覚化).
 *
 * 注意:
 *   - 「連続」のみペア化. 同 group_id でも他 visit を挟んで離れている場合は
 *     single 扱い (入力の sort が壊れていれば視覚的にも別物として描画される).
 *   - group_id=null/undefined / 空文字 は常に single.
 *
 * @example
 *   in: [A(g1), B(g1), C(g2), D(g2), E(null)]
 *   out: [pair(A,B,g1), pair(C,D,g2), single(E)]
 *
 * @example
 *   in: [A(g1), B(g1), C(g1)]  // 3 名同住所 = H2 enforce 漏れ
 *   out: [pair(A,B,g1), single(C)]
 */
export function clusterVisits(visits: VisitListItem[]): ClusterItem[] {
  const out: ClusterItem[] = [];
  let i = 0;
  while (i < visits.length) {
    const v = visits[i]!;
    const gid = v.same_address_group_id ?? null;
    if (gid && i + 1 < visits.length && visits[i + 1]!.same_address_group_id === gid) {
      // 同 group_id の連続 2 件 → pair として cluster 化.
      out.push({ kind: 'pair', visits: [v, visits[i + 1]!], groupId: gid });
      i += 2;
    } else {
      // 単独 visit (group_id なし or 連続しない or 3 名以上の残り).
      out.push({ kind: 'single', visit: v });
      i += 1;
    }
  }
  return out;
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export function WeekdayScheduleCard({
  title,
  totalSummary,
  tone = 'muted',
  courses,
  maxVisitsPerCourse,
  testIdPrefix,
  onPatientClick,
  weekView = false,
  onTogglePin,
  distanceMode = 'to_next',
}: WeekdayScheduleCardProps) {
  const headerCls =
    tone === 'primary'
      ? 'border-brand-primary/40 bg-brand-primary/5 text-brand-primary'
      : 'border-border-default bg-bg-muted text-text-muted';
  return (
    <div
      className="overflow-hidden rounded border border-border-default"
      data-testid={testIdPrefix ? `${testIdPrefix}-card` : undefined}
    >
      <div
        className={`flex items-center justify-between border-b px-2 py-1 text-[11px] font-semibold ${headerCls}`}
      >
        <span>{title}</span>
        {totalSummary ? <span className="tnum text-text-secondary">{totalSummary}</span> : null}
      </div>
      {courses.length === 0 ? (
        <div className="py-4 text-center text-[11px] text-text-muted">(コースなし)</div>
      ) : (
        <ul className="divide-y divide-border-default">
          {courses.map((c) => (
            <CourseRow
              key={c.key}
              course={c}
              maxVisits={maxVisitsPerCourse}
              testIdPrefix={testIdPrefix}
              onPatientClick={onPatientClick}
              weekView={weekView}
              onTogglePin={onTogglePin}
              distanceMode={distanceMode}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

interface CourseRowProps {
  course: CourseListItem;
  maxVisits?: number;
  testIdPrefix?: string;
  onPatientClick?: (patientId: string) => void;
  weekView: boolean;
  onTogglePin?: (pfvId: string, nextPinned: boolean, scope: PinScope, patientId: string) => void;
  distanceMode: 'to_next' | 'to_reach';
}

function CourseRow({
  course,
  maxVisits,
  testIdPrefix,
  onPatientClick,
  weekView,
  onTogglePin,
  distanceMode,
}: CourseRowProps) {
  // 「最大 N 件」制限は visit 単位で先に slice して、その後に cluster 化する.
  // (pair の片割れだけが切れる事態を避けるため、ペア跨ぎは clusterVisits 後に
  //  pair 数を維持したまま描画される — overflow 件数のみ patient 単位で計算).
  const sliced = maxVisits && maxVisits > 0 ? course.visits.slice(0, maxVisits) : course.visits;
  const overflowCount = course.visits.length - sliced.length;
  const clusters = React.useMemo(() => clusterVisits(sliced), [sliced]);

  // Phase G-55: 空き時間帯 (≥60分) を visit (cluster) と時刻順に interleave する。
  //   - 頭数ゲート: capacity.remaining<=0 (満員) のときは時間 gap があっても出さない
  //     (モバイル AgendaBoard と同 semantics)。capacity 未指定でも非表示。
  //   - 各 cluster の開始分は内部 visit の最小 start_time、gap は startMin。
  //   - 同一開始時刻は元の出現順 (seq) で安定ソート (gap は visit の後ろ)。
  const renderItems = React.useMemo(() => {
    type RenderItem =
      | { kind: 'cluster'; sortKey: number; seq: number; cluster: ClusterItem; ci: number }
      | { kind: 'gap'; sortKey: number; seq: number; gap: FreeGap };
    const items: RenderItem[] = [];
    let seq = 0;
    clusters.forEach((cluster, ci) => {
      const visits = cluster.kind === 'pair' ? cluster.visits : [cluster.visit];
      const startMin = visits.reduce<number>((min, v) => {
        const m = parseHM(v.start_time);
        return m != null && m < min ? m : min;
      }, Number.MAX_SAFE_INTEGER);
      items.push({ kind: 'cluster', sortKey: startMin, seq: seq++, cluster, ci });
    });
    const capacity = course.capacity;
    const remaining = capacity ? Math.max(0, capacity.max - capacity.filled) : 0;
    const showFreeSlots = capacity != null && remaining > 0;
    if (showFreeSlots) {
      for (const gap of course.freeGaps ?? []) {
        items.push({ kind: 'gap', sortKey: gap.startMin, seq: seq++, gap });
      }
    }
    items.sort((a, b) => a.sortKey - b.sortKey || a.seq - b.seq);
    return items;
  }, [clusters, course.freeGaps, course.capacity]);

  return (
    <li className="px-2 py-1.5">
      <div className="flex items-center justify-between text-[11px]">
        <span className="font-semibold text-text-primary">{course.title}</span>
        {course.summary ? <span className="tnum text-text-muted">{course.summary}</span> : null}
      </div>
      {renderItems.length > 0 ? (
        <ul className="mt-1 space-y-0.5">
          {renderItems.map((item) => {
            if (item.kind === 'gap') {
              return (
                <FreeGapRow
                  key={`gap-${item.gap.startMin}`}
                  gap={item.gap}
                  testIdPrefix={testIdPrefix}
                />
              );
            }
            const cluster = item.cluster;
            if (cluster.kind === 'single') {
              const v = cluster.visit;
              return (
                <VisitRow
                  key={v.key}
                  visit={v}
                  testIdPrefix={testIdPrefix}
                  onPatientClick={onPatientClick}
                  weekView={weekView}
                  onTogglePin={onTogglePin}
                />
              );
            }
            // pair cluster — 2 名を 1 つの囲みで wrap する.
            // pair の最後尾 visit から次のペア外 visit (= sliced 配列の次要素)
            // への distance を表示するため、sliced 中の位置を解決する.
            const lastVisitInPair = cluster.visits[cluster.visits.length - 1]!;
            const pairLastIdx = sliced.indexOf(lastVisitInPair);
            const nextAfterPair = pairLastIdx >= 0 ? (sliced[pairLastIdx + 1] ?? null) : null;
            return (
              <PairCluster
                key={`pair-${cluster.groupId}-${item.ci}`}
                cluster={cluster}
                nextAfterPair={nextAfterPair}
                testIdPrefix={testIdPrefix}
                onPatientClick={onPatientClick}
                weekView={weekView}
                onTogglePin={onTogglePin}
                distanceMode={distanceMode}
              />
            );
          })}
          {overflowCount > 0 ? (
            <li className="text-[10px] text-text-muted">…他 {overflowCount} 件</li>
          ) : null}
        </ul>
      ) : null}
    </li>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// FreeGapRow — Phase G-55: 空き時間帯マーカー (リスト内 時刻順 interleave)
// ─────────────────────────────────────────────────────────────────────────

/**
 * 1 つの空き時間帯 (≥60分) を表す行。visit 行と時刻順に並ぶよう CourseRow が
 * interleave して描画する。親機意匠: 薄い teal 背景 + amber 左ボーダー + 等幅時刻
 * (CourseDayTable のサマリ帯と同じ視覚言語。モバイルの warm 配色は持ち込まない)。
 */
function FreeGapRow({ gap, testIdPrefix }: { gap: FreeGap; testIdPrefix?: string }) {
  return (
    <li
      className="flex items-center gap-1 rounded border-l-2 border-amber-500 bg-brand-primary/5 px-1.5 py-0.5 text-[10px] font-medium text-brand-primary"
      data-testid={testIdPrefix ? `${testIdPrefix}-free-gap-${gap.startMin}` : undefined}
      data-free-gap-start={gap.startMin}
      title={`空き時間帯 ${gap.label}`}
    >
      <span className="tnum">{gap.label}</span>
      <span className="text-text-secondary">空き時間</span>
    </li>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// PairCluster — 同住所ペアを 1 つの囲みで描画
// ─────────────────────────────────────────────────────────────────────────

interface PairClusterProps {
  cluster: { kind: 'pair'; visits: VisitListItem[]; groupId: string };
  /** ペアの最後尾 visit の「次の patient」 (= 距離計算用. ペア外の visit). */
  nextAfterPair: VisitListItem | null;
  testIdPrefix?: string;
  onPatientClick?: (patientId: string) => void;
  weekView: boolean;
  onTogglePin?: (pfvId: string, nextPinned: boolean, scope: PinScope, patientId: string) => void;
  distanceMode: 'to_next' | 'to_reach';
}

function PairCluster({
  cluster,
  nextAfterPair,
  testIdPrefix,
  onPatientClick,
  weekView,
  onTogglePin,
  distanceMode,
}: PairClusterProps) {
  const { visits, groupId } = cluster;
  return (
    <li
      className={cn('rounded-md border-2 border-yellow-400 bg-yellow-50/60', 'my-1 px-2 py-2')}
      data-testid={testIdPrefix ? `${testIdPrefix}-pair-cluster-${groupId}` : undefined}
      data-same-address-group-id={groupId}
      data-pair-size={visits.length}
    >
      <div className="mb-1 text-[10px] font-semibold text-yellow-700">
        📍 同住所 ({visits.length} 名)
      </div>
      <ul className="divide-y divide-yellow-200/70">
        {visits.map((v, i) => {
          // 'to_reach' (= 日リスト): 各 visit の値は「ここに来るまでの距離」なので
          //   そのまま全メンバー表示する (同住所の 2 人目以降は ≒0km / null)。
          // 'to_next' (= 従来 / 提案ダイアログ): ペア内 i=0..N-2 は「次のペアメンバー」が
          //   同住所 (=0km) なので距離を出さず、最後尾だけ nextAfterPair への距離を出す。
          const isLastInPair = i === visits.length - 1;
          const suppressDistance =
            distanceMode === 'to_reach' ? false : !isLastInPair || nextAfterPair == null;
          return (
            <PairMemberRow
              key={v.key}
              visit={v}
              suppressDistance={suppressDistance}
              testIdPrefix={testIdPrefix}
              onPatientClick={onPatientClick}
              weekView={weekView}
              onTogglePin={onTogglePin}
            />
          );
        })}
      </ul>
    </li>
  );
}

interface PairMemberRowProps {
  visit: VisitListItem;
  /** ペア内距離 (= 同住所 0km) を省略する場合 true. */
  suppressDistance: boolean;
  testIdPrefix?: string;
  onPatientClick?: (patientId: string) => void;
  weekView: boolean;
  onTogglePin?: (pfvId: string, nextPinned: boolean, scope: PinScope, patientId: string) => void;
}

/**
 * ペア囲みの中身 (1 patient 1 行). 構造は VisitRow とほぼ同じだが、囲み枠を
 * 親 PairCluster に委譲しているので個別の border は付けない.
 */
function PairMemberRow({
  visit,
  suppressDistance,
  testIdPrefix,
  onPatientClick,
  weekView,
  onTogglePin,
}: PairMemberRowProps) {
  // CareFlow #101 FE: warning ありの visit は赤枠ハイライト + tooltip 表示.
  const warningInfo = collectVisitWarningInfo(visit);
  const isPinned = visit.is_pinned === true;
  // T-4: 日リストと同じカード視覚言語 (性別ウォッシュ地 + 性別左帯 + 角丸)。
  // 警告赤 / ピン琥珀の背景はウォッシュより優先 (inline bg は通常行のみ)。
  const pal = genderPalette(visit.patient_sex);
  return (
    <li
      className={cn(
        'relative flex flex-wrap items-center gap-1 rounded-md border border-l-[3px] px-1 py-0.5 text-[10px]',
        warningInfo.hasWarning ? 'bg-red-50/40' : '',
        // Phase G-21: 完全固定 visit は薄い黄色背景.
        isPinned && !warningInfo.hasWarning ? 'bg-yellow-50/60' : '',
      )}
      style={{
        borderColor: warningInfo.hasWarning ? '#ef4444' : pal.ln,
        borderLeftColor: pal.bar,
        ...(warningInfo.hasWarning || isPinned ? {} : { background: pal.bg }),
      }}
      data-testid={testIdPrefix ? `${testIdPrefix}-visit-${visit.key}` : undefined}
      data-same-address-group-id={visit.same_address_group_id ?? undefined}
      data-pair-member="true"
      data-visit-warning={warningInfo.hasWarning ? 'true' : undefined}
      data-pinned={isPinned ? 'true' : undefined}
      title={warningInfo.tooltip ?? undefined}
      aria-label={warningInfo.tooltip ?? undefined}
    >
      {/* ピン留め: 行右上に打ち込んだ画鋲 (📍住所と誤認しない・PO要望 2026-07-08)。 */}
      {isPinned ? <CornerPushPin className="h-3.5 w-3.5" /> : null}
      <VisitRowContent
        visit={visit}
        onPatientClick={onPatientClick}
        weekView={weekView}
        // Phase E-1: ペア内の各 visit にも住所を表示する (ユーザー要望: 同住所ペアで
        // 住所が見えない問題の修正). ヘッダーの「📍 同住所」ラベルはペアの可視化
        // 用として維持しつつ、住所詳細は各カードに復活させる.
        // ペア内距離 (= 同住所なので 0km) は親側 (PairCluster) が決定する.
        suppressDistance={suppressDistance}
        onTogglePin={onTogglePin}
      />
    </li>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// VisitRow — 単独 visit (= ペア外) の 1 行描画
// ─────────────────────────────────────────────────────────────────────────

interface VisitRowProps {
  visit: VisitListItem;
  testIdPrefix?: string;
  onPatientClick?: (patientId: string) => void;
  weekView: boolean;
  onTogglePin?: (pfvId: string, nextPinned: boolean, scope: PinScope, patientId: string) => void;
}

function VisitRow({ visit, testIdPrefix, onPatientClick, weekView, onTogglePin }: VisitRowProps) {
  // CareFlow #101 FE: warning ありの visit は赤枠ハイライト + tooltip 表示.
  const warningInfo = collectVisitWarningInfo(visit);
  const isPinned = visit.is_pinned === true;
  // T-4: 日リストと同じカード視覚言語 (性別ウォッシュ地 + 性別左帯 + 角丸)。
  // 警告赤 / ピン琥珀の背景はウォッシュより優先 (inline bg は通常行のみ)。
  const pal = genderPalette(visit.patient_sex);
  return (
    <li
      className={cn(
        'relative flex flex-wrap items-center gap-1 rounded-md border border-l-[3px] px-1 py-0.5 text-[10px]',
        warningInfo.hasWarning ? 'bg-red-50/40' : '',
        // Phase G-21: 完全固定 visit は薄い黄色背景.
        isPinned && !warningInfo.hasWarning ? 'bg-yellow-50/60' : '',
      )}
      style={{
        borderColor: warningInfo.hasWarning ? '#ef4444' : pal.ln,
        borderLeftColor: pal.bar,
        ...(warningInfo.hasWarning || isPinned ? {} : { background: pal.bg }),
      }}
      data-testid={testIdPrefix ? `${testIdPrefix}-visit-${visit.key}` : undefined}
      data-same-address-group-id={visit.same_address_group_id ?? undefined}
      data-visit-warning={warningInfo.hasWarning ? 'true' : undefined}
      data-pinned={isPinned ? 'true' : undefined}
      title={warningInfo.tooltip ?? undefined}
      aria-label={warningInfo.tooltip ?? undefined}
    >
      {/* ピン留め: 行右上に打ち込んだ画鋲 (📍住所と誤認しない・PO要望 2026-07-08)。 */}
      {isPinned ? <CornerPushPin className="h-3.5 w-3.5" /> : null}
      <VisitRowContent
        visit={visit}
        onPatientClick={onPatientClick}
        weekView={weekView}
        onTogglePin={onTogglePin}
      />
    </li>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// CareFlow #101 FE — warning helpers (赤枠 + tooltip 表示用)
// ─────────────────────────────────────────────────────────────────────────

/**
 * 1 visit に紐づく warning から、UI 表示用の `hasWarning` + `tooltip` を抽出する.
 *
 * - visit.warnings が空 / 未定義 → `{ hasWarning: false, tooltip: null }`
 * - 非空 → message を改行で連結したものを tooltip に, hasWarning=true
 *
 * title 属性 (= ホバー時 tooltip) + aria-label を同じ文字列にして
 * coarse pointer (touch) でも tap で表示できるようにする.
 */
function collectVisitWarningInfo(visit: VisitListItem): {
  hasWarning: boolean;
  tooltip: string | null;
} {
  const ws = visit.warnings ?? null;
  if (!ws || ws.length === 0) return { hasWarning: false, tooltip: null };
  const msgs: string[] = [];
  for (const w of ws) {
    if (!w) continue;
    const m = w.message;
    if (typeof m === 'string' && m.length > 0) msgs.push(m);
  }
  if (msgs.length === 0) return { hasWarning: false, tooltip: null };
  return { hasWarning: true, tooltip: msgs.join('\n') };
}

// ─────────────────────────────────────────────────────────────────────────
// VisitRowContent — visit 1 件の内容描画 (single / pair 共通)
// ─────────────────────────────────────────────────────────────────────────

interface VisitRowContentProps {
  visit: VisitListItem;
  onPatientClick?: (patientId: string) => void;
  weekView: boolean;
  /** ペア内距離 (0km) を省略する場合 true. */
  suppressDistance?: boolean;
  /** Phase G-21: 🔒 toggle ハンドラ. 指定時のみボタンが描画される. */
  onTogglePin?: (pfvId: string, nextPinned: boolean, scope: PinScope, patientId: string) => void;
}

function VisitRowContent({
  visit,
  onPatientClick,
  weekView,
  suppressDistance,
  onTogglePin,
}: VisitRowContentProps) {
  const timeCondition = weekView
    ? null
    : formatTimeCondition({
        time_type: visit.time_type,
        preferred_start: visit.preferred_start,
        preferred_end: visit.preferred_end,
      });
  const showAddress = !weekView;
  const showDuration =
    !weekView && typeof visit.duration_min === 'number' && visit.duration_min > 0;
  const showSexRestriction = !weekView;
  const showDistance = !weekView && !suppressDistance;

  return (
    <>
      {/* T-4: 行頭の性別ドット (日リストと同じ視覚言語)。 */}
      <i
        className="inline-block h-2 w-2 shrink-0 rounded-full"
        style={{ background: genderPalette(visit.patient_sex).bar }}
        aria-hidden="true"
      />
      <span className="tnum font-semibold text-text-primary">{trimSeconds(visit.start_time)}</span>
      {(() => {
        // Phase G-15: 性別制限による患者名 色付け (inline style で確実反映)
        //   female_only → 赤 / male_only → 青 / その他 → 標準
        const sexStyle: React.CSSProperties =
          visit.sex_restriction === 'female_only'
            ? { color: '#dc2626', fontWeight: 600 }
            : visit.sex_restriction === 'male_only'
              ? { color: '#2563eb', fontWeight: 600 }
              : {};
        return onPatientClick && visit.patient_id ? (
          <button
            type="button"
            className="text-text-primary underline-offset-2 hover:underline focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary"
            style={sexStyle}
            onClick={(e) => {
              e.stopPropagation();
              onPatientClick(visit.patient_id!);
            }}
            aria-label={`${visit.patient_name} の詳細を開く`}
          >
            {visit.patient_name}
          </button>
        ) : (
          <span className="text-text-primary" style={sexStyle}>
            {visit.patient_name}
          </span>
        );
      })()}
      {showDuration ? (
        <span
          className="text-[9px] text-text-secondary tnum"
          data-testid="visit-duration"
          aria-label={`実動時間 ${formatDuration(visit.duration_min!)}`}
        >
          ⏱ {formatDuration(visit.duration_min!)}
        </span>
      ) : null}
      {visit.area_label && !weekView ? (
        <span className="rounded bg-brand-primary/10 px-1 text-[9px] text-brand-primary">
          {visit.area_label}
        </span>
      ) : null}
      {showAddress && visit.address ? (
        <span
          className="text-[9px] text-text-muted"
          title={visit.address}
          aria-label={`住所 ${visit.address}`}
        >
          {visit.address.length > 18 ? `${visit.address.slice(0, 18)}…` : visit.address}
        </span>
      ) : null}
      {timeCondition ? (
        <span className="text-[9px] text-text-secondary">{timeCondition}</span>
      ) : null}
      {showSexRestriction && visit.sex_restriction === 'female_only' ? (
        <span className="text-[9px] text-pink-600">👩 女性のみ</span>
      ) : null}
      {showSexRestriction && visit.sex_restriction === 'male_only' ? (
        <span className="text-[9px] text-blue-600">👨 男性のみ</span>
      ) : null}
      {/* Phase G-21: 🔒 完全固定 toggle. 時間条件 / 性別制限の直後に配置 (距離より前). */}
      {onTogglePin ? <PinToggleButton visit={visit} onTogglePin={onTogglePin} /> : null}
      {/* 次の patient までの距離 (= VisitArrow). 行末右端に寄せる. */}
      {showDistance ? (
        <span className="ml-auto">
          <VisitArrow distanceKm={visit.distance_to_next_km ?? null} />
        </span>
      ) : null}
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// PinToggleButton — Phase G-21
// ─────────────────────────────────────────────────────────────────────────

interface PinToggleButtonProps {
  visit: VisitListItem;
  onTogglePin: (pfvId: string, nextPinned: boolean, scope: PinScope, patientId: string) => void;
}

/**
 * ピン留め toggle ボタン.
 *
 * - visit.fixed_visit_id が無い (= weekly_pattern 由来) → disabled + tooltip 「先に固定枠登録が必要」
 * - visit.is_pinned=true                              → PushPin (赤い丸頭)
 * - visit.is_pinned=false                             → PushPinOff (灰色アウトライン)
 *
 * 全 UI 共通の PushPin/PushPinOff アイコン (@/components/ui/push-pin) を使用し、
 * スケジュール表・週ビュー・リスト表示で「ピン留め」の見た目を統一する.
 */
export function PinToggleButton({ visit, onTogglePin }: PinToggleButtonProps) {
  const isPinned = visit.is_pinned === true;
  const pfvId = visit.fixed_visit_id ?? null;
  // Phase G-47: bulk (全曜日) スコープには patient_id が必須.
  // visit.patient_id が無い (= weekly_pattern fallback 等の例外) ときは disabled.
  const patientId = visit.patient_id ?? null;
  const disabled = !pfvId || !patientId;
  // 論点 1 (PO 決定 2026-08-08): 無効の理由を正しく伝える。
  // 固定枠が在るのに時刻がズレている場合、「先に固定枠登録が必要」は嘘になる。
  // 合わせ直す導線は設けず、事実を伝えるだけにする。
  const masterStartTime = visit.master_start_time ?? null;
  const diverged = !pfvId && !!masterStartTime;
  const disabledReason = diverged
    ? `固定訪問スケジュールは ${masterStartTime} です。この時間帯では完全固定にできません`
    : '先に固定枠登録が必要';
  // Phase G-47: click 即時 toggle を廃し、 PinScopeMenu で「曜日のみ / 全曜日」 2 択を提示.
  return (
    <PinScopeMenu
      pfvId={pfvId ?? ''}
      patientId={patientId ?? ''}
      isPinned={isPinned}
      onSelect={onTogglePin}
      disabled={disabled}
      testIdSuffix={`weekday-${visit.key}`}
    >
      {({ isOpen }) => (
        <button
          type="button"
          onPointerDown={(e) => e.stopPropagation()}
          disabled={disabled}
          aria-label={
            isPinned
              ? `${visit.patient_name} の完全固定スコープを選択 (完全固定を外す)`
              : disabled
                ? `${visit.patient_name} は${disabledReason}`
                : `${visit.patient_name} の完全固定スコープを選択 (完全固定にする)`
          }
          title={
            disabled
              ? disabledReason
              : isPinned
                ? '完全固定を外すスコープを選択 (この曜日のみ / 全曜日)'
                : '完全固定にするスコープを選択 (この曜日のみ / 全曜日)'
          }
          aria-pressed={isPinned}
          aria-haspopup="menu"
          aria-expanded={isOpen}
          data-testid={`weekday-pin-btn-${visit.key}`}
          data-pinned={isPinned ? 'true' : 'false'}
          data-pfv-id={pfvId ?? ''}
          className={cn(
            'inline-flex items-center justify-center rounded px-1 py-0.5 leading-none',
            isPinned ? 'text-yellow-700 hover:bg-yellow-100' : 'text-text-muted hover:bg-bg-muted',
            disabled ? 'cursor-not-allowed opacity-30' : '',
          )}
        >
          {isPinned ? <PushPin className="h-3.5 w-3.5" /> : <PushPinOff className="h-3.5 w-3.5" />}
        </button>
      )}
    </PinScopeMenu>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Adapter Helpers — caller 側で V2 / patients から VisitListItem を構築する
// ─────────────────────────────────────────────────────────────────────────

/**
 * 2 地点 (lat, lng) 間の Haversine 距離 (km).
 * Backend と同等のアルゴリズム (CourseListColumn 移植元の用途と整合).
 */
export function haversineKm(
  a: { lat: number; lng: number },
  b: { lat: number; lng: number },
): number {
  const R = 6371; // 地球半径 (km)
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(x)));
}

// `buildSameAddressKey` は共有 util `@/lib/scheduling/freeGaps` へ移設 (PC 盤 /
// モバイル盤 共有・bundle 軽量化のため)。既存 import 互換のためここから再エクスポートする。
export { buildSameAddressKey };
