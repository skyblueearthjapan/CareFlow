'use client';

/**
 * TimelineDayList — タイムライン兄弟の「日ごとリスト」(T-1L・読み取り主体)。
 *
 * docs/mockups/timeline-comparison-mock.html のリスト意匠を本番へ:
 *   - 整列した列 (時刻 / 利用者 / 時間 / サービス相当 / 住所・条件 / 次まで) + 列見出し
 *   - 行頭の性別ドット + 性別色の左帯 (タイムラインのカードと同じ視覚言語)
 *   - コースごとのグループ見出し (拠点+コードのチップ / 担当スタッフ名 / n/N件)
 *
 * ただし本番の情報は 1 つも落とさない: 実動時間・住所・個別条件・性別制限・同住所・
 * 移動警告・ピン・次目的地距離・空き枠(時刻順 interleave) を各列に畳み込む。
 *
 * 共有コア WeekdayScheduleCard は提案ダイアログ専用に温存し、本コンポーネントは
 * /schedule の日リストモード専用 (= 提案系に影響しない)。表示専用: 患者クリックで
 * 既存の患者詳細を開く / ピントグルは既存ハンドラを呼ぶだけ。
 */

import type { CourseListItem, VisitListItem } from '@/components/schedule/WeekdayScheduleCard';
import { formatTimeCondition, PinToggleButton } from '@/components/schedule/WeekdayScheduleCard';
import { CornerPushPin } from '@/components/ui/push-pin';
import type { PinScope } from '@/components/schedule/v2/PinScopeMenu';
import { trimSeconds } from '@/components/schedule/v2/_autoScheduleUtils';
import { formatDuration } from '@/lib/format/duration';
import { fmtHM, type FreeGap } from '@/lib/scheduling/freeGaps';
import { genderPalette } from '@/lib/scheduling/timeline';
import { partnerInfo } from './TimelineDayBoard';
import { cn } from '@/lib/utils';

export interface TimelineDayListProps {
  courses: CourseListItem[];
  onPatientClick?: (patientId: string) => void;
  onTogglePin?: (pfvId: string, nextPinned: boolean, scope: PinScope, patientId: string) => void;
  /**
   * G2 (T-6 パリティ): 訪問削除 (行末の ×)。canEdit のときだけ Panel が渡す。
   * 未指定なら × を描画しない。確認ダイアログは Panel 側 (handleDeleteVisit) が持つ。
   * visit_id を持たない行 (提案系のダミー行) には出さない。
   */
  onDeleteVisit?: (visitId: string, patientName: string) => void;
  /**
   * G3 (T-6 パリティ): source='manual_week' 行の「今週のみ」チップ → 固定昇格。
   * 未指定なら非クリックの表示専用チップになる。
   */
  onPromoteWeekOnly?: (patientId: string, patientName: string) => void;
}

/** G3: 「今週のみ」→ 固定昇格の確認文 (CourseDayTable / TimelineDayBoard と同一文言)。 */
const PROMOTE_WEEK_ONLY_CONFIRM = 'この配置を固定訪問週間（毎週の型）に反映しますか？';

const CC_COLOR: Record<string, string> = {
  A: '#0D8478',
  B: '#2F6FB0',
  C: '#8B5C9E',
  D: '#C75C77',
  E: '#B0831F',
  M: '#D97706',
};

function courseColor(code: string | null | undefined): string {
  const head = (code ?? '').trim().charAt(0).toUpperCase();
  return CC_COLOR[head] ?? '#0D8478';
}

/** グリッドの列テンプレ (時刻 / 利用者 / 時間 / サービス・警告 / 住所・条件 / 次まで)。 */
const GRID_COLS =
  'grid-cols-[76px_minmax(120px,1.3fr)_58px_minmax(80px,1fr)_minmax(120px,1.4fr)_84px]';

