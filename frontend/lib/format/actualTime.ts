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
