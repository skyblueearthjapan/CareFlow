/** 行の下のパネルの地図 (MonitorMapClient)。Leaflet は jsdom 非対応のため react-leaflet をモックする。 */
import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const map = { setView: vi.fn(), fitBounds: vi.fn(), invalidateSize: vi.fn() };
vi.mock('leaflet', () => ({ default: { divIcon: () => ({}) } }));
vi.mock('leaflet/dist/leaflet.css', () => ({}));
vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  TileLayer: () => null,
  Polyline: () => <i data-testid="map-line" />,
  Marker: ({ children }: { children?: ReactNode }) => <i data-testid="map-marker">{children}</i>,
  Popup: () => null,
  Circle: () => null,
  useMap: () => map,
}));

import MonitorMapClient from '../MonitorMapClient';
import { makeRow, makeVisit } from './fixtures';

function twoStops() {
  return [
    makeVisit({ patient_lat: 35.6, patient_lng: 140.1, start_time: '09:00' }),
    makeVisit({ patient_lat: 35.61, patient_lng: 140.12, start_time: '10:00' }),
  ];
}

beforeEach(() => {
  map.setView.mockClear();
  map.fitBounds.mockClear();
});

describe('MonitorMapClient', () => {
  it('職員の行は点と順路の線を描く', () => {
    render(
      <MonitorMapClient
        row={makeRow({ staff_id: 'staff-a', visits: twoStops() })}
        selectedVisitId={null}
        matchM={100}
        nearby={[]}
      />,
    );
    expect(screen.getAllByTestId('map-marker')).toHaveLength(2);
    expect(screen.getAllByTestId('map-line').length).toBeGreaterThan(0);
  });

  it('「担当なし」行は 1 人の順路ではないので線を描かず、点だけにする', () => {
    render(
      <MonitorMapClient
        row={makeRow({ staff_id: null, visits: twoStops() })}
        selectedVisitId={null}
        matchM={100}
        nearby={[]}
      />,
    );
    expect(screen.getAllByTestId('map-marker')).toHaveLength(2);
    expect(screen.queryAllByTestId('map-line')).toHaveLength(0);
  });

  it('データの更新 (60 秒ごと) では表示範囲を合わせ直さず、訪問を選び直したときだけ合わせる', () => {
    const visits = twoStops();
    const props = { selectedVisitId: null, matchM: 100, nearby: [] };
    const { rerender } = render(
      <MonitorMapClient row={makeRow({ staff_id: 'staff-a', visits })} {...props} />,
    );
    expect(map.fitBounds).toHaveBeenCalledTimes(1);
    // 同じ行・同じ選択のまま、応答が作り直された (新しいオブジェクト)。
    rerender(
      <MonitorMapClient
        row={makeRow({ staff_id: 'staff-a', visits: visits.map((v) => ({ ...v })) })}
        {...props}
      />,
    );
    expect(map.fitBounds).toHaveBeenCalledTimes(1);
    // 訪問を選んだ: 合わせ直す。
    rerender(
      <MonitorMapClient
        row={makeRow({ staff_id: 'staff-a', visits })}
        {...props}
        selectedVisitId={visits[0]!.visit_id}
      />,
    );
    expect(map.fitBounds).toHaveBeenCalledTimes(2);
  });
});
