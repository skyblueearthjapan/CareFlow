/**
 * Mentor assignment zod schemas — mirrors the F5-Backend contract for
 * `/api/v1/staff/{staff_id}/mentor`:
 *
 *   GET .../mentor → MentorRead
 *     { mentor_staff_id: UUID|null, mentor_staff_name?: str }
 *   PUT .../mentor  Body={ mentor_staff_id: UUID|null } → MentorRead
 */
import { z } from 'zod';

export const mentorAssignSchema = z.object({
  mentor_staff_id: z.string().uuid().nullable(),
});

export const mentorReadSchema = z.object({
  mentor_staff_id: z.string().uuid().nullable(),
  mentor_staff_name: z.string().nullable().optional(),
});

export type MentorAssign = z.infer<typeof mentorAssignSchema>;
export type MentorRead = z.infer<typeof mentorReadSchema>;
