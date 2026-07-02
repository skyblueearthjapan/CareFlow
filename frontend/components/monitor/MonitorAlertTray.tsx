'use client';

/**
 * 要対応アラートトレイ — 未訪問 → 場所違い → 要確認 の優先順で横並び表示。
 *
 * 各カードは理由を 1 行表示し、未訪問は「📞連絡」ボタンを出す。4 件以上は
 * 「全件 ▾」で縦一覧ドロップダウンを開ける。クリックで該当 visit を選択。
 *
 * 注: 患者/スタッフの電話番号は現状 DB に無いため、「📞連絡」は該当 visit を選択
 * して詳細パネルの即連絡ボックスへ誘導する (tel: リンクは番号が入り次第有効化)。
 */
import { useState } from 'react';
import { Check, FileText, Phone, TriangleAlert, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { MonitorStaffRow, MonitorVisit } from '@/lib/schemas/monitor';

import {
  ALERT_RANK,
  LONG_INPROGRESS_REASON,
  groupVisits,
  hmToMinutes,
  isLongInprogress,
} from './constants';

interface AlertEntry {
  visit: MonitorVisit;
  staffNames: string[];
  isPair: boolean;
}

interface MonitorAlertTrayProps {
  rows: MonitorStaffRow[];
  selectedVisitId: string | null;
  onSelectVisit: (visitId: string) => void;
  /** 退出忘れしきい値 (分)。モニター応答の thresholds.max_inprogress_min を渡す。 */
  maxInprogressMin?: number;
}

function alertTag(v: MonitorVisit): string {
  if (v.alert_level === 'missing') return '未訪問';
  if (v.alert_level === 'mismatch') {
    const d = v.arrival?.distance_m;
    return d != null ? `場所違い ${Math.round(d)}m` : '場所違い';
  }
  return '要確認';
}

function alertReason(v: MonitorVisit, maxInprogressMin?: number): string {
  if (v.reason) return v.reason;
  if (isLongInprogress(v, maxInprogressMin)) return LONG_INPROGRESS_REASON;
  return v.alert_level === 'missing' ? '理由未入力（要確認）' : '理由なし';
}

const TAG_CLASS: Record<string, string> = {
  missing: 'bg-error',
  mismatch: 'bg-warning',
  review: 'bg-warning',
};

export function MonitorAlertTray({
  rows,
  selectedVisitId,
  onSelectVisit,
  maxInprogressMin,
}: MonitorAlertTrayProps) {
  const [popOpen, setPopOpen] = useState(false);

  // 2 名体制 (visit_group_id) は 1 枚に集約。worst(alert_level) の visit を代表に、
  // 関与スタッフ名 (複数) をまとめて表示する。null グループは visit.id 単位。
  const alerts: AlertEntry[] = groupVisits(rows)
    .filter(
      (g) =>
        g.worstAlertLevel === 'missing' ||
        g.worstAlertLevel === 'mismatch' ||
        g.worstAlertLevel === 'review',
    )
    .map((g) => ({ visit: g.representative, staffNames: g.staffNames, isPair: g.isPair }));
  alerts.sort(
    (a, b) =>
      (ALERT_RANK[a.visit.alert_level] ?? 9) - (ALERT_RANK[b.visit.alert_level] ?? 9) ||
      hmToMinutes(a.visit.start_time) - hmToMinutes(b.visit.start_time),
  );

  if (alerts.length === 0) {
    return (
      <div
        className="flex items-center gap-1 px-5 py-2.5 text-xs text-text-muted"
        data-testid="monitor-alert-tray"
      >
        <Check className="h-3.5 w-3.5" />
        要対応の異常はありません
      </div>
    );
  }

  const renderCard = (entry: AlertEntry, inPop: boolean) => {
    const { visit: v, staffNames, isPair } = entry;
    const sel = v.visit_id === selectedVisitId;
    return (
      <div
        key={v.visit_id}
        role="button"
        tabIndex={0}
        data-testid={`monitor-alert-${v.visit_id}`}
        onClick={() => {
          setPopOpen(false);
          onSelectVisit(v.visit_id);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            setPopOpen(false);
            onSelectVisit(v.visit_id);
          }
        }}
        className={cn(
          'flex items-center gap-2 rounded border px-2.5 py-1.5 cursor-pointer',
          inPop ? 'w-full border-transparent border-t-border-default/50' : 'flex-none',
          v.alert_level === 'missing' && 'border-border-error bg-error-bg',
          (v.alert_level === 'mismatch' || v.alert_level === 'review') &&
            'border-border-warning bg-warning-bg',
          sel && 'outline outline-2 outline-brand-primary',
        )}
      >
        <span
          className={cn(
            'whitespace-nowrap rounded-full px-1.5 py-0.5 text-[10px] font-bold text-white',
            TAG_CLASS[v.alert_level],
          )}
        >
          {alertTag(v)}
        </span>
        <span className="whitespace-nowrap text-[13px] font-bold text-text-primary">
          {v.patient_name ?? '—'}
        </span>
        {isPair && (
          <span className="whitespace-nowrap rounded-full bg-c-coupled-bg px-1.5 py-0.5 text-[10px] font-bold text-c-coupled">
            2名
          </span>
        )}
        <span className="whitespace-nowrap text-[11px] text-text-muted">
          {staffNames.join('・')}
        </span>
        <span
          className={cn(
            'flex items-center gap-1 overflow-hidden whitespace-nowrap text-[11px] text-text-secondary',
            inPop ? 'flex-1' : 'max-w-[160px]',
          )}
          title={alertReason(v, maxInprogressMin)}
        >
          <FileText className="h-3 w-3 shrink-0" />
          <span className="overflow-hidden text-ellipsis">{alertReason(v, maxInprogressMin)}</span>
        </span>
        {v.alert_level === 'missing' && (
          <Button
            type="button"
            variant="destructive"
            data-testid={`monitor-alert-contact-${v.visit_id}`}
            onClick={(e) => {
              e.stopPropagation();
              setPopOpen(false);
              onSelectVisit(v.visit_id);
            }}
            className="h-7 gap-1 px-2 text-[11px] font-bold"
          >
            <Phone className="h-3 w-3" />
            連絡
          </Button>
        )}
      </div>
    );
  };

  return (
    <div className="relative" data-testid="monitor-alert-tray">
      <div className="flex items-center gap-2 overflow-x-auto px-5 py-2">
        <span
          className="flex flex-none items-center gap-1 whitespace-nowrap text-xs font-bold text-error"
          title="未訪問→場所違い→要確認の優先順"
        >
          <TriangleAlert className="h-3.5 w-3.5" />
          要対応 {alerts.length}件
        </span>
        {alerts.length > 3 && (
          <button
            type="button"
            data-testid="monitor-alert-more"
            onClick={() => setPopOpen((o) => !o)}
            className={cn(
              'flex-none whitespace-nowrap rounded-full px-3 py-1 text-xs font-bold',
              popOpen
                ? 'bg-brand-primary text-white'
                : 'bg-brand-primary-light text-brand-primary-hover',
            )}
          >
            全件 {popOpen ? '▴' : '▾'}
          </button>
        )}
        {alerts.map((entry) => renderCard(entry, false))}
      </div>

      {popOpen && (
        <div
          data-testid="monitor-alert-popover"
          className="absolute left-5 right-5 top-[calc(100%-4px)] z-40 max-h-[56vh] overflow-y-auto rounded-xl border border-border-default bg-bg-base p-1.5 shadow-xl"
        >
          <div className="sticky top-0 flex items-center justify-between bg-bg-base p-2 text-xs font-bold text-text-secondary">
            <span className="flex items-center gap-1">
              <TriangleAlert className="h-3.5 w-3.5" />
              要対応 {alerts.length}件（未訪問→場所違い→要確認）
            </span>
            <button
              type="button"
              onClick={() => setPopOpen(false)}
              className="px-1 text-text-muted"
              aria-label="閉じる"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="flex flex-col gap-0.5">
            {alerts.map((entry) => renderCard(entry, true))}
          </div>
        </div>
      )}
    </div>
  );
}
