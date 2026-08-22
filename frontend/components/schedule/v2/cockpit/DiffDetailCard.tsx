'use client';

/**
 * DiffDetailCard — 選択中の差分「何から何へ」表 (週空間 Phase E・運転席)。
 *
 * モックの `renderDetail()` を部品化。列は常に **らく助(今) ↔ カイポケ** で、
 * 変わった項目だけ「打消 → 強調」。新規は右だけ、取消は左だけに値が出る。
 *
 * シートの before/after は方向で意味が入れ替わるため `direction` で読み替える:
 *   inbound  (🔄突合の⇩取込候補) … before=らく助 / after=カイポケ
 *   outbound (●未送信)          … before=カイポケ / after=らく助
 */
import * as React from 'react';

import { cn } from '@/lib/utils';
import {
  fmtMd,
  type CockpitMarker,
  type CockpitMarkerSide,
  type DiffDirection,
} from './reconcileMarkers';

const KIND_JA: Record<CockpitMarker['action'], string> = {
  add: '新規',
  update: '変更',
  delete: '取消',
};

const KIND_CLS: Record<CockpitMarker['action'], string> = {
  add: 'bg-brand-primary-50 text-brand-primary-hover',
  update: 'bg-warning-bg text-warning-strong',
  delete: 'bg-info-bg text-info-strong',
};

const HEAD_JA: Record<DiffDirection, Record<CockpitMarker['action'], string>> = {
  inbound: {
    add: 'カイポケにだけある予定 → 取り込むとらく助に入ります',
    update: 'カイポケ側で変わっている → 取込=らく助を合わせる / 上書き=らく助を正にする',
    delete: 'カイポケで消えている → 取込=らく助からも取消 / 上書き=カイポケに再登録',
  },
  outbound: {
    add: 'らく助にだけある予定 → 送信するとカイポケに入ります',
    update: 'らく助で変えました → 送信するとカイポケが揃います',
    delete: 'らく助で取消しました → 送信するとカイポケからも消えます',
  },
};

export interface DiffDetailCardProps {
  marker: CockpitMarker;
  direction: DiffDirection;
  /** 担当 ID → 表示名 (CSV 氏名しか無い場合は marker 側の staff_name を使う)。 */
  staffNameById?: Map<string, string>;
  className?: string;
}

interface Row {
  label: string;
  left: string;
  right: string;
  changed: boolean;
}

function sideStaffName(
  side: CockpitMarkerSide | undefined,
  staffNameById?: Map<string, string>,
): string {
  if (!side) return '';
  if (side.staff_id && staffNameById?.has(side.staff_id)) {
    return staffNameById.get(side.staff_id)!;
  }
  return side.staff_name ?? '';
}

export function DiffDetailCard({
  marker,
  direction,
  staffNameById,
  className,
}: DiffDetailCardProps) {
  const rakusuke = direction === 'inbound' ? marker.before : marker.after;
  const kaipoke = direction === 'inbound' ? marker.after : marker.before;
  const both = rakusuke != null && kaipoke != null;

  const rows: Row[] = React.useMemo(() => {
    const build = (label: string, l: string, r: string): Row => ({
      label,
      left: l,
      right: r,
      changed: both ? l !== r : false,
    });
    const date = (s?: CockpitMarkerSide) => (s ? fmtMd(s.date) : '');
    const time = (s?: CockpitMarkerSide) => (s ? `${s.start}〜${s.end}` : '');
    const out: Row[] = [
      build('日付', date(rakusuke), date(kaipoke)),
      build('時刻', time(rakusuke), time(kaipoke)),
      build('担当', sideStaffName(rakusuke, staffNameById), sideStaffName(kaipoke, staffNameById)),
    ];
    if (marker.kind === 'visit') {
      // 側ごとの course_label で比べる (両側に marker のものを入れると
      // コースが変わっても常に「＝」になってしまう)。
      const course = (sd?: CockpitMarkerSide) => sd?.course_label ?? '';
      out.push(build('コース', course(rakusuke), course(kaipoke)));
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [marker, rakusuke, kaipoke, both, staffNameById]);

  const cell = (v: string, missing: boolean) =>
    missing ? (
      <span className="text-text-muted">（無い）</span>
    ) : v ? (
      <span className="tnum">{v}</span>
    ) : (
      <span className="text-text-muted">—</span>
    );

  return (
    <div
      className={cn('rounded border border-border-subtle bg-bg-base px-3 py-2', className)}
      data-testid="diff-detail-card"
    >
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span
          className={cn('rounded px-1.5 py-0.5 text-[11px] font-bold', KIND_CLS[marker.action])}
          data-testid="diff-detail-kind"
        >
          {KIND_JA[marker.action]}
        </span>
        <b className="text-[12px] text-text-primary">
          {marker.kind === 'visit' ? '訪問' : 'イベント'}｜{marker.patient_name ?? marker.title}
          {marker.kind === 'event' && marker.title ? `・${marker.title}` : ''}
        </b>
        <span className="text-[11px] text-text-secondary" data-testid="diff-detail-head">
          {HEAD_JA[direction][marker.action]}
        </span>
      </div>

      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-text-muted">
            <th className="w-12 text-left font-normal" />
            <th className="text-left font-normal">らく助(今)</th>
            <th className="w-6 font-normal" />
            <th className="text-left font-normal">カイポケ</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} className={r.changed ? 'font-bold' : ''}>
              <td className="text-text-muted">{r.label}</td>
              <td className={r.changed ? 'text-text-muted line-through' : 'text-text-primary'}>
                {cell(r.left, rakusuke == null)}
              </td>
              <td className="text-center text-text-muted">{r.changed ? '→' : both ? '＝' : ''}</td>
              <td className={r.changed ? 'text-brand-primary-hover' : 'text-text-primary'}>
                {cell(r.right, kaipoke == null)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
