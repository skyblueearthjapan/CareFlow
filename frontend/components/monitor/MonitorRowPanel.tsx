'use client';

/**
 * 行の下に開くパネル — 訪問モニターを職員単位にしたときの地図・順路・詳細の置き場所
 * (monitor-staff-rows-design-2026-09-30.md §4・PO 決定 #2「③ 行の下に開く」)。
 *
 * 左 = 地図 (既存 ``MonitorMap``。その人の 1 日の順路) / 中 = 順路の一覧 /
 * 右 = 選んだ訪問の詳細 (既存 ``MonitorDetailPanel`` の中身。訪問を選んだときだけ)。
 * 狭い画面では 1 列に落ちる。文字は add-visit-anywhere-design §3-5 の基準 (本文 14px・
 * 見出し 16px・注記 12px)。閉じるボタンは 44px 角。
 */
import { X } from 'lucide-react';

import { cn } from '@/lib/utils';
import type { MonitorStaffRow, MonitorVisit, NearbyPatient } from '@/lib/schemas/monitor';

import { MonitorDetailPanel, MonitorRouteList } from './MonitorDetailPanel';
import { MonitorMap } from './MonitorMap';

interface MonitorRowPanelProps {
  row: MonitorStaffRow;
  /** 選択中の訪問 (この行の訪問)。null なら地図と順路だけ。 */
  visit: MonitorVisit | null;
  onSelectVisit: (visitId: string) => void;
  onClose: () => void;
  matchM: number;
  nearby: NearbyPatient[];
  officeIds: readonly string[];
  maxInprogressMin?: number;
  onReview?: (visitId: string, comment: string | null) => void;
  onUnreview?: (visitId: string) => void;
  reviewPending?: boolean;
}

export function MonitorRowPanel({
  row,
  visit,
  onSelectVisit,
  onClose,
  matchM,
  nearby,
  officeIds,
  maxInprogressMin,
  onReview,
  onUnreview,
  reviewPending,
}: MonitorRowPanelProps) {
  const override = row.day_override ?? null;
  return (
    <div className="px-5 pb-4 pt-3" data-testid="monitor-row-panel-body">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="m-0 truncate text-base font-bold text-text-primary">
            {row.staff_name ?? '担当なし'}
          </h3>
          <p className="m-0 text-sm text-text-secondary">
            {[
              row.staff_id == null ? '担当が決まっていない訪問' : (row.office_name ?? '所属なし'),
              `訪問 ${row.visits.length}件`,
              override?.kind === 'off'
                ? `休み${override.reason ? `（${override.reason}）` : ''}`
                : override?.kind === 'custom_time'
                  ? `時間変更 ${override.start_time ?? ''}–${override.end_time ?? ''}`
                  : null,
            ]
              .filter(Boolean)
              .join(' ・ ')}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          data-testid="monitor-row-panel-close"
          aria-label="閉じる"
          className="inline-flex h-11 min-w-11 shrink-0 items-center justify-center gap-1 rounded-md border border-border-strong bg-bg-base px-3 text-sm text-text-secondary hover:bg-bg-muted"
        >
          <X className="h-4 w-4" />
          閉じる
        </button>
      </div>
      <div
        className={cn(
          'grid grid-cols-1 items-start gap-4',
          visit
            ? 'lg:grid-cols-[minmax(300px,400px)_minmax(280px,1fr)_minmax(320px,400px)]'
            : 'lg:grid-cols-[minmax(320px,440px)_minmax(320px,1fr)]',
        )}
      >
        <div className="overflow-hidden rounded-lg border border-border-default bg-bg-base">
          <MonitorMap
            row={row}
            selectedVisitId={visit?.visit_id ?? null}
            matchM={matchM}
            nearby={nearby}
          />
        </div>
        <div className="max-h-[420px] min-w-0 overflow-y-auto">
          <MonitorRouteList
            row={row}
            onSelectVisit={onSelectVisit}
            selectedVisitId={visit?.visit_id ?? null}
            officeIds={officeIds}
          />
        </div>
        {visit && (
          <div
            className="max-h-[420px] min-w-0 overflow-y-auto rounded-lg border border-border-default bg-bg-base"
            data-testid="monitor-row-panel-detail"
          >
            <MonitorDetailPanel
              visit={visit}
              row={row}
              onSelectVisit={onSelectVisit}
              maxInprogressMin={maxInprogressMin}
              onReview={onReview}
              onUnreview={onUnreview}
              reviewPending={reviewPending}
              officeIds={officeIds}
            />
          </div>
        )}
      </div>
    </div>
  );
}
