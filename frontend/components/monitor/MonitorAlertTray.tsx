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
}

function alertTag(v: MonitorVisit): string {
  if (v.alert_level === 'missing') return '未訪問';
  if (v.alert_level === 'mismatch') {
    const d = v.arrival?.distance_m;
    return d != null ? `場所違い ${Math.round(d)}m` : '場所違い';
  }
  return '要確認';
}

function alertReason(v: MonitorVisit): string {
  if (v.reason) return v.reason;
  if (isLongInprogress(v)) return LONG_INPROGRESS_REASON;
  return v.alert_level === 'missing' ? '理由未入力（要確認）' : '理由なし';
}

const TAG_CLASS: Record<string, string> = {
  missing: 'bg-red-600',
  mismatch: 'bg-amber-600',
  review: 'bg-yellow-600',
};

export function MonitorAlertTray({ rows, selectedVisitId, onSelectVisit }: MonitorAlertTrayProps) {
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
      <div className="px-5 py-2.5 text-xs text-text-muted" data-testid="monitor-alert-tray">
        ✓ 要対応の異常はありません
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
          'flex items-center gap-2 rounded-[10px] border px-2.5 py-1.5 cursor-pointer',
          inPop ? 'w-full border-transparent border-t-border-default/50' : 'flex-none',
          v.alert_level === 'missing' && 'border-red-300 bg-red-50',
          v.alert_level === 'mismatch' && 'border-amber-300 bg-amber-50',
          v.alert_level === 'review' && 'border-yellow-300 bg-yellow-50',
          sel && 'outline outline-2 outline-text-primary',
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
        <span className="whitespace-nowrap text-[12.5px] font-bold text-text-primary">
          {v.patient_name ?? '—'}
        </span>
        {isPair && (
          <span className="whitespace-nowrap rounded-full bg-indigo-100 px-1.5 py-0.5 text-[9.5px] font-bold text-indigo-700">
            2名
          </span>
        )}
        <span className="whitespace-nowrap text-[10.5px] text-text-muted">
          {staffNames.join('・')}
        </span>
        <span
          className={cn(
            'overflow-hidden text-ellipsis whitespace-nowrap text-[11px] text-text-secondary',
            inPop ? 'flex-1' : 'max-w-[160px]',
          )}
          title={alertReason(v)}
        >
          📝 {alertReason(v)}
        </span>
        {v.alert_level === 'missing' && (
          <button
            type="button"
            data-testid={`monitor-alert-contact-${v.visit_id}`}
            onClick={(e) => {
              e.stopPropagation();
              setPopOpen(false);
              onSelectVisit(v.visit_id);
            }}
            className="whitespace-nowrap rounded-md bg-red-600 px-2 py-1 text-[11px] font-bold text-white"
          >
            📞連絡
          </button>
        )}
      </div>
    );
  };

  return (
    <div className="relative" data-testid="monitor-alert-tray">
      <div className="flex items-center gap-2 overflow-x-auto px-5 py-2">
        <span
          className="flex-none whitespace-nowrap text-[11.5px] font-bold text-red-600"
          title="未訪問→場所違い→要確認の優先順"
        >
          ⚠ 要対応 {alerts.length}件
        </span>
        {alerts.length > 3 && (
          <button
            type="button"
            data-testid="monitor-alert-more"
            onClick={() => setPopOpen((o) => !o)}
            className={cn(
              'flex-none whitespace-nowrap rounded-full px-3 py-1 text-[11.5px] font-bold',
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
            <span>⚠ 要対応 {alerts.length}件（未訪問→場所違い→要確認）</span>
            <button
              type="button"
              onClick={() => setPopOpen(false)}
              className="px-1 text-text-muted"
              aria-label="閉じる"
            >
              ✕
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
