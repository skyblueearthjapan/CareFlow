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

/**
 * ブラウザ測位のベストエフォート結果 (失敗時は座標が undefined)。
 *
 * 訪問詳細 (`/m/today/{visitId}`) と QR ランディング (`/q/{token}` の予定外
 * 記録) の双方が同じ形でプレビュー → POST するため、ここに集約している。
 */
export interface GeoFix {
  lat?: number;
  lng?: number;
  accuracy?: number;
  /** GeolocationPositionError.code (1=権限拒否 2=測位不能 3=タイムアウト)。 */
  errorCode?: number;
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

/**
 * ブラウザ測位 (ベストエフォート)。失敗 / 拒否 / タイムアウトでも reject せず
 * 空の {@link GeoFix} で resolve するので、呼び出し側は必ず打刻を続行できる
 * (距離判定はサーバが `no_gps` として扱う)。
 *
 * `enableHighAccuracy` は要求するがあくまでベストエフォート。timeout は端末の
 * コールドスタート測位を待てるよう長め (15s) に取る。
 */
export function getGeolocation(): Promise<GeoFix> {
  return new Promise((resolve) => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) {
      resolve({ errorCode: 2 });
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) =>
        resolve({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
        }),
      // 失敗理由 (権限拒否/測位不能/タイムアウト) をプレビューの案内に使う。
      (err) => resolve({ errorCode: err.code }),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  });
}

/** 測位失敗コード → 現場向けの対処ヒント。 */
export function geoErrorHint(code: number | undefined): string | null {
  switch (code) {
    case 1:
      return '位置情報の利用が許可されていません。端末の設定でこのサイト（ブラウザ）の位置情報を「許可」にしてから「位置を再取得」を押してください。';
    case 3:
      return '測位がタイムアウトしました。屋外や窓際で「位置を再取得」をお試しください。';
    case 2:
      return '端末が位置を測位できませんでした。電波状況を確認して「位置を再取得」をお試しください。';
    default:
      return null;
  }
}

/** {@link GeoFix} を打刻 payload の座標フィールドへ射影する (undefined は落とす)。 */
export function coordsOf(geo: GeoFix): { lat?: number; lng?: number; accuracy?: number } {
  return {
    ...(geo.lat !== undefined ? { lat: geo.lat } : {}),
    ...(geo.lng !== undefined ? { lng: geo.lng } : {}),
    ...(geo.accuracy !== undefined ? { accuracy: geo.accuracy } : {}),
  };
}
