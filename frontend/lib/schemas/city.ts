/**
 * City (市区町村) zod schemas — Phase 3-13.
 * Mirrors backend `app/schemas/city.py` (CityRead/Create/Update).
 */
import { z } from 'zod';

export const CityReadSchema = z.object({
  id: z.string().uuid(),
  prefecture: z.string().min(1),
  name: z.string().min(1),
  jis_code: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
  deleted_at: z.string().nullable().optional(),
});

export const CityCreateSchema = z.object({
  prefecture: z.string().min(1),
  name: z.string().min(1),
  jis_code: z.string().nullable().optional(),
});

export const CityUpdateSchema = CityCreateSchema.partial();

export type City = z.infer<typeof CityReadSchema>;
export type CityCreate = z.infer<typeof CityCreateSchema>;
export type CityUpdate = z.infer<typeof CityUpdateSchema>;
