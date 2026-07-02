'use client';

/**
 * 訪問モニター 地図 (Leaflet + OSM) — クライアント専用の実装本体。
 *
 * SSR で ``window is not defined`` を出さないため、本ファイルは必ず
 * ``MonitorMap.tsx`` 経由で ``dynamic(import, { ssr:false })`` でのみ読み込む。
 *
 * 行 (コース) 選択でコース目的地ピン + 順路 + 区間距離を描画。場所違い (mismatch)
 * の選択 visit は 自宅↔実GPS を赤破線 + 距離 + しきい値円、(nearby があれば) 近隣
 * 候補「〇〇様宅？」を表示。ホイールズームは無効 (パネルスクロール優先)。
 */
import { useEffect, useMemo } from 'react';
import L from 'leaflet';
import { Circle, MapContainer, Marker, Polyline, Popup, TileLayer, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

import type { MonitorStaffRow, MonitorVisit, NearbyPatient } from '@/lib/schemas/monitor';

import { STATUS_COLOR, displayStatus, formatDistance } from './constants';
import { haversineMeters } from '@/lib/geo';

interface MonitorMapClientProps {
  row: MonitorStaffRow | null;
  selectedVisitId: string | null;
  matchM: number;
  nearby: NearbyPatient[];
}

type LatLng = [number, number];

function hasCoords(
  v: MonitorVisit,
): v is MonitorVisit & { patient_lat: number; patient_lng: number } {
  return typeof v.patient_lat === 'number' && typeof v.patient_lng === 'number';
}

const numIcon = (color: string, selected: boolean, num: number) =>
  L.divIcon({
    className: '',
    iconSize: [18, 18],
    iconAnchor: [9, 9],
    // 影は温色 rgba(28,25,23,.35)（デザインシステム 1-8 準拠）。
    // selected の outline は var(--text-primary) = #1c1917 と同値のため var() を使用可（html style 内）。
    html: `<div style="width:16px;height:16px;border-radius:50%;border:2px solid #fff;box-shadow:0 1px 4px rgba(28,25,23,.35);background:${color};${
      selected ? 'outline:3px solid var(--text-primary);outline-offset:1px;' : ''
    }position:relative"><span style="position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);font-size:9px;font-weight:700;color:#fff">${num}</span></div>`,
  });

const emojiIcon = (bg: string, txt: string) =>
  L.divIcon({
    className: '',
    iconSize: [30, 30],
    iconAnchor: [15, 28],
    // 影は温色 rgba(28,25,23,.35)（デザインシステム 1-8 準拠）。
    html: `<div style="width:30px;height:30px;border-radius:9px;background:${bg};color:#fff;display:flex;align-items:center;justify-content:center;font-size:16px;border:2px solid #fff;box-shadow:0 2px 6px rgba(28,25,23,.35)">${txt}</div>`,
  });

const nearIcon = () =>
  L.divIcon({
    className: '',
    iconSize: [22, 22],
    iconAnchor: [11, 11],
    html: `<div style="width:22px;height:22px;border-radius:50%;background:#fff;color:var(--text-secondary);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;border:2px dashed var(--text-muted)">？</div>`,
  });

/** points が変わるたびに地図を fit する子コンポーネント。 */
function FitBounds({ points }: { points: LatLng[] }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 1 && points[0]) {
      map.setView(points[0], 16);
    } else if (points.length > 1) {
      map.fitBounds(points, { padding: [40, 40], maxZoom: 16 });
    }
    const t = setTimeout(() => map.invalidateSize(), 80);
    return () => clearTimeout(t);
  }, [map, points]);
  return null;
}

