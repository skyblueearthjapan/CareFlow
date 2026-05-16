'use client';

/**
 * VisitArrow — W41 v2 (訪問間距離, 行内右端版, 距離テキストのみ).
 *
 * 同コース内の patient 行の **右端** に「次の patient までの距離 (km)」を
 * インライン表示する. 矢印 SVG はユーザー希望により廃止し、距離テキストのみ
 * を表示する (向きが揺れる問題を回避).
 *
 * distance が null/undefined の場合 (= コース最後尾の visit) はレンダリング
 * しない.
 */
import * as React from 'react';

export interface VisitArrowProps {
  /** 次の patient までの直線距離 (km). null なら描画しない (= 最後の visit). */
  distanceKm: number | null | undefined;
}

export function VisitArrow({ distanceKm }: VisitArrowProps) {
  if (distanceKm === null || distanceKm === undefined) return null;
  const km = Math.round(distanceKm * 10) / 10;
  return (
    <span
      className="tnum ml-auto inline-flex flex-shrink-0 items-center whitespace-nowrap pl-1 text-[9px] text-text-secondary"
      data-testid="visit-distance"
      aria-label={`次の患者まで ${km}km`}
    >
      {km}km
    </span>
  );
}
