/**
 * `signIn({ redirect: false })` からの `code` を利用者向け文言に変換する。
 * 2026-09-14: 同時ログインで 429 になった際に「パスワードが違う」と誤案内
 * していたため、絞り込み(429)/ロック(423)は別文言にする。
 */
export function loginErrorMessage(code: string | null | undefined): string {
  switch (code) {
    case 'rate_limited':
      return 'ログインの回数制限にかかりました。15分ほど待ってからもう一度お試しください。';
    case 'locked':
      return 'パスワードの間違いが続いたため、このアカウントは一時的にロックされています。15分後にお試しください。';
    default:
      return 'メール／スタッフIDまたはパスワードが正しくありません';
  }
}
