'use client';

/**
 * 詳細パネル — 選択中の visit の予定/到着/退出/滞在/距離/GPS精度/理由/次距離。
 * 未訪問は即連絡ボックスを最上部に出す。visit 未選択でコースのみ選択時は訪問一覧
 * (stop list) を出す。
 */
import { useState } from 'react';
import {
  Check,
  Circle,
  CircleCheck,
  CircleDot,
  CircleX,
  Clock,
  OctagonAlert,
  Timer,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { MonitorStaffRow, MonitorVisit } from '@/lib/schemas/monitor';

import {
  LONG_INPROGRESS_REASON,
  STATUS_COLOR,
  STATUS_JUDGE,
  STATUS_LABEL,
  type DisplayStatus,
  displayStatus,
  formatDistance,
  isLongInprogress,
  isoToHm,
  isoToYmdHm,
} from './constants';

/** 判定見出しの status → lucide アイコン。 */
const STATUS_ICON: Record<DisplayStatus, LucideIcon> = {
  match: CircleCheck,
  review: TriangleAlert,
  mismatch: CircleX,
  inprogress: CircleDot,
  missing: TriangleAlert,
  future: Circle,
  awaiting: Clock,
};

interface MonitorDetailPanelProps {
  visit: MonitorVisit | null;
  row: MonitorStaffRow | null;
  onSelectVisit: (visitId: string) => void;
  /** 退出忘れしきい値 (分)。モニター応答の thresholds.max_inprogress_min を渡す。 */
  maxInprogressMin?: number;
  /** 確認済みにする (comment 任意)。admin/manager のみ呼ばれる。 */
  onReview?: (visitId: string, comment: string | null) => void;
  /** 確認を取り消す (undo)。 */
  onUnreview?: (visitId: string) => void;
  /** review/undo の実行中フラグ (ボタン無効化用)。 */
  reviewPending?: boolean;
}

export function MonitorDetailPanel({
  visit,
  row,
  onSelectVisit,
  maxInprogressMin,
  onReview,
  onUnreview,
  reviewPending,
}: MonitorDetailPanelProps) {
  if (visit) {
    return (
      <VisitDetail
        visit={visit}
        row={row}
        maxInprogressMin={maxInprogressMin}
        onReview={onReview}
        onUnreview={onUnreview}
        reviewPending={reviewPending}
      />
    );
  }
  if (row) {
    return <CourseDetail row={row} onSelectVisit={onSelectVisit} />;
  }
  return (
    <div
      className="flex min-h-[240px] items-center justify-center px-6 py-8 text-center text-[13px] text-text-muted"
      data-testid="monitor-detail-empty"
    >
      行（コース）をクリックで地図に目的地を表示。
      <br />
      各訪問バー／上部の要対応カードをクリックで詳細。
    </div>
  );
}

function CourseDetail({
  row,
  onSelectVisit,
}: {
  row: MonitorStaffRow;
  onSelectVisit: (visitId: string) => void;
}) {
  return (
    <div className="p-4" data-testid="monitor-detail-course">
      <h3 className="m-0 text-[15px] font-bold text-text-primary">
        {row.staff_name ?? '（担当未設定）'}
      </h3>
      <div className="mb-3.5 text-xs text-text-secondary">
        {[row.office_name, row.course_label].filter(Boolean).join(' ')} ／ 訪問 {row.visits.length}
        件
      </div>
      <ul className="m-0 list-none p-0">
        {row.visits.map((v, i) => {
          const st = displayStatus(v);
          return (
            <li key={v.visit_id}>
              <button
                type="button"
                onClick={() => onSelectVisit(v.visit_id)}
                className="flex w-full items-center gap-2 rounded-lg border-b border-border-default/40 px-1.5 py-2 text-left hover:bg-bg-muted"
              >
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: STATUS_COLOR[st] }}
                />
                <span className="w-10 shrink-0 text-xs tabular-nums text-text-secondary">
                  {v.start_time}
                </span>
                <span className="flex-1 text-[13px] font-semibold text-text-primary">
                  {i + 1}. {v.patient_name ?? '—'}
                </span>
                <span className="text-[11px]" style={{ color: STATUS_COLOR[st] }}>
                  {STATUS_LABEL[st]}
                </span>
              </button>
              {v.distance_to_next_m != null && (
                <div className="pb-1.5 pl-[60px] text-[11px] text-text-muted">
                  ↓ 次まで {formatDistance(v.distance_to_next_m)}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function VisitDetail({
  visit,
  row,
  maxInprogressMin,
  onReview,
  onUnreview,
  reviewPending,
}: {
  visit: MonitorVisit;
  row: MonitorStaffRow | null;
  maxInprogressMin?: number;
  onReview?: (visitId: string, comment: string | null) => void;
  onUnreview?: (visitId: string) => void;
  reviewPending?: boolean;
}) {
  const st = displayStatus(visit);
  const txt = STATUS_JUDGE[st];
  const JudgeIcon = STATUS_ICON[st];
  const arrive = isoToHm(visit.arrival?.scanned_at);
  const depart =
    visit.departure?.scanned_at != null
      ? isoToHm(visit.departure.scanned_at)
      : visit.phase === 'inprogress'
        ? '（滞在中）'
        : '—';
  const stay = visit.stay_minutes != null ? `${visit.stay_minutes}分` : '—';
  const delay =
    visit.arrival_delay_min != null
      ? `${visit.arrival_delay_min >= 0 ? '+' : ''}${visit.arrival_delay_min}分`
      : '—';
  const dist = visit.arrival?.distance_m;
  const acc = visit.arrival?.accuracy_m;
  const distColor =
    st === 'mismatch' ? 'var(--error)' : st === 'review' ? 'var(--warning)' : 'var(--brand-primary-hover)';

  const judgeClass: Record<string, string> = {
    match: 'bg-brand-primary-50 text-brand-primary-hover',
    review: 'bg-warning-bg text-warning-strong',
    mismatch: 'bg-error-bg text-error',
    inprogress: 'bg-brand-primary-50 text-brand-primary-hover',
    missing: 'bg-error-bg text-error',
    future: 'bg-bg-muted text-text-secondary',
    awaiting: 'bg-bg-muted text-text-secondary',
  };

  return (
    <div className="p-4" data-testid="monitor-detail-visit">
      <h3 className="m-0 flex items-center gap-2 text-[15px] font-bold text-text-primary">
        {visit.patient_name ?? '—'} さん
        {visit.visit_group_id != null && (
          <span className="rounded-full bg-c-coupled-bg px-2 py-0.5 text-[11px] font-bold text-c-coupled">
            2名体制
          </span>
        )}
      </h3>
      <div className="mb-3 text-xs text-text-secondary">
        {[row?.staff_name, row?.office_name, row?.course_label].filter(Boolean).join(' ／ ')}
      </div>

      <div
        className={cn(
          'mb-3 flex items-center gap-2.5 rounded px-3 py-2.5 text-[13px] font-semibold',
          judgeClass[st],
        )}
      >
        <JudgeIcon className="h-[18px] w-[18px] shrink-0" /> {txt}
        {dist != null && `（${Math.round(dist)}m）`}
      </div>

      {(onReview || onUnreview) && (
        <ReviewSection
          visit={visit}
          onReview={onReview}
          onUnreview={onUnreview}
          reviewPending={reviewPending}
        />
      )}

      {isLongInprogress(visit, maxInprogressMin) && (
        <div
          className="mb-3 rounded border border-border-warning bg-warning-bg p-3 text-[13px] leading-relaxed text-warning-strong"
          data-testid="monitor-long-inprogress"
        >
          <span className="mb-1 flex items-center gap-1 text-[11px] font-bold">
            <Timer className="h-3.5 w-3.5" />
            要確認
          </span>
          {LONG_INPROGRESS_REASON}（滞在 {visit.stay_minutes}分）
        </div>
      )}

      {visit.phase === 'missing' && (
        <div
          className="mb-3 rounded-md border border-border-error bg-error-bg p-3"
          data-testid="monitor-callbox"
        >
          <div className="mb-1.5 flex items-center gap-1 text-[13px] font-bold text-error">
            <OctagonAlert className="h-4 w-4" />
            未訪問 — 即対応
          </div>
          <div className="mb-2 text-xs text-text-secondary">
            予定 {visit.start_time}–{visit.end_time} を過ぎても到着スキャンがありません。
          </div>
          <div className="text-xs text-text-secondary">
            {visit.reason ? `理由: ${visit.reason}` : '理由未入力 — スタッフへ確認してください。'}
          </div>
        </div>
      )}

      {visit.phase !== 'missing' && visit.reason && (
        <div
          className={cn(
            'mb-3 rounded border p-3 text-[13px] leading-relaxed',
            st === 'mismatch' ? 'border-border-error bg-error-bg' : 'border-border-warning bg-warning-bg',
          )}
        >
          <span className="mb-1 block text-[11px] font-bold text-text-secondary">
            {st === 'mismatch' ? '場所違いの理由' : '理由'}（スタッフ入力）
          </span>
          {visit.reason}
        </div>
      )}

      {visit.phase !== 'missing' && (
        <div className="mb-3 rounded-md border border-border-default bg-bg-base px-3.5 py-1.5">
          <Kv k="予定時刻" v={`${visit.start_time} – ${visit.end_time}`} />
          <Kv k="到着（QR/GPS）" v={arrive} />
          <Kv k="退出（QR/GPS）" v={depart} />
          <Kv k="滞在時間" v={stay} />
          <Kv k="到着ズレ" v={delay} />
          {dist != null && (
            <Kv k="登録住所からの距離" v={`${Math.round(dist)}m`} vColor={distColor} />
          )}
          {acc != null && <Kv k="GPS精度" v={`±${Math.round(acc)}m`} />}
          {visit.distance_to_next_m != null && (
            <Kv k="次の訪問まで" v={formatDistance(visit.distance_to_next_m)} />
          )}
        </div>
      )}
    </div>
  );
}

/**
 * 「確認済みにする」セクション (Phase 5-3)。
 *
 * - 未確認: 「✓ 確認済みにする」ボタン → クリックで理由 (任意) 入力フォームを開き、
 *   確定で review を送る。
 * - 確認済み: 確認者 / 日時 / 理由を表示し「確認を取り消す (undo)」ボタンを出す。
 */
function ReviewSection({
  visit,
  onReview,
  onUnreview,
  reviewPending,
}: {
  visit: MonitorVisit;
  onReview?: (visitId: string, comment: string | null) => void;
  onUnreview?: (visitId: string) => void;
  reviewPending?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [comment, setComment] = useState('');

  if (visit.reviewed) {
    return (
      <div
        className="mb-3 rounded border border-brand-primary-light bg-brand-primary-50 p-3"
        data-testid="monitor-review-done"
      >
        <div className="mb-1 flex items-center gap-1.5 text-[13px] font-bold text-brand-primary-hover">
          <Check className="h-3.5 w-3.5" />
          確認済み
        </div>
        <div className="text-xs text-text-secondary">
          {visit.reviewed_by_name ?? '—'}
          {visit.reviewed_at != null && ` ／ ${isoToYmdHm(visit.reviewed_at)}`}
        </div>
        {visit.review_comment && (
          <div className="mt-1.5 whitespace-pre-wrap text-xs text-text-primary">
            {visit.review_comment}
          </div>
        )}
        {onUnreview && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            data-testid="monitor-review-undo"
            disabled={reviewPending}
            onClick={() => onUnreview(visit.visit_id)}
            className="mt-2 h-7 px-2.5 text-xs font-semibold text-text-secondary"
          >
            確認を取り消す
          </Button>
        )}
      </div>
    );
  }

  if (!onReview) return null;

  if (!open) {
    return (
      <Button
        type="button"
        variant="outline"
        data-testid="monitor-review-button"
        onClick={() => setOpen(true)}
        className="mb-3 h-auto w-full gap-1.5 rounded border-brand-primary-light bg-brand-primary-50 px-3 py-2 text-[13px] font-bold text-brand-primary-hover hover:bg-brand-primary-light hover:text-brand-primary-hover"
      >
        <Check className="h-3.5 w-3.5" />
        確認済みにする
      </Button>
    );
  }

  return (
    <div
      className="mb-3 rounded border border-brand-primary-light bg-brand-primary-50 p-3"
      data-testid="monitor-review-form"
    >
      <label className="mb-1 block text-[11px] font-bold text-text-secondary">
        確認理由（任意）
      </label>
      <textarea
        data-testid="monitor-review-comment"
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        rows={2}
        placeholder="例: 電話で在宅を確認済み"
        className="mb-2 w-full resize-none rounded-md border border-border-default bg-bg-base px-2 py-1.5 text-xs"
      />
      <div className="flex gap-2">
        <Button
          type="button"
          variant="default"
          size="sm"
          data-testid="monitor-review-submit"
          disabled={reviewPending}
          onClick={() => {
            onReview(visit.visit_id, comment.trim() || null);
            setOpen(false);
            setComment('');
          }}
          className="h-7 px-3 text-xs font-bold"
        >
          確定
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => {
            setOpen(false);
            setComment('');
          }}
          className="h-7 px-3 text-xs font-semibold text-text-secondary"
        >
          キャンセル
        </Button>
      </div>
    </div>
  );
}

function Kv({ k, v, vColor }: { k: string; v: string; vColor?: string }) {
  return (
    <div className="flex justify-between border-b border-border-default/40 py-2 text-[13px] last:border-b-0">
      <span className="text-text-secondary">{k}</span>
      <span className="font-semibold tabular-nums" style={vColor ? { color: vColor } : undefined}>
        {v}
      </span>
    </div>
  );
}
