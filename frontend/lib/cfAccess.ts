/**
 * Cloudflare Access セッション切れの検知と通知 (2026-07-11 handoff §6-3)。
 *
 * 本番は carelink.kaipoke-api.net 全体が Cloudflare Access (人間用ログイン) で
 * 保護されており、セッションが切れると fetch は
 *   - `redirect: 'manual'` なら opaqueredirect (Access ログインへの 302)
 *   - 通常の fetch ならクロスオリジン リダイレクト起因の TypeError や 401
 * になる。PWA はアドレスバーが無く自力でトップレベル遷移できないため、
 * ここで検知して画面上部バナー (CloudflareAccessBanner) へ通知する。
 *
 * 判定は /api/v1/healthz (アプリ認証不要・Access 配下) への探針で行う:
 *   healthz が 200        → Access は生きている (アプリ側の認証問題)
 *   opaqueredirect / 401  → Access セッション切れの可能性が高い
 *
 * 注意: Access のログイン画面は frame-ancestors 'none' のため iframe では
 * 復旧できない。復旧導線は必ず「窓ごと」のトップレベル遷移にすること。
 */

export const CF_ACCESS_EXPIRED_EVENT = 'cf-access:expired';

/** 探針の乱発防止 (401 が連発しても healthz を叩くのは 30 秒に 1 回まで)。 */
const PROBE_COOLDOWN_MS = 30_000;

let lastProbeAt = 0;
let probing: Promise<boolean> | null = null;

/** Cloudflare Access セッションが有効かを healthz 探針で判定する (browser 専用)。 */
export async function checkAccessSession(): Promise<boolean> {
  if (typeof window === 'undefined') return true;
  // オフラインは Access 切れと区別できない (誤誘導を避けるため通知しない)。
  if (typeof navigator !== 'undefined' && navigator.onLine === false) return true;
  try {
    const res = await fetch(`/api/v1/healthz?_=${Date.now()}`, {
      cache: 'no-store',
      redirect: 'manual',
      credentials: 'include',
    });
    // Access ログインへの 302 は opaqueredirect として観測される。
    if (res.type === 'opaqueredirect') return false;
    if (res.status === 401 || res.status === 403) return false;
    return true;
  } catch {
    // オンラインなのに healthz へ届かない → Access/エッジ起因の可能性として扱う。
    return false;
  }
}

/** バナーへ「Access セッション切れ」を通知する。 */
export function notifyAccessExpired(): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event(CF_ACCESS_EXPIRED_EVENT));
}

/**
 * 「Access 切れかもしれない」シグナル (401 / ネットワークエラー) を受けて、
 * throttle 付きで探針 → 切れていればバナーへ通知する。
 * 呼び出し側は待たずに `void reportPossibleAccessIssue()` で投げてよい。
 */
export async function reportPossibleAccessIssue(): Promise<void> {
  if (typeof window === 'undefined') return;
  const now = Date.now();
  if (probing || now - lastProbeAt < PROBE_COOLDOWN_MS) return;
  lastProbeAt = now;
  probing = checkAccessSession();
  const ok = await probing.finally(() => {
    probing = null;
  });
  if (!ok) notifyAccessExpired();
}

/** テスト用: throttle 状態をリセットする。 */
export function _resetAccessProbeStateForTest(): void {
  lastProbeAt = 0;
  probing = null;
}
