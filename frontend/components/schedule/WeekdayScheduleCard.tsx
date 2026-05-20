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
import { VisitArrow } from './v2/VisitArrow';
import { trimSeconds } from './v2/_autoScheduleUtils';

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
}

function CourseRow({ course, maxVisits, testIdPrefix, onPatientClick, weekView }: CourseRowProps) {
  // 「最大 N 件」制限は visit 単位で先に slice して、その後に cluster 化する.
  // (pair の片割れだけが切れる事態を避けるため、ペア跨ぎは clusterVisits 後に
  //  pair 数を維持したまま描画される — overflow 件数のみ patient 単位で計算).
  const sliced = maxVisits && maxVisits > 0 ? course.visits.slice(0, maxVisits) : course.visits;
  const overflowCount = course.visits.length - sliced.length;
  const clusters = React.useMemo(() => clusterVisits(sliced), [sliced]);

  return (
    <li className="px-2 py-1.5">
      <div className="flex items-center justify-between text-[11px]">
        <span className="font-semibold text-text-primary">{course.title}</span>
        {course.summary ? <span className="tnum text-text-muted">{course.summary}</span> : null}
      </div>
      {clusters.length > 0 ? (
        <ul className="mt-1 space-y-0.5">
          {clusters.map((cluster, ci) => {
            if (cluster.kind === 'single') {
              const v = cluster.visit;
              return (
                <VisitRow
                  key={v.key}
                  visit={v}
                  testIdPrefix={testIdPrefix}
                  onPatientClick={onPatientClick}
                  weekView={weekView}
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
                key={`pair-${cluster.groupId}-${ci}`}
                cluster={cluster}
                nextAfterPair={nextAfterPair}
                testIdPrefix={testIdPrefix}
                onPatientClick={onPatientClick}
                weekView={weekView}
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
// PairCluster — 同住所ペアを 1 つの囲みで描画
// ─────────────────────────────────────────────────────────────────────────

interface PairClusterProps {
  cluster: { kind: 'pair'; visits: VisitListItem[]; groupId: string };
  /** ペアの最後尾 visit の「次の patient」 (= 距離計算用. ペア外の visit). */
  nextAfterPair: VisitListItem | null;
  testIdPrefix?: string;
  onPatientClick?: (patientId: string) => void;
  weekView: boolean;
}

function PairCluster({
  cluster,
  nextAfterPair,
  testIdPrefix,
  onPatientClick,
  weekView,
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
          // ペア内 i=0..N-2 → 「次のペアメンバー」は同住所 (= 0km) なので距離は出さない.
          //   isLastInPair=false → suppressDistance=true.
          // ペア内 i=N-1 (最後) → 次のペア外要素 (nextAfterPair) があれば距離を出す.
          //   isLastInPair=true & nextAfterPair!=null → distance を表示.
          const isLastInPair = i === visits.length - 1;
          const suppressDistance = !isLastInPair || nextAfterPair == null;
          return (
            <PairMemberRow
              key={v.key}
              visit={v}
              suppressDistance={suppressDistance}
              testIdPrefix={testIdPrefix}
              onPatientClick={onPatientClick}
              weekView={weekView}
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
}: PairMemberRowProps) {
  // CareFlow #101 FE: warning ありの visit は赤枠ハイライト + tooltip 表示.
  const warningInfo = collectVisitWarningInfo(visit);
  return (
    <li
      className={cn(
        'flex flex-wrap items-center gap-1 py-0.5 text-[10px]',
        warningInfo.hasWarning ? 'rounded border border-red-500 bg-red-50/40 px-1' : '',
      )}
      data-testid={testIdPrefix ? `${testIdPrefix}-visit-${visit.key}` : undefined}
      data-same-address-group-id={visit.same_address_group_id ?? undefined}
      data-pair-member="true"
      data-visit-warning={warningInfo.hasWarning ? 'true' : undefined}
      title={warningInfo.tooltip ?? undefined}
      aria-label={warningInfo.tooltip ?? undefined}
    >
      <VisitRowContent
        visit={visit}
        onPatientClick={onPatientClick}
        weekView={weekView}
        // Phase E-1: ペア内の各 visit にも住所を表示する (ユーザー要望: 同住所ペアで
        // 住所が見えない問題の修正). ヘッダーの「📍 同住所」ラベルはペアの可視化
        // 用として維持しつつ、住所詳細は各カードに復活させる.
        // ペア内距離 (= 同住所なので 0km) は親側 (PairCluster) が決定する.
        suppressDistance={suppressDistance}
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
}

function VisitRow({ visit, testIdPrefix, onPatientClick, weekView }: VisitRowProps) {
  // CareFlow #101 FE: warning ありの visit は赤枠ハイライト + tooltip 表示.
  const warningInfo = collectVisitWarningInfo(visit);
  return (
    <li
      className={cn(
        'flex flex-wrap items-center gap-1 text-[10px]',
        warningInfo.hasWarning ? 'rounded border border-red-500 bg-red-50/40 px-1' : '',
      )}
      data-testid={testIdPrefix ? `${testIdPrefix}-visit-${visit.key}` : undefined}
      data-same-address-group-id={visit.same_address_group_id ?? undefined}
      data-visit-warning={warningInfo.hasWarning ? 'true' : undefined}
      title={warningInfo.tooltip ?? undefined}
      aria-label={warningInfo.tooltip ?? undefined}
    >
      <VisitRowContent visit={visit} onPatientClick={onPatientClick} weekView={weekView} />
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
}

function VisitRowContent({
  visit,
  onPatientClick,
  weekView,
  suppressDistance,
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
      <span className="tnum text-text-muted">{trimSeconds(visit.start_time)}</span>
      {(() => {
        // Phase G-15: 性別制限による患者名 色付け
        //   female_only → 赤 / male_only → 青 / その他 → 標準 (text-text-primary)
        const sexColorClass =
          visit.sex_restriction === 'female_only'
            ? 'text-red-600'
            : visit.sex_restriction === 'male_only'
              ? 'text-blue-600'
              : 'text-text-primary';
        return onPatientClick && visit.patient_id ? (
          <button
            type="button"
            className={`${sexColorClass} underline-offset-2 hover:underline focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary`}
            onClick={(e) => {
              e.stopPropagation();
              onPatientClick(visit.patient_id!);
            }}
            aria-label={`${visit.patient_name} の詳細を開く`}
          >
            {visit.patient_name}
          </button>
        ) : (
          <span className={sexColorClass}>{visit.patient_name}</span>
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
      {/* 次の patient までの距離 (= VisitArrow). null なら描画しない. */}
      {showDistance ? <VisitArrow distanceKm={visit.distance_to_next_km ?? null} /> : null}
    </>
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

/**
 * lat/lng から同住所グループキーを生成する (tolerance 0.001 ≒ 100m).
 * Backend と同じ rounding (toFixed(3)) で bucket 化.
 * lat/lng が無い patient は null を返す (グルーピングしない).
 */
export function buildSameAddressKey(
  lat: number | null | undefined,
  lng: number | null | undefined,
): string | null {
  if (lat === null || lat === undefined || lng === null || lng === undefined) return null;
  return `${lat.toFixed(3)}:${lng.toFixed(3)}`;
}
