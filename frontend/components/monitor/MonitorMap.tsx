'use client';

/**
 * 地図ラッパ — Leaflet は SSR 不可 (``window`` 依存) のため、実装本体
 * (``MonitorMapClient``) を ``dynamic(import, { ssr:false })`` で遅延読み込みする。
 * これにより Next.js のサーバレンダリングで ``window is not defined`` を出さない。
 */
import dynamic from 'next/dynamic';

import type { MonitorStaffRow, NearbyPatient } from '@/lib/schemas/monitor';

const MonitorMapClient = dynamic(() => import('./MonitorMapClient'), {
  ssr: false,
  loading: () => (
    <div className="flex h-[280px] items-center justify-center bg-bg-muted text-xs text-text-muted">
      地図を読み込み中…
    </div>
  ),
});

interface MonitorMapProps {
  row: MonitorStaffRow | null;
  selectedVisitId: string | null;
  matchM: number;
  nearby: NearbyPatient[];
}

export function MonitorMap(props: MonitorMapProps) {
  return (
    <div className="h-[280px] w-full" data-testid="monitor-map">
      {props.row ? (
        <MonitorMapClient {...props} />
      ) : (
        <div className="flex h-full items-center justify-center bg-bg-muted text-xs text-text-muted">
          行（コース）を選択すると地図を表示します。
        </div>
      )}
    </div>
  );
}
