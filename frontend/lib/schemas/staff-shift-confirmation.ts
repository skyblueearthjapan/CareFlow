/**
 * Staff shift confirmation (月次出勤カレンダー確定) zod schemas — mirrors backend
 * `app/schemas/staff_shift_confirmation.py`.
 *
 * Endpoint contract (staff-shift-confirmation-design.md §2-a):
 *   GET  /api/v1/staff/{staff_id}/shift-confirmations?from=&to=  -> ShiftConfirmationRead[]
 *   POST /api/v1/staff/{staff_id}/shift-confirmations            body: { month } (admin)
 */
import { z } from 'zod';

const ISO_DATE_REGEX = /^\d{4}-\d{2}-\d{2}$/;

export const shiftConfirmationReadSchema = z.object({
  id: z.string().uuid(),
  staff_id: z.string().uuid(),
  /** 月初日 (YYYY-MM-01) */
  month: z.string().regex(ISO_DATE_REGEX),
  confirmed_by: z.string().uuid().nullable().optional(),
  confirmed_at: z.string(),
});
export type ShiftConfirmationRead = z.infer<typeof shiftConfirmationReadSchema>;

export const shiftConfirmationCreateSchema = z.object({
  /** 月初日 (YYYY-MM-01) のみ受理 (BE でも検証) */
  month: z.string().regex(ISO_DATE_REGEX),
});
export type ShiftConfirmationCreate = z.infer<typeof shiftConfirmationCreateSchema>;
