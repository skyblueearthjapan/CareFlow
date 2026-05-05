/**
 * Geocoding zod schemas — CareFlow Wave 4-C.
 *
 * Mirrors backend `app/schemas/geocoding.py`. Used by the
 * `useGeocode()` mutation and the shared <AddressGeocodeField />
 * component to drive lat/lng auto-fill.
 */
import { z } from 'zod';

const numericLike = z
  .union([z.number(), z.string()])
  .transform((v) => (typeof v === 'string' ? Number(v) : v))
  .pipe(z.number().finite());

export const GeocodeRequestSchema = z.object({
  address: z.string().min(1, '住所を入力してください').max(500),
  force_refresh: z.boolean().optional(),
});
export type GeocodeRequest = z.infer<typeof GeocodeRequestSchema>;

export const GeocodeResponseSchema = z.object({
  lat: numericLike,
  lng: numericLike,
  formatted_address: z.string().nullable().optional(),
  place_id: z.string().nullable().optional(),
  cached: z.boolean(),
  address_hash: z.string(),
});
export type GeocodeResponse = z.infer<typeof GeocodeResponseSchema>;