function VisitRow({
  v,
  onPatientClick,
  onTogglePin,
  onDeleteVisit,
  onPromoteWeekOnly,
}: {
  v: VisitListItem;
  onPatientClick?: (patientId: string) => void;
  onTogglePin?: TimelineDayListProps['onTogglePin'];
  onDeleteVisit?: TimelineDayListProps['onDeleteVisit'];
  onPromoteWeekOnly?: TimelineDayListProps['onPromoteWeekOnly'];
}) {
  const pal = genderPalette(v.patient_sex);
  const cond = formatTimeCondition({
    time_type: v.time_type,
    preferred_start: v.preferred_start,
    preferred_end: v.preferred_end,
  });
  const warn = (v.warnings ?? []).find((w) => w && w.message);
  const restrictLabel =
    v.sex_restriction === 'female_only'
      ? '👩女性のみ'
      : v.sex_restriction === 'male_only'
        ? '👨男性のみ'
        : null;
  // 条件ビット (時間条件 / 性別制限)。ピンは PushPin アイコンで別描画 (🔒→ピン統一)。
  // 同住所はペア囲みで表現するのでここでは出さない。
  const condBits = [cond ?? '', restrictLabel ?? ''].filter(Boolean).join(' ');
  // T-6撤去: 旧テーブルの ①/② と「相方: ...」注記を移設 (相方プール残存は警告色)。
  const partner = partnerInfo(v);

  return (
    <div
      className={cn(
        // タイムラインカードと同じ視覚言語 (性別ウォッシュ地 + 左帯 + 角丸) の
        // 「縦幅の狭いカード行」(PO要望: 日/週リストをカードUIへ統一)。
        'relative grid items-center gap-2.5 rounded-md border border-l-[3px] px-2 py-1 text-[12px] shadow-[var(--shadow-xs)] transition-shadow hover:shadow-[var(--shadow-sm)]',
        GRID_COLS,
        // M-2: 移動警告は行全体を薄い赤で強調 (性別ウォッシュより優先)。
        warn && 'bg-error-bg/40 hover:bg-error-bg/60',
        // L-3: ピン留め行は薄い琥珀背景で強調 (性別ウォッシュより優先)。
        !warn && v.is_pinned && 'bg-amber-50/60',
      )}
      style={{
        borderColor: pal.ln,
        borderLeftColor: pal.bar,
        // 警告/ピンの背景クラスを潰さないよう、通常行だけ性別ウォッシュを敷く。
        ...(warn || v.is_pinned ? {} : { background: pal.bg }),
      }}
      title={warn?.message ?? undefined}
      data-testid={`tdl-row-${v.key}`}
    >
      {/* ピン留め: 行右上に打ち込んだ画鋲 (📍住所と誤認しない・PO要望 2026-07-08)。 */}
      {v.is_pinned ? <CornerPushPin className="h-4 w-4" /> : null}
      <span className="tnum font-bold text-text-primary">
        {trimSeconds(v.start_time)}
        {v.duration_min ? (
          <span className="ml-1 text-[9px] font-medium text-text-muted">
            〜{fmtHMFromStart(v.start_time, v.duration_min)}
          </span>
        ) : null}
      </span>

      <span className="flex min-w-0 items-center gap-1.5 font-bold">
        <i
          className="inline-block h-2 w-2 shrink-0 rounded-full"
          style={{ background: pal.bar }}
          aria-hidden="true"
        />
        {onPatientClick && v.patient_id ? (
          <button
            type="button"
            className="truncate text-left text-text-primary underline-offset-2 hover:underline focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary"
            onClick={() => onPatientClick(v.patient_id!)}
          >
            {v.patient_name}
          </button>
        ) : (
          <span className="truncate text-text-primary">{v.patient_name}</span>
        )}
        {/* T-6撤去: ①/② = 2名体制の slot。相方未配置なら警告色 (旧テーブルから移設)。 */}
        {partner.slotMark ? (
          <span
            data-testid={`tdl-slot-mark-${v.key}`}
            aria-label={`2名体制 ${partner.slotMark}`}
            className={cn(
              'shrink-0 text-[11px] leading-none',
              partner.warn ? 'text-error' : 'text-text-secondary',
            )}
          >
            {partner.slotMark}
          </span>
        ) : null}
        {onTogglePin ? (
          <span className="shrink-0">
            <PinToggleButton visit={v} onTogglePin={onTogglePin} />
          </span>
        ) : null}
        {/* G2: 訪問削除 (×) — ピン留めトグルの隣。visit_id が無い行には出さない。 */}
        {onDeleteVisit && v.visit_id ? (
          <button
            type="button"
            data-testid={`tdl-delete-visit-${v.key}`}
            aria-label={`${v.patient_name} の訪問を削除`}
            title="この訪問を削除"
            className="shrink-0 rounded p-0.5 text-[11px] font-bold leading-none text-error opacity-40 transition-opacity hover:bg-error-bg hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-error"
            onClick={() => onDeleteVisit(v.visit_id!, v.patient_name)}
          >
            ×
          </button>
        ) : null}
      </span>

      <span className="tnum text-text-secondary">
        {v.duration_min ? formatDuration(v.duration_min) : '—'}
      </span>

      <span className="flex min-w-0 flex-nowrap items-center gap-1 overflow-hidden text-[11px] text-text-muted">
        {/* M-4: 警告と area_label は排他にせず両方出す (1行厳守・全文は行 title で読める)。 */}
        {warn ? (
          <span className="min-w-0 truncate rounded bg-error-bg px-1 py-px text-[10px] font-bold text-error">
            ⚠ {warn.message ?? '移動警告'}
          </span>
        ) : null}
        {/* T-6撤去: 相方の現在地 (旧テーブルの注記)。プール残存 = 片側未配置 → 警告色。 */}
        {partner.text ? (
          <span
            data-testid={`tdl-partner-note-${v.key}`}
            title={partner.tooltip ?? undefined}
            className={cn(
              'min-w-0 truncate rounded px-1 text-[10px]',
              partner.warn ? 'bg-error-bg font-bold text-error' : 'text-text-muted',
            )}
          >
            👥 {partner.text}
          </span>
        ) : null}
        {v.area_label ? (
          <span className="shrink-0 rounded bg-brand-primary/10 px-1 text-[10px] text-brand-primary">
            {v.area_label}
          </span>
        ) : null}
        {!warn && !partner.text && !v.area_label ? '—' : null}
      </span>

      <span className="min-w-0 truncate text-[11px] text-text-muted">
        {v.address ?? ''}
        {v.same_address_group_id ? (
          <span className="ml-1 text-[10px] font-semibold text-amber-700">📍同住所</span>
        ) : null}
        {condBits ? <span className="ml-1 text-text-secondary">{condBits}</span> : null}
        {/* G3: 「今週のみ」= この週だけの配置。クリックで毎週の型へ昇格 (confirm あり)。 */}
        {v.source === 'manual_week' ? <WeekOnlyChip v={v} onPromote={onPromoteWeekOnly} /> : null}
      </span>

      <span
        className="tnum text-right text-[11px] text-text-muted"
        data-testid="visit-distance"
        aria-label={
          v.distance_to_next_km != null ? `移動 ${v.distance_to_next_km.toFixed(1)}km` : undefined
        }
      >
        {v.distance_to_next_km != null ? `${v.distance_to_next_km.toFixed(1)}km` : '—'}
      </span>
    </div>
  );
}

