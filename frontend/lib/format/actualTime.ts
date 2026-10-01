/**
 * 打刻の実績時刻 (到着 / 退出) を「予定の隣に並べる」ための表示ヘルパー。
 *
 * 背景 (お客様要望 2026-09-18): QR 打刻の実時刻 (例 12:56 到着 / 13:40 退出) は
 * 記録されているのに、画面が予定時刻 (13:00-13:40) しか出さないため「実時間が
 * 取れていない」と誤解されていた。予定はそのまま残し、打刻があれば実績を並べる。
 *
 * 入力は BE `VisitRead.actual_arrival_at` / `actual_departure_at` (ISO 8601・tz
 * 付き) か、訪問モニターの `visit.arrival?.scanned_at` / `visit.departure
 * ?.scanned_at`。どちらも同じ「最新の到着打刻 / 最新の退出打刻」なので、
 * モバイルと PC でこの 1 ファイルを共用する。
 *
 * 時刻は **JST 固定** (`Asia/Tokyo`) で整形する。現場端末は JST なので
 * `new Date(iso).toTimeString()` (端末ローカル) と表示は一致し、テスト・サーバ
 * レンダリング・海外からの閲覧でもズレない (モニターの `isoToHm` と同じ流儀)。
 */

/** `hourCycle: 'h23'` は 0 時を "24:00" と書かせないための明示 (将来の ICU 差異への保険)。 */
const JST_HM = new Intl.DateTimeFormat('ja-JP', {
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
  timeZone: 'Asia/Tokyo',
});

/** tz 指定 (`Z` / `+09:00` / `+0900`) を持つ ISO か。 */
const HAS_TZ = /(?:Z|[+-]\d{2}:?\d{2})$/;

/**
 * ISO 8601 → JST の "HH:MM"。null / 空 / 不正な値は null。
 *
 * tz の無い naive な値 (`2026-09-18T12:56:00`) は **JST として読む**。BE は tz 付きで
 * 返す契約だが、DB から naive な値がそのまま出た旧データ / 退避レコードを UTC 扱い
 * すると 9 時間ズレた時刻を現場に見せてしまう。ここで JST を補う方が安全側。
 */
export function jstHm(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(HAS_TZ.test(iso) ? iso : `${iso}+09:00`);
  if (Number.isNaN(d.getTime())) return null;
  return JST_HM.format(d);
}

/**
 * "HH:MM" (または "HH:MM:SS") → 0 時からの分。読めない値は null。
 *
 * 「実績の時刻を合わせる」(設計 2026-09-30) の分単位の計算用。時刻を合わせる
 * 操作はすべて「その日の 0 時からの分」で扱い、API へは "HH:MM" で渡す
 * (端末側でタイムゾーン計算をしない = 設計 §6-1)。
 */
export function hmToMinutes(hm: string | null | undefined): number | null {
  if (!hm) return null;
  const m = /^(\d{1,2}):(\d{2})/.exec(hm);
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  if (h > 23 || min > 59) return null;
  return h * 60 + min;
}

/** 0 時からの分 → "HH:MM"。 */
export function minutesToHm(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

/** ISO 8601 → JST の 0 時からの分 (秒は切り捨て)。null / 不正な値は null。 */
export function jstMinutes(iso: string | null | undefined): number | null {
  return hmToMinutes(jstHm(iso));
}

/** 実績時刻の部品。画面ごとに接頭辞 (実績 / 到着 / ✓ / ▶) を付けて使う。 */
export interface ActualTimeParts {
  /** 到着打刻の "HH:MM" (JST)。 */
  arrival: string;
  /** 退出打刻の "HH:MM" (JST)。未退出 (訪問中) は null。 */
  departure: string | null;
  /** 退出まで揃っているか (= 実績が確定した訪問)。 */
  done: boolean;
  /** 本文用: "12:56 – 13:40" / "12:56 〜"。 */
  range: string;
  /** 狭い場所用 (モニターのカード 2 行目など): "12:56–13:40" / "12:56〜"。 */
  compactRange: string;
}

/**
 * 到着 / 退出の ISO から表示部品を作る。打刻が無ければ null (= 行を出さない)。
 *
 * 到着が無い場合は退出だけあっても null を返す。実績の起点が無いまま
 * 「– 13:40」と出すと予定との区別がつかず、現場に誤読を生むため。
 * 日跨ぎ (23:50 到着 → 00:20 退出) は**時刻だけ**を出す (日付は出さない)。
 */
export function actualTimeParts(
  arrivalIso: string | null | undefined,
  departureIso: string | null | undefined,
): ActualTimeParts | null {
  const arrival = jstHm(arrivalIso);
  if (!arrival) return null;
  const departure = jstHm(departureIso);
  return {
    arrival,
    departure,
    done: departure != null,
    range: departure ? `${arrival} – ${departure}` : `${arrival} 〜`,
    compactRange: departure ? `${arrival}–${departure}` : `${arrival}〜`,
  };
}

/** 本文用の実績レンジ文字列 ("12:56 – 13:40" / "12:56 〜")。打刻なしは null。 */
export function fmtActualRange(
  arrivalIso: string | null | undefined,
  departureIso: string | null | undefined,
): string | null {
  return actualTimeParts(arrivalIso, departureIso)?.range ?? null;
}

/**
 * 「遅れて届いた」とみなす目安 (分)。圏外で退避して後から送った打刻のうち、読み取りから
 * 受信までがこれを超えたもの (日付をまたいだものは長さに依らず)。BE
 * `actuals.LATE_DELIVERY_MARK_AFTER` と同じ値 (設計 checkin-late-delivery-design-2026-10-01)。
 */
export const LATE_DELIVERY_MARK_AFTER_MIN = 30;

const JST_DAY_TIME = new Intl.DateTimeFormat('ja-JP', {
  month: 'numeric',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  hourCycle: 'h23',
  timeZone: 'Asia/Tokyo',
});

const JST_DATE_KEY = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo' });

function parseIso(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(HAS_TZ.test(iso) ? iso : `${iso}+09:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Date → JST の "M/D H:MM" (例 "10/2 8:30")。 */
export function jstDayTime(date: Date): string {
  const parts: Record<string, string> = {};
  for (const p of JST_DAY_TIME.formatToParts(date)) parts[p.type] = p.value;
  return `${parts.month}/${parts.day} ${Number(parts.hour)}:${parts.minute}`;
}

/**
 * 遅れて届いた打刻の表示 — 「遅れて届いた（10/2 8:30 受信）」。
 * `receivedIso` は BE の `*_late_received_at` (遅れていなければ null → null を返す)。
 */
export function lateDeliveryLabel(receivedIso: string | null | undefined): string | null {
  const received = parseIso(receivedIso);
  if (!received) return null;
  return `遅れて届いた（${jstDayTime(received)} 受信）`;
}

/**
 * 読み取った時刻 `readIso` の打刻を `receivedAt` に送ったとき「遅れて届いた」になるか
 * (BE `actuals.late_received_at` と同じ目安)。端末側の再送結果の案内に使う。
 */
export function isLateDelivery(readIso: string | null | undefined, receivedAt: Date): boolean {
  const read = parseIso(readIso);
  if (!read || read.getTime() >= receivedAt.getTime()) return false;
  if (receivedAt.getTime() - read.getTime() > LATE_DELIVERY_MARK_AFTER_MIN * 60_000) return true;
  return JST_DATE_KEY.format(read) !== JST_DATE_KEY.format(receivedAt);
}
