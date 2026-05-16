'use client';

/**
 * VisitArrow — W41 v2 (訪問間距離, 行内右端版).
 *
 * 同コース内の patient 行の **右端** に「横向き湾曲矢印 + 距離 (km)」を
 * インラインで描画する SVG コンポーネント. 行と行の間にスペースを挟まず、
 * 既存の縦に詰まったレイアウトを維持したまま「次の患者までの距離」を示す.
 *
 * distance が null/undefined の場合 (= コース最後尾の visit) はレンダリング
 * しない. SVG marker は ``React.useId()`` で一意化し、同一ページ上で複数
 * インスタンスが共存しても矢印先端が消えない (Firefox 対応).
 */
import * as React from 'react';

export interface VisitArrowProps {
  /** 次の patient までの直線距離 (km). null なら描画しない (= 最後の visit). */
  distanceKm: number | null | undefined;
}

export function VisitArrow({ distanceKm }: VisitArrowProps) {
  const reactId = React.useId();
  if (distanceKm === null || distanceKm === undefined) return null;
  const km = Math.round(distanceKm * 10) / 10;
  const markerId = `visit-arrow-h-${reactId.replace(/:/g, '')}`;
  return (
    <span
      className="ml-auto inline-flex flex-shrink-0 items-center gap-0.5 pl-1 text-[9px] text-text-secondary"
      data-testid="visit-arrow"
      aria-label={`次の患者まで ${km}km`}
    >
      <svg width="26" height="14" viewBox="0 0 26 14" className="flex-shrink-0" aria-hidden="true">
        <defs>
          <marker
            id={markerId}
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerUnits="strokeWidth"
            markerWidth="4"
            markerHeight="4"
            orient="auto"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
          </marker>
        </defs>
        <path
          d="M 1 12 C 6 2, 16 2, 23 7"
          stroke="#94a3b8"
          strokeWidth="1.2"
          fill="none"
          markerEnd={`url(#${markerId})`}
        />
      </svg>
      <span className="tnum whitespace-nowrap">{km}km</span>
    </span>
  );
}
