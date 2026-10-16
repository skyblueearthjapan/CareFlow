/**
 * 患者様の住所から Google マップのリンクを作る (設計 2026-10-17・PO 決定)。
 *
 * - 行き先は**住所の文字**で渡す。登録座標は数百 m ずれている方がいる
 *   (本番 10/17 実測: 75 人中 2 人が 400〜800m) ので使わない。
 * - API キーも料金も要らない「Maps URLs」の形。スマホでは Google マップの
 *   アプリが開き、無ければブラウザで開く。
 * - `hl=ja` は公式の説明に無いが、ブラウザで開いたときに日本語で出る。
 * - 移動手段は車 (`travelmode=driving`)。付けないと PC では電車や徒歩で出ることがある。
 *
 * 住所が空・空白だけのときは null (ボタンを出さない)。
 */
function cleanAddress(address: string | null | undefined): string | null {
  // `|` は経由地の区切りなので、住所の中にあれば空白にする (経由地が割れないように)。
  const a = (address ?? '').replace(/\|/g, ' ').trim();
  return a.length > 0 ? a : null;
}

const TAIL = '&travelmode=driving&hl=ja';

/** スマホ用: その住所までの道案内を開く。 */
export function googleMapsDirectionsUrl(address: string | null | undefined): string | null {
  const a = cleanAddress(address);
  if (!a) return null;
  return `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(a)}${TAIL}`;
}

/** PC 用: その住所にピンが立った地図を開く。 */
export function googleMapsPlaceUrl(address: string | null | undefined): string | null {
  const a = cleanAddress(address);
  if (!a) return null;
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(a)}&hl=ja`;
}

/**
 * PC 用: 住所 A から住所 B への経路 (PO 要望 2026-10-17: 事業所にいる管理者が
 * 「A さんの家から B さんの家へ」を押すだけで見られるように)。
 * どちらかの住所が空なら null。
 */
export function googleMapsRouteUrl(
  fromAddress: string | null | undefined,
  toAddress: string | null | undefined,
): string | null {
  const from = cleanAddress(fromAddress);
  const to = cleanAddress(toAddress);
  if (!from || !to) return null;
  return `https://www.google.com/maps/dir/?api=1&origin=${encodeURIComponent(from)}&destination=${encodeURIComponent(to)}${TAIL}`;
}

/**
 * PC で開いたときに Google マップが受け付ける最大の地点数 (出発地＋経由地 9＋目的地)。
 * スマホのブラウザは経由地 3 までなので、1 日の経路は PC の訪問モニター向け。
 */
export const MAX_ROUTE_STOPS = 11;

/**
 * PC 用: 1 日の順路をまとめて 1 本の経路で開く (PO 要望 2026-10-17)。
 * 住所の無い方は飛ばし、続けて同じ住所 (同住所のペア) は 1 か所にまとめる。
 * 地点が 2 か所未満なら null。多すぎるときは先頭から MAX_ROUTE_STOPS か所まで。
 */
export function googleMapsDayRouteUrl(
  addresses: readonly (string | null | undefined)[],
): { url: string; stops: number; truncated: boolean } | null {
  const stops: string[] = [];
  for (const raw of addresses) {
    const a = cleanAddress(raw);
    if (a && stops[stops.length - 1] !== a) stops.push(a);
  }
  if (stops.length < 2) return null;
  const truncated = stops.length > MAX_ROUTE_STOPS;
  const used = stops.slice(0, MAX_ROUTE_STOPS);
  const origin = used[0]!;
  const destination = used[used.length - 1]!;
  const waypoints = used.slice(1, -1);
  let url = `https://www.google.com/maps/dir/?api=1&origin=${encodeURIComponent(origin)}&destination=${encodeURIComponent(destination)}`;
  if (waypoints.length > 0) url += `&waypoints=${encodeURIComponent(waypoints.join('|'))}`;
  return { url: `${url}${TAIL}`, stops: used.length, truncated };
}

/**
 * PC 用: 登録の位置から、実際に QR を読んだ場所 (提案の位置) までの経路
 * (location-drift-align-2026-10-17 §5「地図で見比べる」)。2 点と距離が一度に見られる。
 * ここだけは住所ではなく「緯度,経度」で渡す (比べたいのが登録座標そのものなので)。
 * 登録座標が無いときは、提案の位置にピンが立った地図を開く。
 */
export function googleMapsCompareUrl(
  from: { lat: number; lng: number } | null,
  to: { lat: number; lng: number },
): string {
  const dest = encodeURIComponent(`${to.lat},${to.lng}`);
  if (!from) return `https://www.google.com/maps/search/?api=1&query=${dest}&hl=ja`;
  const origin = encodeURIComponent(`${from.lat},${from.lng}`);
  return `https://www.google.com/maps/dir/?api=1&origin=${origin}&destination=${dest}${TAIL}`;
}
