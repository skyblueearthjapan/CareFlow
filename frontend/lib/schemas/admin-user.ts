/**
 * Admin user-management zod schemas — mirrors `backend/app/schemas/admin_user.py`.
 *
 * Used by the `/admin/users` page hooks (`lib/queries/admin-users.ts`) and the
 * create / edit dialogs.
 */
import { z } from 'zod';

export const ADMIN_USER_ROLES = ['admin', 'manager', 'staff'] as const;
export type AdminUserRole = (typeof ADMIN_USER_ROLES)[number];

export const ADMIN_ROLE_LABELS: Record<AdminUserRole, string> = {
  admin: '管理者',
  manager: 'マネージャー',
  staff: 'スタッフ',
};

export const adminUserReadSchema = z.object({
  id: z.string().uuid(),
  email: z.string().email(),
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
  email: z.string().email('有効なメールアドレスを入力してください'),
  role: z.enum(ADMIN_USER_ROLES).default('staff'),
  staff_id: z.string().uuid().nullable().optional(),
});
export type AdminUserCreate = z.infer<typeof adminUserCreateSchema>;

export const adminUserUpdateSchema = z.object({
  email: z.string().email().optional(),
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
