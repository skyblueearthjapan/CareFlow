/**
 * Office (拠点) zod schemas — Phase 3-12.
 *
 * Mirrors backend `app/schemas/office.py` (OfficeRead/Create/Update). The
 * `allowed_cities` field is a UI-side helper: it captures the city UUIDs
 * picked in the Combobox and is wired to the office_cities M2M table.
 *
 * TODO(Phase 3-14): backend OfficeRead does not yet expose city ids; once
 * the API is extended, drop the optional fallback here.
 */
import { z } from 'zod';

const numericLike = z
  .union([z.number(), z.string()])
  .transform((v) => (typeof v === 'string' ? Number(v) : v))
  .pipe(z.number().finite());

export const OfficeReadSchema = z.object({
  id: z.string().uuid(),
  name: z.string().min(1),
  code: z.string().nullable().optional(),
  address: z.string().nullable().optional(),
  lat: numericLike.nullable().optional(),
  lng: numericLike.nullable().optional(),
  prefecture: z.string().nullable().optional(),
  note: z.string().nullable().optional(),
  allowed_cities: z.array(z.string().uuid()).default([]),
  created_at: z.string(),
  updated_at: z.string(),
  deleted_at: z.string().nullable().optional(),
});

export const OfficeCreateSchema = z.object({
  name: z.string().min(1, '拠点名は必須です'),
  code: z.string().optional(),
  address: z.string().nullable().optional(),
  lat: z.number().finite().nullable().optional(),
  lng: z.number().finite().nullable().optional(),
  prefecture: z.string().nullable().optional(),
  note: z.string().nullable().optional(),
  allowed_cities: z.array(z.string().uuid()).default([]),
});

export const OfficeUpdateSchema = OfficeCreateSchema.partial();

export type Office = z.infer<typeof OfficeReadSchema>;
export type OfficeCreate = z.infer<typeof OfficeCreateSchema>;
export type OfficeUpdate = z.infer<typeof OfficeUpdateSchema>;
