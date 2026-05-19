/**
 * TanStack Query hooks for visit photos (Wave 4-D).
 *
 * Endpoints:
 *   GET    /api/v1/visits/{visit_id}/photos
 *   POST   /api/v1/visits/{visit_id}/photos                multipart/form-data
 *   DELETE /api/v1/visits/{visit_id}/photos/{photo_id}     admin/manager only
 *   GET    /api/v1/visits/{visit_id}/photos/{photo_id}/download
 *
 * The upload mutation does NOT go through `fetcher` because that helper
 * always sets `Content-Type: application/json`, which corrupts multipart
 * boundary detection. We post the FormData directly with native `fetch`
 * and reuse the auth header / 401 handling logic locally.
 */
'use client';

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { signOut, useSession } from 'next-auth/react';

import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';
import {
  ALLOWED_PHOTO_MIME,
  MAX_PHOTO_BYTES,
  type VisitPhotoRead,
} from '@/lib/schemas/visit-photo';

const VISIT_PHOTOS_KEY = ['visit', 'photos'] as const;

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

function resolveBaseUrl(): string {
  if (typeof window !== 'undefined') return '';
  return (
    process.env.BACKEND_API_BASE_URL ??
    process.env.NEXT_PUBLIC_BACKEND_API_BASE_URL ??
    'http://localhost:8000'
  );
}

/** GET /api/v1/visits/{visit_id}/photos */
export function useVisitPhotos(
  visitId: string | null | undefined,
): UseQueryResult<VisitPhotoRead[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<VisitPhotoRead[], Error>({
    queryKey: [...VISIT_PHOTOS_KEY, visitId ?? '__none__'],
    enabled: status === 'authenticated' && !!visitId,
    queryFn: () => {
      if (!visitId) throw new Error('visitId is required');
      return fetcher<VisitPhotoRead[]>(`/api/v1/visits/${visitId}/photos`, {
        accessToken,
        refreshToken,
      });
    },
  });
}

export interface UploadPhotoVariables {
  file: File;
  caption?: string;
}

/**
 * POST /api/v1/visits/{visit_id}/photos with multipart body. Returns the
 * created VisitPhotoRead so the caller can prepend it to the cached list
 * via `onSuccess` invalidation.
 */
export function useUploadPhoto(
  visitId: string,
): UseMutationResult<VisitPhotoRead, Error, UploadPhotoVariables> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken } = authPair(session);

  return useMutation<VisitPhotoRead, Error, UploadPhotoVariables>({
    mutationFn: async ({ file, caption }) => {
      // Pre-validate so we surface a clean error instead of pushing a
      // 415/413 round-trip for an obvious mismatch.
      if (!ALLOWED_PHOTO_MIME.includes(file.type as never)) {
        throw new ApiError(`Unsupported MIME type: ${file.type}`, 415, {
          detail: 'JPEG / PNG / WebP のみアップロード可能です',
        });
      }
      if (file.size > MAX_PHOTO_BYTES) {
        throw new ApiError(`File exceeds ${MAX_PHOTO_BYTES} bytes`, 413, {
          detail: '10MB を超える画像はアップロードできません',
        });
      }

      const form = new FormData();
      form.append('file', file);
      if (caption) form.append('caption', caption);

      // Native fetch — `fetcher` would clobber the multipart boundary
      // by forcing a JSON Content-Type header.
      const url = `${resolveBaseUrl()}/api/v1/visits/${visitId}/photos`;
      const headers: Record<string, string> = {};
      if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
      const res = await fetch(url, {
        method: 'POST',
        body: form,
        headers,
        cache: 'no-store',
      });
      if (res.status === 401) {
        // Mirror fetcher's behaviour for the dead-end case: bounce to /login.
        if (typeof window !== 'undefined') {
          void signOut({ callbackUrl: '/login' });
        }
      }
      const text = await res.text();
      const body = text ? safeJsonParse(text) : null;
      if (!res.ok) {
        throw new ApiError(`Upload failed: ${res.status} ${res.statusText}`, res.status, body);
      }
      return body as VisitPhotoRead;
    },
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: [...VISIT_PHOTOS_KEY, visitId],
      });
    },
  });
}

/** DELETE /api/v1/visits/{visit_id}/photos/{photo_id} (admin/manager). */
export function useDeletePhoto(visitId: string): UseMutationResult<void, Error, string> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<void, Error, string>({
    mutationFn: async (photoId) => {
      await fetcher<void>(`/api/v1/visits/${visitId}/photos/${photoId}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: [...VISIT_PHOTOS_KEY, visitId],
      });
    },
  });
}

function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
