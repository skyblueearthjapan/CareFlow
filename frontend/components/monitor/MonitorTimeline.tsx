'use client';

/**
 * 訪問モニター タイムライン (ガント) — スタッフ×時刻 (8–19h)。
 *
 * 各コース行に予定バー (患者名) + 実績バー (alert_level 色) を重ねる。行クリックで
 * コース選択 (地図に順路)、バークリックで訪問詳細。選択行は強調 + 他行ディム。
 * 「今」ラインと次までの距離も表示 (pc-proto.html 準拠)。
 */
import { cn } from '@/lib/utils';
import type { MonitorStaffRow, MonitorVisit } from '@/lib/schemas/monitor';

import {
  MISSING_BAR_BG,
  PLAN_BAR_BG,
  PLAN_BAR_BORDER,
  STATUS_COLOR,
  TL_END_MIN,
  TL_START_MIN,
  displayStatus,
  formatDistance,
  hmToMinutes,
  isoToHm,
  minutesToPct,
} from './constants';

interface MonitorTimelineProps {
  rows: MonitorStaffRow[];
  selectedStaffId: string | null;
  selectedVisitId: string | null;
  /** 現在時刻 (分, JST)。「今」ライン用。 */
  nowMinutes: number;
  onSelectStaff: (staffId: string | null) => void;
  onSelectVisit: (visitId: string) => void;
}

const HOURS = Array.from({ length: (TL_END_MIN - TL_START_MIN) / 60 + 1 }, (_, i) => 8 + i);

