'use client';

/**
 * VisitArrow — W41 v2 拡張 (訪問間距離).
 *
 * 同コース内で隣り合う 2 つの visit カードの間に「曲線矢印 + 距離 (km)」を
 * 描画する SVG コンポーネント. distance が null の場合 (= コース最後尾の
 * visit) はレンダリングしない.
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
    <div
      className="my-0.5 flex h-7 items-center gap-1 pl-3 text-[10px] text-text-secondary"
      data-testid="visit-arrow"
      aria-hidden={false}
      aria-label={`次の患者まで ${km}km`}
    >
      <svg width="32" height="28" viewBox="0 0 32 28" className="flex-shrink-0">
        <defs>
          <marker
            id="visit-arrow-head"
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerUnits="strokeWidth"
            markerWidth="6"
            markerHeight="6"
            orient="auto"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
          </marker>
        </defs>
        <path
          d="M 8 0 C 24 8, 24 20, 8 28"
          stroke="#94a3b8"
          strokeWidth="1.5"
          fill="none"
          markerEnd="url(#visit-arrow-head)"
        />
      </svg>
      <span className="tnum">{km}km</span>
    </div>
  );
}
