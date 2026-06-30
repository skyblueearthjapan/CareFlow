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
