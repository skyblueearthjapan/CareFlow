/**
 * QR チェックインのトークン抽出 (Phase 2).
 *
 * 患者宅の固定 QR の中身は `https://<app>/q/{token}` 形式 (設計 Phase 5)。
 * アプリ内スキャナは URL から **token 部分のみ** を取り出し、checkin API の
 * `qr_token` として送る。標準カメラからのディープリンクにも同じパスを使う。
 *
 * 寛容な解析:
 *   - 完全な URL (`https://app.example/q/abc123`) → `abc123`
 *   - 末尾スラッシュやクエリ/ハッシュ付き (`/q/abc123/?v=2#x`) → `abc123`
 *   - パスのみ (`/q/abc123`) → `abc123`
 *   - 既に生トークンだけ (`abc123`) → `abc123` (手入力/旧QR互換)
 *   - 空文字や `/q/` の後ろが空 → `null`
 */

/** `/q/{token}` を含む文字列、または生トークンから token を取り出す。 */
export function extractQrToken(raw: string): string | null {
  const text = raw.trim();
  if (!text) return null;

  // `/q/<token>` パターンを優先的に拾う (URL でもパスのみでも一致)。
  // token は URL-safe (token_urlsafe = [A-Za-z0-9_-]) のみ許容し、
  // 後続の `/`・`?`・`#`・空白で切り出す。
  const match = text.match(/\/q\/([A-Za-z0-9_-]+)/);
  if (match && match[1]) {
    return match[1];
  }

  // `/q/` を含まない場合: URL ならトークンとして扱わない (別サイトの QR 等)。
  // スキームや `/` を含まない素のトークンだけを後方互換で受理する。
  if (!text.includes('://') && !text.includes('/')) {
    // token_urlsafe 相当の文字種のみ許容。
    if (/^[A-Za-z0-9_-]+$/.test(text)) {
      return text;
    }
  }

  return null;
}

/**
 * 読取時刻の引き継ぎ (`/q/{token}` → 訪問詳細)。
 *
 * 標準カメラで QR を読むと、まず `/q/{token}` が開く。そこで候補を解決してから
 * 訪問詳細 (`?qr=`) へ移るので、詳細がマウントされた時刻は読み取った瞬間より遅い
 * (担当外の選択画面で止まれば、その分だけ遅れる)。`/q` を開いた時刻をこのクエリで
 * 渡し、打刻の `at` に載せる (設計 2026-09-30 §3)。
 */
export const QR_READ_AT_PARAM = 'read_at';

/**
 * 引き継いだ読取時刻を受け入れる上限 (ms)。`/q` で止まっている時間はせいぜい数分。
 * これより古い値は、開きっぱなしの URL の再読み込みなどなので採らない。
 */
const HANDOFF_READ_MAX_AGE_MS = 10 * 60 * 1000;

/** `Date#toISOString()` の形 (UTC)。これ以外の書き方は受け付けない。 */
const ISO_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/;

/** 訪問詳細へのディープリンク (`?qr=` に読取時刻を添える)。 */
export function visitDeepLinkHref(visitId: string, token: string, readAt: string): string {
  return `/m/today/${visitId}?qr=${encodeURIComponent(token)}&${QR_READ_AT_PARAM}=${encodeURIComponent(readAt)}`;
}

/**
 * 引き継いだ読取時刻 (URL のクエリ = 外から来る値) を検証する。採れる値なら ISO 8601、
 * 採れない値は null (呼び出し元は現在時刻を使う)。
 *
 * 採らない値: 形が違う・日付として読めない・**未来**・古すぎる。URL は手で書き換え
 * られるので、実績の時刻を好きな時刻にする抜け道にしない。
 */
export function parseHandoffReadAt(raw: string | null | undefined, nowMs: number): string | null {
  if (!raw || !ISO_UTC.test(raw)) return null;
  const ms = new Date(raw).getTime();
  if (Number.isNaN(ms)) return null;
  if (ms > nowMs || nowMs - ms > HANDOFF_READ_MAX_AGE_MS) return null;
  return new Date(ms).toISOString();
}
