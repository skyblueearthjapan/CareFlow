/**
 * Staff override (休み・時間変更) zod schemas — mirrors backend
 * `staff_overrides` pydantic schemas (F5-Backend, in-flight Wave 2).
 *
 * Endpoint contract:
 *   GET    /api/v1/staff/{staff_id}/overrides?from=YYYY-MM-DD&to=YYYY-MM-DD
 *           -> OverrideRead[]
 *   POST   /api/v1/staff/{staff_id}/overrides            body: OverrideCreate -> OverrideRead
 *   PATCH  /api/v1/staff/{staff_id}/overrides/{id}       body: OverrideUpdate -> OverrideRead
 *   DELETE /api/v1/staff/{staff_id}/overrides/{id}       -> 204
 */
import { z } from 'zod';

const HHMM_REGEX = /^([01]\d|2[0-3]):[0-5]\d$/;
const ISO_DATE_REGEX = /^\d{4}-\d{2}-\d{2}$/;

const hhmmSchema = z
  .string()
  .regex(HHMM_REGEX, 'HH:MM 形式で入力してください');

export const OVERRIDE_TYPE_VALUES = [
  '休み',
  '時間変更',
  '午前休',
  '午後休',
] as const;

export const overrideTypeSchema = z.enum(OVERRIDE_TYPE_VALUES);
export type OverrideType = z.infer<typeof overrideTypeSchema>;

/**
 * "Time-shaped" types still need start/end (時間変更) or partials (午前休 /
 * 午後休). We let the form layer surface required/optional based on the type,
 * but enforce the cross-field rules here so any caller using the schema gets
 * the same guarantees.
 */
function applyTimeRules(
  data: {
    type: OverrideType;
    start_time?: string | null;
    end_time?: string | null;
  },
  ctx: z.RefinementCtx,
): void {
  if (data.type === '時間変更') {
    if (!data.start_time) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['start_time'],
        message: '時間変更は開始時刻が必須です',
      });
    }
    if (!data.end_time) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['end_time'],
        message: '時間変更は終了時刻が必須です',
      });
    }
    if (data.start_time && data.end_time && data.start_time >= data.end_time) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['end_time'],
        message: '終了時刻は開始時刻より後にしてください',
      });
    }
  }
}

export const overrideCreateSchema = z
  .object({
    date: z.string().regex(ISO_DATE_REGEX, '日付は YYYY-MM-DD 形式'),
    type: overrideTypeSchema,
    start_time: hhmmSchema.nullable().optional(),
    end_time: hhmmSchema.nullable().optional(),
    note: z.string().max(500).nullable().optional(),
  })
  .superRefine(applyTimeRules);

export type OverrideCreate = z.infer<typeof overrideCreateSchema>;

/** PATCH partial — every field is optional, but if `type` is supplied we
 * still enforce the time-rules consistently. */
export const overrideUpdateSchema = z
  .object({
    date: z.string().regex(ISO_DATE_REGEX, '日付は YYYY-MM-DD 形式').optional(),
    type: overrideTypeSchema.optional(),
    start_time: hhmmSchema.nullable().optional(),
    end_time: hhmmSchema.nullable().optional(),
    note: z.string().max(500).nullable().optional(),
  })
  .superRefine((val, ctx) => {
    if (val.type) {
      applyTimeRules(
        {
          type: val.type,
          start_time: val.start_time,
          end_time: val.end_time,
        },
        ctx,
      );
    }
  });

export type OverrideUpdate = z.infer<typeof overrideUpdateSchema>;

export const overrideReadSchema = z.object({
  id: z.string().uuid(),
  date: z.string().regex(ISO_DATE_REGEX),
  type: overrideTypeSchema,
  start_time: z.string().nullable().optional(),
  end_time: z.string().nullable().optional(),
  note: z.string().nullable().optional(),
});
export type OverrideRead = z.infer<typeof overrideReadSchema>;

/** Helper: which override types display the time fields. */
export function overrideTypeUsesTime(type: OverrideType): boolean {
  return type === '時間変更';
}
