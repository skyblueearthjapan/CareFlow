/**
 * API wrappers for patient master Excel export / import.
 *
 * Export/template endpoints return xlsx blobs; import returns a typed JSON
 * preview/apply response validated with Zod.
 */
import { ApiError } from '@/lib/api-client';
import {
  patientExcelImportResponseSchema,
  patientExcelReplaceAllResponseSchema,
  type PatientExcelImportResponse,
  type PatientExcelReplaceAllResponse,
} from '@/lib/schemas/patientExcel';

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
 * Download the current patient master as an xlsx blob.
 * GET /api/v1/patients/import-export/export
 */
export async function downloadPatientsExcel(opts: AuthOpts): Promise<Blob> {
  return fetchBlob('/api/v1/patients/import-export/export', opts);
}

/**
 * Download an empty xlsx template (column headers only).
 * GET /api/v1/patients/import-export/template
 */
export async function downloadPatientsExcelTemplate(opts: AuthOpts): Promise<Blob> {
  return fetchBlob('/api/v1/patients/import-export/template', opts);
}

/**
 * Upload an xlsx file for dry-run preview or live commit.
 * POST /api/v1/patients/import-export/import?dry_run=...
 *
 * NOTE: ``dry_run`` is a *query parameter* on the backend (FastAPI ``Form()``
 * 注釈なし), so it MUST be sent in the URL, not as a multipart field.
 * Putting it in the FormData makes the backend silently fall back to its
 * default of ``True`` and the import will never commit.
 *
 * 手動回帰確認手順:
 *   1. 患者マスタ画面で Excel インポート → プレビュー → 反映ボタン
 *   2. 反映後にトーストが「患者マスタを更新しました」になることを確認
 *   3. DB / 一覧画面で変更が反映されていること (transaction_applied=true)
 *
 * @param dryRun  true → preview only (default); false → commit
 */
export async function importPatientsExcel(
  opts: AuthOpts & { file: File; dryRun: boolean },
): Promise<PatientExcelImportResponse> {
  const url =
    `${resolveBaseUrl()}/api/v1/patients/import-export/import` +
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
      `API ${res.status} ${res.statusText} (/api/v1/patients/import-export/import)`,
      res.status,
      raw,
    );
  }

  return patientExcelImportResponseSchema.parse(raw);
}

/**
 * Upload an xlsx file for the *complete replacement* (backup restore) flow.
 * POST /api/v1/patients/import-export/replace-all?dry_run=...
 *
 * admin のみ. 通常 import との違い:
 *   - Excel に無い既存患者は soft delete される
 *   - 空セルは NULL で上書きされる
 *   - 既存 PFV は全件物理削除 → Excel から再投入
 *   - atomic: error 1 件で全 rollback (422 を返す)
 */
export async function replaceAllPatientsExcel(
  opts: AuthOpts & { file: File; dryRun: boolean },
): Promise<PatientExcelReplaceAllResponse> {
  const url =
    `${resolveBaseUrl()}/api/v1/patients/import-export/replace-all` +
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
      `API ${res.status} ${res.statusText} (/api/v1/patients/import-export/replace-all)`,
      res.status,
      raw,
    );
  }

  return patientExcelReplaceAllResponseSchema.parse(raw);
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
