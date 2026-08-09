/**
 * アカウント権限 (二軸分離・PO 決定 2026-08-09)。
 *
 * ログイン権限は 2 値:
 *   - admin = 「管理者」…… すべての編集が可能
 *   - staff = 「一般」……… 閲覧中心 (担当患者スコープ・現場操作のみ)
 *
 * 旧 'manager' 権限は廃止 (migration 0069 で admin へ移行済み)。
 * 旧セッションのトークンに残る 'manager' は admin の別名として扱う。
 *
 * ※ スタッフマスタの業務ロール (staff/manager = スケジュールエンジン専用) とは
 *    無関係。混同しないこと。
 */

/** 管理者 (編集可) か。旧 'manager' は admin の別名として true。 */
export function isAdminRole(role: string | null | undefined): boolean {
  return role === 'admin' || role === 'manager';
}

/** アカウント権限の表示名。旧 'manager' は「管理者」と表示する。 */
export function userRoleLabel(role: string | null | undefined): string {
  if (role === 'admin' || role === 'manager') return '管理者';
  if (role === 'staff') return '一般';
  return role ?? '--';
}
