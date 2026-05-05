/**
 * Visit photo zod schemas — mirrors `backend/app/schemas/visit_photo.py`.
 *
 * Endpoints:
 *   POST   /api/v1/visits/{visit_id}/photos                 multipart upload → VisitPhotoRead
 *   GET    /api/v1/visits/{visit_id}/photos                 → VisitPhotoRead[]
 *   DELETE /api/v1/visits/{visit_id}/photos/{photo_id}      → 204
 *   GET    /api/v1/visits/{visit_id}/photos/{photo_id}/download → image binary
 */
import { z } from 'zod';

export const visitPhotoReadSchema = z.object({
  id: z.string().uuid(),
  visit_id: z.string().uuid(),
  mime_type: z.string(),
  size_bytes: z.number().int().nonnegative(),
  caption: z.string().nullable().optional(),
  uploaded_by: z.string().uuid().nullable().optional(),
  uploaded_at: z.string(),
  download_url: z.string(),
});

export type VisitPhotoRead = z.infer<typeof visitPhotoReadSchema>;

/** Allowed MIME types (Backend rejects anything else with 415). */
export const ALLOWED_PHOTO_MIME = [
  'image/jpeg',
  'image/png',
  'image/webp',
] as const;

export const MAX_PHOTO_BYTES = 10 * 1024 * 1024; // 10 MiB
