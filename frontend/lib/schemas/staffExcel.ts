import { z } from 'zod';

export const staffExcelChangeSchema = z.object({
  field: z.string(),
  old_value: z.unknown().nullable(),
  new_value: z.unknown().nullable(),
});

export const staffExcelImportRowSchema = z.object({
  row_number: z.number().int(),
  staff_id: z.string().uuid().nullable(),
  staff_code: z.string().nullable(),
  operation: z.enum(['new', 'update', 'delete', 'error', 'noop']),
  changes: z.array(staffExcelChangeSchema).default([]),
  error_message: z.string().nullable(),
});

export const shiftExcelImportRowSchema = z.object({
  row_number: z.number().int(),
  staff_id: z.string().uuid().nullable(),
  staff_code: z.string().nullable(),
  weekday: z.number().int().nullable(),
  operation: z.enum(['new', 'update', 'delete', 'error', 'noop']),
  changes: z.array(staffExcelChangeSchema).default([]),
  error_message: z.string().nullable(),
});

export const staffExcelImportSummarySchema = z.object({
  staff_new: z.number().int().default(0),
  staff_update: z.number().int().default(0),
  staff_delete: z.number().int().default(0),
  staff_error: z.number().int().default(0),
  staff_noop: z.number().int().default(0),
  shift_new: z.number().int().default(0),
  shift_update: z.number().int().default(0),
  shift_delete: z.number().int().default(0),
  shift_error: z.number().int().default(0),
  shift_noop: z.number().int().default(0),
});

export const staffExcelImportResponseSchema = z.object({
  summary: staffExcelImportSummarySchema,
  staff_rows: z.array(staffExcelImportRowSchema),
  shift_rows: z.array(shiftExcelImportRowSchema),
  transaction_applied: z.boolean(),
});

export type StaffExcelImportResponse = z.infer<typeof staffExcelImportResponseSchema>;
export type StaffExcelImportRow = z.infer<typeof staffExcelImportRowSchema>;
export type ShiftExcelImportRow = z.infer<typeof shiftExcelImportRowSchema>;

// ---------------------------------------------------------------------------
// 完全置換 (バックアップ復元) 用 schema
// ---------------------------------------------------------------------------

export const staffExcelReplaceAllSummarySchema = z.object({
  staff_to_create: z.number().int().default(0),
  staff_to_update: z.number().int().default(0),
  staff_to_soft_delete: z.number().int().default(0),
  staff_error: z.number().int().default(0),
  shift_to_replace: z.number().int().default(0),
  shift_to_create: z.number().int().default(0),
  shift_error: z.number().int().default(0),
});

export const staffSoftDeletePreviewSchema = z.object({
  staff_id: z.string(),
  staff_code: z.string().nullable(),
  name: z.string(),
});

export const staffExcelReplaceAllResponseSchema = z.object({
  summary: staffExcelReplaceAllSummarySchema,
  staff_rows: z.array(staffExcelImportRowSchema),
  shift_rows: z.array(shiftExcelImportRowSchema),
  transaction_applied: z.boolean(),
  staff_to_soft_delete_preview: z.array(staffSoftDeletePreviewSchema).default([]),
});

export type StaffExcelReplaceAllResponse = z.infer<typeof staffExcelReplaceAllResponseSchema>;
export type StaffSoftDeletePreview = z.infer<typeof staffSoftDeletePreviewSchema>;
