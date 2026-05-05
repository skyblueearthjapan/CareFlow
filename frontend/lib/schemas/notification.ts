/**
 * Notification zod schemas — mirrors `backend/app/schemas/notification.py`.
 *
 * Endpoints:
 *   GET   /api/v1/notifications?unread_only&limit&offset → Paginated<NotificationRead>
 *   PATCH /api/v1/notifications/{id}/read                → NotificationRead
 *   POST  /api/v1/notifications/mark-all-read            → { updated: number }
 */
import { z } from 'zod';

export const notificationReadSchema = z.object({
  id: z.string().uuid(),
  user_id: z.string().uuid(),
  type: z.string(),
  title: z.string(),
  body: z.string().nullable().optional(),
  read_at: z.string().nullable().optional(),
  created_at: z.string(),
});

export const notificationListSchema = z.object({
  items: z.array(notificationReadSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
});

export const markAllReadResponseSchema = z.object({
  updated: z.number().int().nonnegative(),
});

export type NotificationRead = z.infer<typeof notificationReadSchema>;
export type NotificationList = z.infer<typeof notificationListSchema>;
export type MarkAllReadResponse = z.infer<typeof markAllReadResponseSchema>;
