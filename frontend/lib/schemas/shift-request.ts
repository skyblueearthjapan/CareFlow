/**
 * Shift-request zod schemas — mirrors `backend/app/schemas/shift_request.py`.
 *
 * Endpoints:
 *   GET   /api/v1/staff/{staff_id}/shift-requests        → ShiftRequestRead[]
 *   POST  /api/v1/staff/{staff_id}/shift-requests        → ShiftRequestRead
 *   PATCH /api/v1/shift-requests/{id}/status             → ShiftRequestRead
 */
import { z } from 'zod';

export const shiftRequestStatusSchema = z.enum(['submitted', 'reviewed']);

const monthDateSchema = z
  .string()
  .regex(
    /^\d{4}-\d{2}-\d{2}$/,
    '対象月は YYYY-MM-DD (月初) で送信してください',
  );

export const shiftRequestCreateSchema = z.object({
  target_month: monthDateSchema,
  content: z
    .string()
    .min(1, '内容を入力してください')
    .max(2000, '2000文字以内で入力してください'),
});

export const shiftRequestStatusUpdateSchema = z.object({
  status: shiftRequestStatusSchema,
});

export const shiftRequestReadSchema = z.object({
  id: z.string().uuid(),
  staff_id: z.string().uuid(),
  target_month: monthDateSchema,
  content: z.string(),
  submitted_at: z.string(),
  status: shiftRequestStatusSchema,
  reviewed_by: z.string().uuid().nullable().optional(),
  reviewed_at: z.string().nullable().optional(),
});

export type ShiftRequestStatus = z.infer<typeof shiftRequestStatusSchema>;
export type ShiftRequestCreate = z.infer<typeof shiftRequestCreateSchema>;
export type ShiftRequestStatusUpdate = z.infer<
  typeof shiftRequestStatusUpdateSchema
>;
export type ShiftRequestRead = z.infer<typeof shiftRequestReadSchema>;
