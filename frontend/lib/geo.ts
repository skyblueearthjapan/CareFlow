/**
 * 軽量な地理距離ユーティリティ (モバイル QR チェックイン Phase 2).
 *
 * 記録前のクライアント側プレビュー (玄関 QR を読み取った位置と患者の登録座標の
 * 距離) を出すためだけの最小実装。判定の正本はサーバ (judge) で、ここはあくまで
 * 「記録する前に距離/想定判定をユーザーに見せる」ための概算。
 *
 * 既存の `haversineKm` (`components/schedule/WeekdayScheduleCard.tsx`) は重い
 * スケジュール盤コンポーネントに同梱されており、モバイル bundle へ引き込むのを
 * 避けるため距離計算のみをここに切り出している。
 */

export interface LatLng {
  lat: number;
  lng: number;
}

const EARTH_RADIUS_M = 6_371_000; // 地球半径 (m)

/** 2 点間の大円距離をメートルで返す (haversine)。 */
export function haversineMeters(a: LatLng, b: LatLng): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(x)));
}
