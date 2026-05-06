/**
 * StaffCompanionAssignment v2 zod schemas (W10-FE1).
 *
 * 設計仕様書 §3.5.x / §4.2.x に基づく同行スタッフ割当スキーマ。
 * Backend `backend/app/schemas/v2/staff_companion_assignment.py` と完全一致。
 *
 * 曜日 (weekday: 0=Mon ... 6=Sun) × パート (am/pm/full) の単位で
 * 新人スタッフ (trainee) への同行スタッフ (companion) を管理する。
 */
import { z } from 'zod';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const STAFF_COMPANION_PARTS = ['am', 'pm', 'full'] as const;
export type StaffCompanionPart = (typeof STAFF_COMPANION_PARTS)[number];

// ---------------------------------------------------------------------------
// Base / Read schemas
// ---------------------------------------------------------------------------

export const staffCompanionAssignmentV2BaseSchema = z.object({
  weekday: z.number().int().min(0).max(6),
  part: z.enum(STAFF_COMPANION_PARTS),
  companion_staff_id: z.string().uuid(),
});

export type StaffCompanionAssignmentV2Base = z.infer<typeof staffCompanionAssignmentV2BaseSchema>;

export const staffCompanionAssignmentV2ReadSchema = staffCompanionAssignmentV2BaseSchema.extend({
  id: z.string().uuid(),
  trainee_staff_id: z.string().uuid(),
  created_at: z.string(),
  updated_at: z.string(),
});

export type StaffCompanionAssignmentV2Read = z.infer<typeof staffCompanionAssignmentV2ReadSchema>;

// ---------------------------------------------------------------------------
// Bulk PUT schema (same validation as backend)
//   - 0..14 items (7 曜日 × 3 part)
//   - 同 (weekday, part) 重複禁止
//   - 同曜日内: full と am/pm の併存禁止
// ---------------------------------------------------------------------------

export const staffCompanionAssignmentsBulkPutSchema = z.object({
  items: z
    .array(staffCompanionAssignmentV2BaseSchema)
    .max(14, '最大 14 件までです')
    .superRefine((items, ctx) => {
      const wkSeen: Record<number, Set<string>> = {};
      items.forEach((it, i) => {
        if (!wkSeen[it.weekday]) wkSeen[it.weekday] = new Set<string>();
        const set = wkSeen[it.weekday]!;

        // 同 (weekday, part) 重複
        if (set.has(it.part)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: [i, 'part'],
            message: '同曜日内 part 重複',
          });
        }

        // full と am/pm の併存禁止
        if (
          (it.part === 'full' && (set.has('am') || set.has('pm'))) ||
          ((it.part === 'am' || it.part === 'pm') && set.has('full'))
        ) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: [i, 'part'],
            message: 'full と am/pm の併存不可',
          });
        }

        set.add(it.part);
      });
    }),
});

export type StaffCompanionAssignmentsBulkPut = z.infer<
  typeof staffCompanionAssignmentsBulkPutSchema
>;