/**
 * 「今週のみ」チップ (G3)。onPromote 指定時のみクリック可
 * (confirm → 固定訪問週間 = 毎週の型 へ昇格)。patient_id 未設定の行では出さない。
 */
function WeekOnlyChip({
  v,
  onPromote,
}: {
  v: VisitListItem;
  onPromote?: TimelineDayListProps['onPromoteWeekOnly'];
}) {
  if (!v.patient_id) return null;
  const base =
    'ml-1 inline-flex flex-shrink-0 items-center rounded bg-amber-100 px-1 py-0.5 text-[9px] font-semibold text-amber-800 ring-1 ring-amber-300';
  if (!onPromote) {
    return (
      <span
        data-testid={`tdl-week-only-chip-${v.key}`}
        className={base}
        title="この週だけの配置です"
      >
        今週のみ
      </span>
    );
  }
  return (
    <button
      type="button"
      data-testid={`tdl-week-only-chip-${v.key}`}
      className={cn(base, 'cursor-pointer hover:bg-amber-200')}
      title="この週だけの配置です。クリックで固定訪問週間（毎週の型）に反映できます"
      onClick={() => {
        if (window.confirm(PROMOTE_WEEK_ONLY_CONFIRM)) {
          onPromote(v.patient_id!, v.patient_name);
        }
      }}
    >
      今週のみ
    </button>
  );
}

