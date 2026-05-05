/**
 * Office (拠点) zod schemas — Phase 3-12.
 *
 * Mirrors backend `app/schemas/office.py` (OfficeRead/Create/Update). The
 * `allowed_cities` field captures the city UUIDs picked in the Combobox and
 * is wired to the office_cities M2M table.
 *
 * Done: migration 0002_add_office_prefecture_code added prefecture/code
 * columns and OfficeRead now exposes allowed_cities directly from the API.
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
  code: z.string().nullable().optional(),
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
