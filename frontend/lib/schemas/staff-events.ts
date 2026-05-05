/**
 * Staff event (研修日 / イベント) zod schemas — mirrors the F5-Backend
 * contract for `/api/v1/staff/{staff_id}/events`:
 *
 *   GET    .../events?from=YYYY-MM-DD&to=YYYY-MM-DD
 *     → list[EventRead]
 *   POST   .../events                    Body=EventCreate  → EventRead
 *   PATCH  .../events/{event_id}         Body=EventUpdate  → EventRead
 *   DELETE .../events/{event_id}         → 204
 *
 * NOTE: Distinct from `staffEventSchema` in `./staff.ts` (the legacy
 * starts_at/ends_at shape used by Wave 1 read-only mocks). The Wave 2
 * REST contract uses `date` + `start_time` + `end_time` instead.
 */
import { z } from 'zod';

export const eventTypeSchema = z.enum(['研修', 'イベント']);
export type EventType = z.infer<typeof eventTypeSchema>;

const dateSchema = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, '日付は YYYY-MM-DD 形式で入力してください');
const timeSchema = z
  .string()
  .regex(/^\d{2}:\d{2}$/, '時刻は HH:MM 形式で入力してください');

const titleSchema = z
  .string()
  .min(1, 'タイトルを入力してください')
  .max(100, 'タイトルは100文字以内で入力してください');

export const eventCreateSchema = z
  .object({
    date: dateSchema,
    title: titleSchema,
    start_time: timeSchema,
    end_time: timeSchema,
    type: eventTypeSchema,
    note: z.string().max(500).optional().nullable(),
  })
  .refine((v) => v.start_time < v.end_time, {
    message: '開始時刻は終了時刻より前にしてください',
    path: ['end_time'],
  });

export const eventUpdateSchema = z
  .object({
    date: dateSchema.optional(),
    title: titleSchema.optional(),
    start_time: timeSchema.optional(),
    end_time: timeSchema.optional(),
    type: eventTypeSchema.optional(),
    note: z.string().max(500).optional().nullable(),
  })
  .refine(
    (v) => {
      if (v.start_time && v.end_time) return v.start_time < v.end_time;
      return true;
    },
    {
      message: '開始時刻は終了時刻より前にしてください',
      path: ['end_time'],
    },
  );

export const eventReadSchema = z.object({
  id: z.string().uuid(),
  date: dateSchema,
  title: z.string(),
  start_time: timeSchema,
  end_time: timeSchema,
  type: eventTypeSchema,
  note: z.string().nullable().optional(),
});

export type EventCreate = z.infer<typeof eventCreateSchema>;
export type EventUpdate = z.infer<typeof eventUpdateSchema>;
export type EventRead = z.infer<typeof eventReadSchema>;