/** 空き枠マーカー行 (時刻順 interleave)。 */
function FreeGapRow({ gap }: { gap: FreeGap }) {
  return (
    <div
      className={cn('grid items-center gap-2.5 px-2 py-0.5', GRID_COLS)}
      data-testid={`tdl-gap-${gap.startMin}`}
    >
      <span className="tnum text-[11px] font-semibold text-brand-primary">
        {fmtHM(gap.startMin)}
      </span>
      <span className="col-span-5 flex items-center gap-1.5 text-[11px] font-semibold text-brand-primary">
        <span className="border-l-2 border-amber-500 pl-1.5">
          {fmtHM(gap.startMin)}〜{fmtHM(gap.endMin)} 空き時間（{gap.endMin - gap.startMin}分）
        </span>
      </span>
    </div>
  );
}

/** visit と空き枠を時刻順にマージ (capacity.remaining>0 のときのみ gap を出す)。 */
function interleave(
  course: CourseListItem,
): Array<{ kind: 'visit'; v: VisitListItem } | { kind: 'gap'; g: FreeGap }> {
  const rows: Array<{ kind: 'visit'; v: VisitListItem } | { kind: 'gap'; g: FreeGap }> =
    course.visits.map((v) => ({ kind: 'visit' as const, v }));
  const remaining = course.capacity ? course.capacity.max - course.capacity.filled : 0;
  const showGaps = course.capacity != null && remaining > 0 && (course.freeGaps?.length ?? 0) > 0;
  if (showGaps) {
    for (const g of course.freeGaps!) rows.push({ kind: 'gap' as const, g });
  }
  const startMin = (r: (typeof rows)[number]): number =>
    r.kind === 'gap' ? r.g.startMin : hhmmToMin(r.v.start_time);
  return rows.sort((a, b) => startMin(a) - startMin(b));
}

function hhmmToMin(t: string): number {
  const [h, m] = t.split(':').map(Number);
  return (h ?? 0) * 60 + (m ?? 0);
}

type InterleaveRow = { kind: 'visit'; v: VisitListItem } | { kind: 'gap'; g: FreeGap };
type GroupedRow =
  | { kind: 'visit'; v: VisitListItem }
  | { kind: 'gap'; g: FreeGap }
  | { kind: 'sameaddr'; rows: VisitListItem[] };

/**
 * 連続する同 same_address_group_id の visit 行 (=別患者・同住所) を 1 グループに束ねる。
 * 旧 WeekdayScheduleCard の clusterVisits と同じ視覚言語 (囲み) を list に戻すため (M-1)。
 * gap 行はグループを分断する。単独 (同 group が 1 件だけ) はそのまま visit として返す。
 */
function groupSameAddress(rows: InterleaveRow[]): GroupedRow[] {
  const out: GroupedRow[] = [];
  let i = 0;
  while (i < rows.length) {
    const r = rows[i]!;
    if (r.kind === 'gap') {
      out.push(r);
      i++;
      continue;
    }
    const gid = r.v.same_address_group_id ?? null;
    if (gid) {
      // 後続の visit 行で同じ group_id・別患者が続く限り束ねる。
      const bucket: VisitListItem[] = [r.v];
      let j = i + 1;
      while (
        j < rows.length &&
        rows[j]!.kind === 'visit' &&
        (rows[j] as { v: VisitListItem }).v.same_address_group_id === gid
      ) {
        bucket.push((rows[j] as { v: VisitListItem }).v);
        j++;
      }
      if (bucket.length >= 2) {
        out.push({ kind: 'sameaddr', rows: bucket });
        i = j;
        continue;
      }
    }
    out.push({ kind: 'visit', v: r.v });
    i++;
  }
  return out;
}

