import { z } from 'zod';

export const patientExcelChangeSchema = z.object({
  field: z.string(),
  old_value: z.unknown().nullable(),
  new_value: z.unknown().nullable(),
});

export const patientExcelImportRowSchema = z.object({
  row_number: z.number().int(),
  patient_id: z.string().uuid().nullable(),
  patient_code: z.string().nullable(),
  operation: z.enum(['new', 'update', 'delete', 'error', 'noop']),
  changes: z.array(patientExcelChangeSchema).default([]),
  error_message: z.string().nullable(),
});

export const pfvExcelImportRowSchema = z.object({
  row_number: z.number().int(),
  patient_id: z.string().uuid().nullable(),
  patient_code: z.string().nullable(),
  weekday: z.number().int().nullable(),
  slot_index: z.number().int().nullable(),
  operation: z.enum(['new', 'update', 'delete', 'error', 'noop']),
  changes: z.array(patientExcelChangeSchema).default([]),
  error_message: z.string().nullable(),
});

export const patientExcelImportSummarySchema = z.object({
  patients_new: z.number().int().default(0),
  patients_update: z.number().int().default(0),
  patients_delete: z.number().int().default(0),
  patients_error: z.number().int().default(0),
  patients_noop: z.number().int().default(0),
  pfv_new: z.number().int().default(0),
  pfv_update: z.number().int().default(0),
  pfv_delete: z.number().int().default(0),
  pfv_error: z.number().int().default(0),
  pfv_noop: z.number().int().default(0),
});

export const patientExcelImportResponseSchema = z.object({
  summary: patientExcelImportSummarySchema,
  patient_rows: z.array(patientExcelImportRowSchema),
  pfv_rows: z.array(pfvExcelImportRowSchema),
  transaction_applied: z.boolean(),
});

export type PatientExcelImportResponse = z.infer<typeof patientExcelImportResponseSchema>;
export type PatientExcelImportRow = z.infer<typeof patientExcelImportRowSchema>;
export type PfvExcelImportRow = z.infer<typeof pfvExcelImportRowSchema>;

// ---------------------------------------------------------------------------
// 完全置換 (バックアップ復元) 用 schema
// ---------------------------------------------------------------------------

export const patientExcelReplaceAllSummarySchema = z.object({
  patients_to_create: z.number().int().default(0),
  patients_to_update: z.number().int().default(0),
  patients_to_soft_delete: z.number().int().default(0),
  patients_error: z.number().int().default(0),
  pfv_to_replace: z.number().int().default(0),
  pfv_to_create: z.number().int().default(0),
  pfv_error: z.number().int().default(0),
});

export const patientSoftDeletePreviewSchema = z.object({
  patient_id: z.string(),
  patient_code: z.string().nullable(),
  name: z.string(),
});

export const patientExcelReplaceAllResponseSchema = z.object({
  summary: patientExcelReplaceAllSummarySchema,
  patient_rows: z.array(patientExcelImportRowSchema),
  pfv_rows: z.array(pfvExcelImportRowSchema),
  transaction_applied: z.boolean(),
  patients_to_soft_delete_preview: z.array(patientSoftDeletePreviewSchema).default([]),
});

export type PatientExcelReplaceAllResponse = z.infer<typeof patientExcelReplaceAllResponseSchema>;
export type PatientSoftDeletePreview = z.infer<typeof patientSoftDeletePreviewSchema>;
