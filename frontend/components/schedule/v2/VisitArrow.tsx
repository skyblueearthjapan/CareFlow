'use client';

/**
 * VisitArrow — 訪問間距離 (行内右端版, 距離テキストのみ).
 *
 * patient 行の **右端** に移動距離 (km) をインライン表示する. 距離の意味は呼び出し
 * 側の distanceMode に依存する:
 *   - 'to_next' (提案ダイアログ等): 次の patient までの距離.
 *   - 'to_reach' (スケジュール日リスト): ここに来るまでの距離 (前の patient / 拠点から).
 * いずれも「移動距離」なので aria-label は中立表現にしている.
 *
 * distance が null/undefined の場合はレンダリングしない.
 */
import * as React from 'react';

export interface VisitArrowProps {
  /** 移動距離 (km). null なら描画しない. */
  distanceKm: number | null | undefined;
}

export function VisitArrow({ distanceKm }: VisitArrowProps) {
  if (distanceKm === null || distanceKm === undefined) return null;
  const km = Math.round(distanceKm * 10) / 10;
  return (
    <span
      className="tnum ml-auto inline-flex flex-shrink-0 items-center whitespace-nowrap pl-1 text-[9px] text-text-secondary"
      data-testid="visit-distance"
      aria-label={`移動 ${km}km`}
    >
      {km}km
    </span>
  );
}