function fmtHMFromStart(start: string, durationMin: number): string {
  return fmtHM(hhmmToMin(start) + durationMin);
}

export function TimelineDayList({
  courses,
  onPatientClick,
  onTogglePin,
  onDeleteVisit,
  onPromoteWeekOnly,
}: TimelineDayListProps) {
  return (
    <div className="space-y-3" data-testid="timeline-day-list">
      {courses.map((c) => {
        const rows = interleave(c);
        return (
          <div key={c.key} className="rounded-lg border border-border-default bg-bg-base px-3 py-2">
            {/* グループ見出し: 拠点+コードのチップ / 担当スタッフ名 / n/N件 */}
            <div className="flex items-center gap-2 border-b-2 border-border-default pb-1">
              <span
                className="rounded px-1.5 py-0.5 text-[10px] font-extrabold text-white"
                style={{ background: courseColor(c.course_code) }}
              >
                {c.office_name ?? ''}
                {c.course_code ?? ''}
              </span>
              <span className="text-[13px] font-bold text-text-primary">
                {c.staff_name ?? '（未割当）'}
              </span>
              {c.capacity ? (
                <span
                  className={cn(
                    'tnum text-[11px] text-text-muted',
                    c.capacity.filled >= c.capacity.max && 'text-warning',
                  )}
                >
                  {c.capacity.filled}/{c.capacity.max}件
                </span>
              ) : c.summary ? (
                <span className="tnum text-[11px] text-text-muted">{c.summary}</span>
              ) : null}
            </div>

            {/* 列見出し */}
            <div
              className={cn(
                'grid gap-2.5 px-2 pb-1 pt-1 text-[10px] font-bold tracking-wide text-text-muted',
                GRID_COLS,
              )}
            >
              <span>時刻</span>
              <span>利用者</span>
              <span>時間</span>
              <span>状況</span>
              <span>住所 / 条件</span>
              <span className="text-right">前から</span>
            </div>

            <div className="space-y-1 pt-0.5">
              {rows.length === 0 ? (
                <div className="px-2 py-3 text-[12px] text-text-muted">
                  この日の予定はありません。
                </div>
              ) : (
                groupSameAddress(rows).map((grp, gi) =>
                  grp.kind === 'sameaddr' ? (
                    // M-1: 同住所ペアは琥珀の囲みでひと目で分かるように (旧リスト踏襲)。
                    // 見出し行は置かず高さを節約 (各行の📍同住所チップ + title で内訳が分かる)。
                    <div
                      key={`sa-${gi}`}
                      className="my-0.5 rounded-md border border-amber-400 bg-amber-50/50"
                      title={`同住所（${grp.rows.length}名・同時刻帯に連続訪問）`}
                      data-testid={`tdl-sameaddr-${gi}`}
                    >
                      <div className="space-y-0.5 p-0.5">
                        {grp.rows.map((v) => (
                          <VisitRow
                            key={v.key}
                            v={v}
                            onPatientClick={onPatientClick}
                            onTogglePin={onTogglePin}
                            onDeleteVisit={onDeleteVisit}
                            onPromoteWeekOnly={onPromoteWeekOnly}
                          />
                        ))}
                      </div>
                    </div>
                  ) : grp.kind === 'gap' ? (
                    <FreeGapRow key={`gap-${grp.g.startMin}-${gi}`} gap={grp.g} />
                  ) : (
                    <VisitRow
                      key={grp.v.key}
                      v={grp.v}
                      onPatientClick={onPatientClick}
                      onTogglePin={onTogglePin}
                      onDeleteVisit={onDeleteVisit}
                      onPromoteWeekOnly={onPromoteWeekOnly}
                    />
                  ),
                )
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
