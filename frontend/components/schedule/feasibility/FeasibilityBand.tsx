'use client';

/**
 * 実現性の帯 (赤 N・黄 N・「この週に確かめたい予定があります」) と一覧。
 *
 * 設計 = docs/plans/schedule-feasibility-alerts-design-2026-11-01.md
 * PO 決定 (2026-11-01 夕) の「案 1」: スケジュールは週の上・訪問モニターはその日の上。
 * 一覧を開いて行を押すと、そのカードへ移って枠で知らせる。
 * データは `FeasibilityMarksProvider` から読む (管理者以外・Provider の外では出ない)。
 */

import { useState } from 'react';
import { toast } from 'sonner';

import { cn } from '@/lib/utils';
import type { FeasibilityAlertItem } from '@/lib/queries/feasibility';

import { FeasibilityTierIcon, TIER_LABEL, useFeasibilityMarksData } from './FeasibilityMark';

const WD = ['日', '月', '火', '水', '木', '金', '土'];

/** YYYY-MM-DD → 「11/4（火）」。 */
export function feasibilityDayLabel(ymd: string): string {
  const [y, m, d] = ymd.split('-').map(Number);
  if (!y || !m || !d) return ymd;
  const wd = WD[new Date(Date.UTC(y, m - 1, d)).getUTCDay()] ?? '';
  return `${m}/${d}（${wd}）`;
}

/**
 * 指摘の一覧 (帯と取り込み結果で共通)。`onSelect` があれば訪問の行を押せる。
 * イベントの指摘 (訪問 ID が無い) は移る先のカードが無いので、押せない行にする。
 */
export function FeasibilityAlertList({
  items,
  onSelect,
  showDay = true,
  testIdPrefix = 'feas-item',
}: {
  items: FeasibilityAlertItem[];
  onSelect?: (item: FeasibilityAlertItem) => void;
  showDay?: boolean;
  testIdPrefix?: string;
}) {
  return (
    <ul className="divide-y divide-border-subtle" data-testid={`${testIdPrefix}-list`}>
      {items.map((it, i) => {
        const body = (
          <>
            <FeasibilityTierIcon tier={it.tier} className="mt-0.5" />
            <span className="min-w-0 flex-1">
              <span className="tnum mr-1.5 font-bold text-text-primary">
                {showDay ? `${feasibilityDayLabel(it.day)} ` : ''}
                {it.at}
              </span>
              <span className="mr-1.5 text-text-secondary">{it.staff}</span>
              <span className="mr-1.5 font-bold text-text-primary">
                {it.visitIds.length > 0 ? `${it.patientName}様` : it.patientName}
              </span>
              <span className="text-text-secondary">
                {TIER_LABEL[it.tier]}（{it.kind}）：{it.reason}
              </span>
            </span>
          </>
        );
        const key = `${it.day}-${it.at}-${it.kind}-${i}`;
        return (
          <li key={key}>
            {onSelect && it.visitIds.length > 0 ? (
              <button
                type="button"
                onClick={() => onSelect(it)}
                data-testid={`${testIdPrefix}-${i}`}
                className="flex w-full items-start gap-1.5 px-2 py-1.5 text-left text-xs hover:bg-bg-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
              >
                {body}
              </button>
            ) : (
              <div
                className="flex items-start gap-1.5 px-2 py-1.5 text-xs"
                data-testid={`${testIdPrefix}-${i}`}
              >
                {body}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** 画面の中から訪問のカードを探して、見える所へ移し、少しの間だけ枠で知らせる。 */
export function focusVisitCard(visitIds: string[], testIdPrefixes: string[]): boolean {
  if (typeof document === 'undefined') return false;
  for (const id of visitIds) {
    for (const prefix of testIdPrefixes) {
      const el = document.querySelector<HTMLElement>(`[data-testid="${prefix}${id}"]`);
      if (!el) continue;
      el.scrollIntoView?.({ block: 'center', inline: 'center', behavior: 'smooth' });
      el.focus?.({ preventScroll: true });
      el.classList.add('ring-2', 'ring-error', 'ring-offset-1');
      window.setTimeout(() => el.classList.remove('ring-2', 'ring-error', 'ring-offset-1'), 2400);
      return true;
    }
  }
  return false;
}

export interface FeasibilityBandProps {
  /** 「この週」「この日」 */
  scopeLabel: string;
  /** 行を押したときに探すカードの data-testid の頭 (例: 'wtl-visit-')。 */
  cardTestIdPrefixes: string[];
  /** カードが画面に無かったときの案内 (既定はトースト)。 */
  notFoundMessage?: string;
  /** 一覧で日付を出すか (1 日の画面では出さない)。 */
  showDay?: boolean;
  className?: string;
}

export function FeasibilityBand({
  scopeLabel,
  cardTestIdPrefixes,
  notFoundMessage = 'このカードは今の表示にありません。表示を切り替えてください',
  showDay = true,
  className,
}: FeasibilityBandProps) {
  const data = useFeasibilityMarksData();
  const [open, setOpen] = useState(false);
  if (!data) return null;
  const { hardCount, softCount, items } = data;
  if (hardCount + softCount === 0) return null;
  const onSelect = (it: FeasibilityAlertItem) => {
    if (!focusVisitCard(it.visitIds, cardTestIdPrefixes)) toast.info(notFoundMessage);
  };
  return (
    <div
      className={cn(
        'rounded-md border text-xs',
        hardCount > 0 ? 'border-error/40 bg-error-bg' : 'border-warning/40 bg-warning-bg',
        className,
      )}
      data-testid="feasibility-band"
      role="region"
      aria-label={`${scopeLabel}に確かめたい予定`}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        data-testid="feasibility-band-toggle"
        className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
      >
        {hardCount > 0 ? (
          <span className="inline-flex items-center gap-1 font-bold text-error">
            <FeasibilityTierIcon tier="hard" />
            無理 <span data-testid="feasibility-band-hard">{hardCount}</span>
          </span>
        ) : null}
        {softCount > 0 ? (
          <span className="inline-flex items-center gap-1 font-bold text-warning-strong">
            <FeasibilityTierIcon tier="soft" />
            注意 <span data-testid="feasibility-band-soft">{softCount}</span>
          </span>
        ) : null}
        <span className="text-text-primary">{scopeLabel}に確かめたい予定があります</span>
        <span className="ml-auto text-text-secondary">
          {open ? '一覧を閉じる ▲' : '一覧を開く ▼'}
        </span>
      </button>
      {open ? (
        <div className="max-h-64 overflow-auto border-t border-border-subtle bg-bg-base">
          <FeasibilityAlertList
            items={items}
            onSelect={onSelect}
            showDay={showDay}
            testIdPrefix="feasibility-band-item"
          />
        </div>
      ) : null}
    </div>
  );
}
