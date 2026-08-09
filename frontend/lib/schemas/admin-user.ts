/**
 * Admin user-management zod schemas — mirrors `backend/app/schemas/admin_user.py`.
 *
 * Used by the `/admin/users` page hooks (`lib/queries/admin-users.ts`) and the
 * create / edit dialogs.
 */
import { z } from 'zod';

// 二軸分離 (PO 決定 2026-08-09): アカウント権限は 2 値。
//   admin = 管理者 (すべての編集が可能) / staff = 一般 (閲覧中心)
// 旧 'manager' は廃止 (0069 で admin へ移行済み)。zod の受理値には旧トークン・
// 旧データ互換のため残すが、選択肢 (ADMIN_USER_ROLE_OPTIONS) には出さない。
export const ADMIN_USER_ROLES = ['admin', 'manager', 'staff'] as const;
export type AdminUserRole = (typeof ADMIN_USER_ROLES)[number];

/** 新規作成・編集ダイアログに出す選択肢 (2 値)。 */
export const ADMIN_USER_ROLE_OPTIONS = ['admin', 'staff'] as const;

export const ADMIN_ROLE_LABELS: Record<AdminUserRole, string> = {
  admin: '管理者',
  // 旧 manager は admin の別名 — 残存表示も「管理者」に寄せる。
  manager: '管理者',
  staff: '一般',
};

export const adminUserReadSchema = z.object({
  id: z.string().uuid(),
  // email is nullable: staff-role accounts log in with `username` and may have
  // no email at all (backend P1a made the column nullable).
  email: z.string().email().nullable().optional(),
  username: z.string().nullable().optional(),
  staff_name: z.string().nullable().optional(),
  role: z.enum(ADMIN_USER_ROLES),
  staff_id: z.string().uuid().nullable().optional(),
  must_change_password: z.boolean().default(false),
  failed_login_count: z.number().int().nonnegative().default(0),
  locked_until: z.string().nullable().optional(),
  deleted_at: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type AdminUserRead = z.infer<typeof adminUserReadSchema>;

export const adminUserCreateSchema = z.object({
  // email is required for admin/manager but optional for staff; role-based
  // validation is enforced in the create dialog (and 422 from the backend).
  email: z.string().email('有効なメールアドレスを入力してください').nullable().optional(),
  username: z.string().nullable().optional(),
  role: z.enum(ADMIN_USER_ROLES).default('staff'),
  staff_id: z.string().uuid().nullable().optional(),
});
export type AdminUserCreate = z.infer<typeof adminUserCreateSchema>;

export const adminUserUpdateSchema = z.object({
  email: z.string().email().nullable().optional(),
  username: z.string().nullable().optional(),
  role: z.enum(ADMIN_USER_ROLES).optional(),
  staff_id: z.string().uuid().nullable().optional(),
  must_change_password: z.boolean().optional(),
});
export type AdminUserUpdate = z.infer<typeof adminUserUpdateSchema>;

export const adminUserCreateResponseSchema = z.object({
  user: adminUserReadSchema,
  temp_password: z.string(),
});
export type AdminUserCreateResponse = z.infer<typeof adminUserCreateResponseSchema>;

export const adminPasswordResetResponseSchema = z.object({
  user_id: z.string().uuid(),
  temp_password: z.string(),
});
export type AdminPasswordResetResponse = z.infer<typeof adminPasswordResetResponseSchema>;

export function roleLabel(role: AdminUserRole | string): string {
  return (ADMIN_ROLE_LABELS as Record<string, string>)[role] ?? role;
}