export default function MonitorMapClient({
  row,
  selectedVisitId,
  matchM,
  nearby,
}: MonitorMapClientProps) {
  const stops = useMemo(() => (row ? row.visits.filter(hasCoords) : []), [row]);
  const selected = stops.find((v) => v.visit_id === selectedVisitId) ?? null;
  const selMismatch =
    selected != null &&
    displayStatus(selected) === 'mismatch' &&
    typeof selected.arrival?.lat === 'number' &&
    typeof selected.arrival?.lng === 'number';

  const points: LatLng[] = useMemo(() => {
    const pts: LatLng[] = stops.map((v) => [v.patient_lat, v.patient_lng]);
    if (selMismatch && selected?.arrival) {
      pts.push([selected.arrival.lat as number, selected.arrival.lng as number]);
    }
    for (const n of nearby) pts.push([n.lat, n.lng]);
    return pts;
  }, [stops, selMismatch, selected, nearby]);

  // 順路ラインと区間距離。
  const segments = useMemo(() => {
    const segs: { a: LatLng; b: LatLng; mid: LatLng; dist: number }[] = [];
    for (let i = 0; i < stops.length - 1; i++) {
      const cur = stops[i];
      const nxt = stops[i + 1];
      if (!cur || !nxt) continue;
      const a: LatLng = [cur.patient_lat, cur.patient_lng];
      const b: LatLng = [nxt.patient_lat, nxt.patient_lng];
      segs.push({
        a,
        b,
        mid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2],
        dist: haversineMeters({ lat: a[0], lng: a[1] }, { lat: b[0], lng: b[1] }),
      });
    }
    return segs;
  }, [stops]);

  const center: LatLng = points.length > 0 ? (points[0] as LatLng) : [35.61, 140.11];

  return (
    <MapContainer
      center={center}
      zoom={14}
      scrollWheelZoom={false}
      attributionControl={false}
      style={{ height: 280, width: '100%' }}
    >
      <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" maxZoom={19} />
      <FitBounds points={points} />

      {/* 順路ライン + 区間距離 */}
      {segments.map((s, i) => (
        <Polyline
          key={`seg-${i}`}
          positions={[s.a, s.b]}
          pathOptions={{ color: '#0d9488' /* = --brand-primary; SVG属性のためvar()不可、tokens.cssと同期 */, weight: 2, opacity: 0.45 }}
        />
      ))}

      {/* コース目的地ピン */}
      {stops.map((v, i) => {
        const sel = v.visit_id === selectedVisitId;
        const st = displayStatus(v);
        return (
          <Marker
            key={v.visit_id}
            position={[v.patient_lat, v.patient_lng]}
            icon={numIcon(STATUS_COLOR[st], sel, i + 1)}
          >
            <Popup>
              <b>
                {i + 1}. {v.patient_name ?? '—'}
              </b>
              <br />
              {v.start_time}–{v.end_time}
              {v.arrival?.distance_m != null && (
                <>
                  <br />
                  距離 {Math.round(v.arrival.distance_m)}m
                </>
              )}
            </Popup>
          </Marker>
        );
      })}

      {/* 場所違い: 自宅↔実GPS + しきい値円 + 近隣候補 */}
      {selMismatch && selected?.arrival && (
        <>
          <Circle
            center={[selected.patient_lat, selected.patient_lng]}
            radius={matchM}
            pathOptions={{
              color: '#0d9488', // = --brand-primary; SVG属性のためvar()不可、tokens.cssと同期
              weight: 1,
              dashArray: '4',
              fillColor: '#0d9488',
              fillOpacity: 0.06,
            }}
          />
          <Marker
            position={[selected.patient_lat, selected.patient_lng]}
            icon={emojiIcon('var(--brand-primary)', '🏠')}
          >
            <Popup>
              <b>{selected.patient_name ?? '—'} さん宅（予定）</b>
              <br />
              登録住所
            </Popup>
          </Marker>
          <Marker
            position={[selected.arrival.lat as number, selected.arrival.lng as number]}
            icon={emojiIcon('var(--error)', '📍')}
          >
            <Popup>
              <b>実際のGPS現在地</b>
              {selected.arrival.distance_m != null && (
                <>
                  <br />
                  登録住所から {Math.round(selected.arrival.distance_m)}m
                </>
              )}
            </Popup>
          </Marker>
          <Polyline
            positions={[
              [selected.patient_lat, selected.patient_lng],
              [selected.arrival.lat as number, selected.arrival.lng as number],
            ]}
            pathOptions={{ color: '#dc2626' /* = --error; SVG属性のためvar()不可、tokens.cssと同期 */, dashArray: '6', weight: 2.5 }}
          />
          {nearby.map((n) => (
            <Marker key={n.patient_id} position={[n.lat, n.lng]} icon={nearIcon()}>
              <Popup>
                {n.name} 様宅？
                <br />
                GPSから {formatDistance(n.distance_m)}（近隣の登録住所）
              </Popup>
            </Marker>
          ))}
        </>
      )}
    </MapContainer>
  );
}