export function MonitorTimeline({
  rows,
  selectedStaffId,
  selectedVisitId,
  nowMinutes,
  onSelectStaff,
  onSelectVisit,
}: MonitorTimelineProps) {
  const hasSelection = selectedStaffId !== null;

  return (
    <div className="min-w-[880px] select-none px-1 pb-4" data-testid="monitor-timeline">
      {/* 時間軸ヘッダ */}
      <div className="sticky top-0 z-[5] grid grid-cols-[156px_1fr] border-b border-border-default bg-bg-base">
        <div className="p-2 text-[11px] text-text-muted">#／スタッフ</div>
        <div className="flex">
          {HOURS.map((h) => (
            <span
              key={h}
              className="flex-1 border-l border-border-default py-2 pl-0.5 text-[11px] text-text-muted"
            >
              {h}
            </span>
          ))}
        </div>
      </div>

      {rows.length === 0 && (
        <div className="px-4 py-10 text-center text-sm text-text-muted">
          この日の訪問はありません。
        </div>
      )}

      {rows.map((row, idx) => {
        const isSel = row.staff_id != null && row.staff_id === selectedStaffId;
        const key = row.staff_id ?? `unassigned-${idx}`;
        return (
          <div
            key={key}
            role="button"
            tabIndex={0}
            data-testid={`monitor-row-${idx}`}
            onClick={() => onSelectStaff(row.staff_id ?? null)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') onSelectStaff(row.staff_id ?? null);
            }}
            className={cn(
              'grid min-h-[66px] cursor-pointer grid-cols-[156px_1fr] border-b border-border-default/60 transition-[opacity,background] duration-150',
              isSel
                ? 'bg-brand-primary-light shadow-[inset_5px_0_0_var(--brand-primary)]'
                : 'hover:bg-bg-muted',
              hasSelection && !isSel ? 'opacity-40' : '',
            )}
          >
            {/* 左: 行番号 + スタッフ */}
            <div className="flex items-center gap-2 px-2 py-1.5">
              <span
                className={cn(
                  'flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-[11px] font-bold tabular-nums',
                  isSel ? 'bg-brand-primary text-white' : 'bg-bg-muted text-text-secondary',
                )}
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
                <span className="text-[10.5px] text-text-muted">
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
              {/* 今ライン */}
              {nowMinutes >= TL_START_MIN && nowMinutes <= TL_END_MIN && (
                <div
                  className="absolute bottom-0 top-0 z-[4] w-0.5 bg-brand-accent"
                  style={{ left: `${minutesToPct(nowMinutes)}%` }}
                  aria-hidden
                >
                  <span className="absolute -top-px left-1 text-[9px] font-bold text-brand-accent">
                    今
                  </span>
                </div>
              )}
              {row.visits.map((v) => (
                <VisitBars
                  key={v.visit_id}
                  visit={v}
                  nowMinutes={nowMinutes}
                  isSelected={v.visit_id === selectedVisitId}
                  onSelect={onSelectVisit}
                />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function VisitBars({
  visit,
  nowMinutes,
  isSelected,
  onSelect,
}: {
  visit: MonitorVisit;
  nowMinutes: number;
  isSelected: boolean;
  onSelect: (visitId: string) => void;
}) {
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

  return (
    <>
      {/* 患者名ラベル (ペア訪問は「2名」バッジ付き) */}
      <div
        className="pointer-events-none absolute top-1 flex items-center gap-1 whitespace-nowrap text-[10px] font-semibold text-text-secondary [text-shadow:0_0_3px_#fff,0_0_3px_#fff]"
        style={{ left: `${pL}%` }}
        title={visit.patient_name ?? undefined}
      >
        {visit.patient_name ?? '—'}
        {isPair && (
          <span className="rounded-full bg-c-coupled-bg px-1 py-px text-[8.5px] font-bold text-c-coupled [text-shadow:none]">
            2名
          </span>
        )}
      </div>
      {/* はみ出し印 (時間軸外の訪問) */}
      {overflowLeft && (
        <span
          className="pointer-events-none absolute top-5 left-0 z-[3] text-[10px] font-bold text-text-muted"
          title={`${visit.start_time} 開始（表示範囲外）`}
        >
          ‹
        </span>
      )}
      {overflowRight && (
        <span
          className="pointer-events-none absolute top-5 right-0 z-[3] text-[10px] font-bold text-text-muted"
          title={`${visit.end_time} 終了（表示範囲外）`}
        >
          ›
        </span>
      )}
      {/* 次までの距離 */}
      {dn != null && (
        <div
          className="pointer-events-none absolute top-[21px] whitespace-nowrap text-[9px] text-text-muted [text-shadow:0_0_3px_#fff,0_0_3px_#fff]"
          style={{ left: `${minutesToPct(pe)}%` }}
        >
          →{formatDistance(dn)}
        </div>
      )}
      {/* 予定バー (ハッチ) */}
      <button
        type="button"
        data-testid={`monitor-bar-plan-${visit.visit_id}`}
        onClick={(e) => {
          e.stopPropagation();
          onSelect(visit.visit_id);
        }}
        title={`予定 ${visit.start_time}–${visit.end_time} ${visit.patient_name ?? ''}`}
        className="absolute top-5 h-3.5 rounded-[5px] border opacity-90"
        style={{
          left: `${pL}%`,
          width: `${pW}%`,
          borderColor: PLAN_BAR_BORDER,
          backgroundImage: PLAN_BAR_BG,
        }}
      />
      {/* 実績バー */}
      {hasActual && (
        <button
          type="button"
          data-testid={`monitor-bar-actual-${visit.visit_id}`}
          data-status={status}
          onClick={(e) => {
            e.stopPropagation();
            onSelect(visit.visit_id);
          }}
          className={cn(
            'absolute top-[38px] flex h-[15px] items-center gap-0.5 overflow-hidden whitespace-nowrap rounded-[5px] px-1.5 text-[10px] font-semibold text-white',
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
                }
              : { left: `${actLeft}%`, width: `${actWidth}%`, backgroundColor: color }
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
    </>
  );
}

/** ISO (UTC) → JST の「その日の分」。タイムライン座標用。 */
function isoToMinutesJst(iso: string): number {
  const hm = isoToHm(iso); // "HH:MM" (JST)
  return hmToMinutes(hm);
}
