/**
 * Staff weekly-shift zod schemas — mirrors backend `staff_shifts` table /
 * pydantic ShiftItem + ShiftsBulk shapes (F5-Backend, in-flight Wave 2).
 *
 * Endpoint contract:
 *   GET /api/v1/staff/{staff_id}/shifts
 *     -> { shifts: ShiftItem[7] }   // weekday 0=Mon ... 6=Sun
 *   PUT /api/v1/staff/{staff_id}/shifts
 *     body: { shifts: ShiftItem[7] }
 *     -> { shifts: ShiftItem[7] }
 *
 * Validation:
 *   - shifts.length === 7 and weekdays form the complete 0..6 set
 *   - if is_on=true then start_time + end_time required and start < end
 *   - if is_on=false then start_time / end_time may be null
 */
import { z } from 'zod';

const HHMM_REGEX = /^([01]\d|2[0-3]):[0-5]\d$/;

const hhmmSchema = z
  .string()
  .regex(HHMM_REGEX, 'HH:MM 形式で入力してください');

/** Compares two HH:MM strings lexicographically (safe because zero-padded). */
export function isStartBeforeEnd(start: string, end: string): boolean {
  return start < end;
}

export const staffShiftItemSchema = z
  .object({
    weekday: z.number().int().min(0).max(6),
    is_on: z.boolean(),
    start_time: hhmmSchema.nullable().optional(),
    end_time: hhmmSchema.nullable().optional(),
  })
  .superRefine((val, ctx) => {
    if (val.is_on) {
      if (!val.start_time) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['start_time'],
          message: '勤務日は開始時刻が必須です',
        });
      }
      if (!val.end_time) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['end_time'],
          message: '勤務日は終了時刻が必須です',
        });
      }
      if (
        val.start_time &&
        val.end_time &&
        !isStartBeforeEnd(val.start_time, val.end_time)
      ) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['end_time'],
          message: '終了時刻は開始時刻より後にしてください',
        });
      }
    }
  });

export type StaffShiftItem = z.infer<typeof staffShiftItemSchema>;

export const shiftsBulkSchema = z
  .object({
    shifts: z
      .array(staffShiftItemSchema)
      .length(7, '週 7 日分の入力が必要です'),
  })
  .superRefine((val, ctx) => {
    const seen = new Set<number>();
    for (const s of val.shifts) {
      if (seen.has(s.weekday)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['shifts'],
          message: `weekday=${s.weekday} が重複しています`,
        });
      }
      seen.add(s.weekday);
    }
    for (let i = 0; i < 7; i += 1) {
      if (!seen.has(i)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['shifts'],
          message: `weekday=${i} が不足しています`,
        });
      }
    }
  });

export type ShiftsBulk = z.infer<typeof shiftsBulkSchema>;

/** Server response payload: same shape as the PUT body. */
export const shiftsResponseSchema = z.object({
  shifts: z.array(staffShiftItemSchema).length(7),
});
export type ShiftsResponse = z.infer<typeof shiftsResponseSchema>;

/** Convenience: build a default 7-day "all off" template (used when the
 * backend returns an empty list during initial onboarding). */
export function buildEmptyShifts(): StaffShiftItem[] {
  return Array.from({ length: 7 }, (_, weekday) => ({
    weekday,
    is_on: false,
    start_time: null,
    end_time: null,
  }));
}
