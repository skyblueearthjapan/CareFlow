/**
 * API wrappers for staff master Excel export / import.
 *
 * Export/template endpoints return xlsx blobs; import returns a typed JSON
 * preview/apply response validated with Zod.
 */
import { ApiError } from '@/lib/api-client';
import {
  staffExcelImportResponseSchema,
  type StaffExcelImportResponse,
} from '@/lib/schemas/staffExcel';

function resolveBaseUrl(): string {
  if (typeof window !== 'undefined') {
    return '';
  }
  return (
    process.env.BACKEND_API_BASE_URL ??
    process.env.NEXT_PUBLIC_BACKEND_API_BASE_URL ??
    'http://localhost:8000'
  );
}

interface AuthOpts {
  accessToken: string;
  refreshToken: string | null;
}

/** Fetch a blob (xlsx) from an authenticated endpoint. */
async function fetchBlob(path: string, opts: AuthOpts): Promise<Blob> {
  const url = `${resolveBaseUrl()}${path}`;
  const headers: Record<string, string> = {
    Authorization: `Bearer ${opts.accessToken}`,
  };
  const res = await fetch(url, { method: 'GET', headers, cache: 'no-store' });
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(`API ${res.status} ${res.statusText} (${path})`, res.status, text);
  }
  return res.blob();
}

/**
 * Download the current staff master as an xlsx blob.
 * GET /api/v1/staff/import-export/export
 */
export async function downloadStaffExcel(opts: AuthOpts): Promise<Blob> {
  return fetchBlob('/api/v1/staff/import-export/export', opts);
}

/**
 * Download an empty xlsx template (column headers only).
 * GET /api/v1/staff/import-export/template
 */
export async function downloadStaffExcelTemplate(opts: AuthOpts): Promise<Blob> {
  return fetchBlob('/api/v1/staff/import-export/template', opts);
}

/**
 * Upload an xlsx file for dry-run preview or live commit.
 * POST /api/v1/staff/import-export/import?dry_run=...
 *
 * NOTE: ``dry_run`` is a *query parameter* on the backend (FastAPI ``Form()``
 * 注釈なし), so it MUST be sent in the URL, not as a multipart field.
 *
 * @param dryRun  true → preview only (default); false → commit
 */
export async function importStaffExcel(
  opts: AuthOpts & { file: File; dryRun: boolean },
): Promise<StaffExcelImportResponse> {
  const url =
    `${resolveBaseUrl()}/api/v1/staff/import-export/import` +
    `?dry_run=${opts.dryRun ? 'true' : 'false'}`;
  const formData = new FormData();
  formData.append('file', opts.file);

  const res = await fetch(url, {
    method: 'POST',
    headers: { Authorization: `Bearer ${opts.accessToken}` },
    body: formData,
    cache: 'no-store',
  });

  const text = await res.text();
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    raw = text;
  }

  if (!res.ok) {
    throw new ApiError(
      `API ${res.status} ${res.statusText} (/api/v1/staff/import-export/import)`,
      res.status,
      raw,
    );
  }

  return staffExcelImportResponseSchema.parse(raw);
}

/** Trigger a browser file download from a Blob. */
export function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
